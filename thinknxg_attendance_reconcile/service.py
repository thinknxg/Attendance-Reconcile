"""Background orchestration: preview, apply, scheduled runs.

Transaction rules
* Preview performs reads only; results are stored on the Reconciliation document.
* Apply handles one absence per savepoint. Success is committed immediately, so a
  later failure never undoes an earlier correction; a failure rolls back only its
  own absence (cancellation, check-in changes, replacement) and is audited.
* A site-wide Redis lock serializes apply phases; row locks in engine._lock and
  HRMS duplicate validation protect against standard auto-attendance racing us.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate
from redis.exceptions import LockError

from thinknxg_attendance_reconcile import compat, engine
from thinknxg_attendance_reconcile.constants import (
    ACTION_ERROR,
    ACTION_NO_CHANGE,
    ACTION_REPLACE,
    ACTION_REVIEW,
    ACTION_WAIT,
    RECONCILIATION,
    RECONCILIATION_ITEM,
    RESULT_CORRECTED,
    RESULT_PENDING,
    SETTINGS,
)

APPLY_LOCK = "thinknxg-attendance-reconcile-apply"
JOB_TIMEOUT = 3600

STATUS_FILTERS = {
    "Eligible": {ACTION_REPLACE},
    "HR Review": {ACTION_REVIEW},
    "Waiting": {ACTION_WAIT},
    "No Change": {ACTION_NO_CHANGE},
    "Errors": {ACTION_ERROR},
}


def get_settings():
    return frappe.get_cached_doc(SETTINGS)


def _commit():
    if not compat.in_test():
        frappe.db.commit()  # nosemgrep: per-absence durability is intentional


def _rollback_item(savepoint):
    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        # A deadlock/lock-timeout aborts the whole transaction; earlier items are committed.
        frappe.db.rollback()


def absence_names(filters, limit=None):
    """Permission-aware list of submitted Absent attendance matching run filters."""
    conditions = {
        "docstatus": 1,
        "status": "Absent",
        "attendance_date": ["between", [getdate(filters.from_date), getdate(filters.to_date)]],
    }
    for field in ("company", "employee", "department"):
        if filters.get(field):
            conditions[field] = filters.get(field)
    return frappe.get_list(
        "Attendance",
        filters=conditions,
        pluck="name",
        order_by="attendance_date asc, name asc",
        limit_page_length=limit or 0,
    )  # limit_page_length=0 means no limit on v15 and v16


def _item(info, action=None, reason=None):
    return {
        "attendance": info.get("attendance"),
        "employee": info.get("employee"),
        "employee_name": info.get("employee_name"),
        "attendance_date": info.get("attendance_date"),
        "existing_status": info.get("existing_status"),
        "proposed_status": info.get("proposed_status"),
        "current_shift": info.get("current_shift"),
        "proposed_shift": info.get("proposed_shift"),
        "checkin_count": cint(info.get("checkin_count")),
        "checkins": ", ".join(info.get("checkins") or []),
        "working_hours": flt(info.get("working_hours"), 2),
        "skip_status": info.get("skip_status"),
        "absence_origin": info.get("origin"),
        "proposed_action": action or info.get("action"),
        "review_required": cint((action or info.get("action")) == ACTION_REVIEW),
        "reason": reason or info.get("reason"),
        "selected": cint((action or info.get("action")) == ACTION_REPLACE),
        "result": RESULT_PENDING,
    }


def evaluate(name, settings, cache):
    """Preview one absence. Never writes."""
    info = frappe._dict(attendance=name)
    try:
        engine.reconcile(name, settings, apply=False, info=info, cache=cache)
        return _item(info)
    except engine.Outcome as outcome:
        return _item(info, outcome.action, outcome.reason)
    except Exception:
        error = frappe.log_error(title=_("thinkNXG Attendance Reconcile preview failed: {0}").format(name))
        row = _item(info, ACTION_ERROR, _("Unexpected error; see Error Log {0}.").format(error.name))
        row["error_log"] = error.name
        return row


def apply_one(name, settings, run, cache, check_permissions=True):
    """Correct one absence inside its own savepoint. Returns a result dict."""
    info = frappe._dict(attendance=name)
    savepoint = "tnxg_ar_item"
    frappe.db.savepoint(savepoint)
    try:
        engine.reconcile(
            name, settings, apply=True, check_permissions=check_permissions, run=run, info=info, cache=cache
        )
        _commit()
        return {
            "result": RESULT_CORRECTED,
            "reason": info.reason,
            "replacement_attendance": info.replacement_attendance,
            "audit_log": info.audit_log,
            "proposed_status": info.proposed_status,
            "working_hours": flt(info.working_hours, 2),
        }
    except engine.Outcome as outcome:
        _rollback_item(savepoint)
        return {"result": outcome.action, "reason": outcome.reason}
    except Exception:
        traceback = frappe.get_traceback()
        _rollback_item(savepoint)
        error = frappe.log_error(title=_("thinkNXG Attendance Reconcile failed: {0}").format(name))
        audit = engine.write_failure_audit(info, traceback, run)
        _commit()
        return {
            "result": ACTION_ERROR,
            "error_log": error.name,
            "audit_log": audit,
            "reason": _("Correction rolled back; see Error Log {0}.").format(error.name),
        }


# ------------------------------------------------------------------ jobs


def run_job(name, action, user=None):
    """RQ entry point. Executes as the requesting user so permissions still apply."""
    previous = frappe.session.user
    if user:
        frappe.set_user(user)
    try:
        doc = frappe.get_doc(RECONCILIATION, name)
        try:
            if action == "preview":
                preview(doc)
            else:
                apply(doc, selected_only=(action == "selected"))
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title=_("thinkNXG Attendance Reconcile run {0} failed").format(name))
            frappe.db.set_value(
                RECONCILIATION,
                name,
                {"status": "Failed", "error_message": frappe.get_traceback()},
                update_modified=False,
            )
            _commit()
            notify(name, "Failed")
    finally:
        frappe.set_user(previous)


def notify(name, status):
    user = frappe.db.get_value(RECONCILIATION, name, "owner")
    frappe.publish_realtime("tnxg_attendance_reconcile", {"name": name, "status": status}, user=user)


def preview(doc):
    settings = get_settings()
    cap = cint(settings.max_records_per_run) or 1000
    doc.db_set({"status": "In Progress", "error_message": None}, update_modified=False)
    _commit()
    names = absence_names(doc, cap + 1)
    wanted = STATUS_FILTERS.get(doc.status_filter or "All")
    cache, rows = {}, []
    for name in names[:cap]:
        row = evaluate(name, settings, cache)
        if doc.shift_type and doc.shift_type not in (row["current_shift"], row["proposed_shift"]):
            continue
        if wanted and row["proposed_action"] not in wanted:
            continue
        rows.append(row)

    doc.reload()
    doc.set("items", rows)
    doc.truncated = cint(len(names) > cap)
    doc.previewed_on = now_datetime()
    doc.status = "Previewed"
    doc.update_counts()
    doc.save()
    _commit()
    notify(doc.name, doc.status)


def apply(doc, selected_only=True):
    settings = get_settings()
    targets = [
        i
        for i in doc.items
        if i.proposed_action == ACTION_REPLACE
        and i.result in (RESULT_PENDING, "", None)
        and (i.selected or not selected_only)
    ]
    if not targets:
        frappe.throw(_("No eligible rows to reconcile. Run Preview again."))
    doc.db_set({"status": "In Progress", "error_message": None}, update_modified=False)
    _commit()
    cache = {}
    try:
        with compat.run_lock(APPLY_LOCK, timeout=JOB_TIMEOUT + 300, blocking_timeout=600):
            for item in targets:
                outcome = apply_one(item.attendance, settings, doc, cache)
                frappe.db.set_value(RECONCILIATION_ITEM, item.name, outcome, update_modified=False)
                _commit()
    except LockError:
        frappe.throw(_("Another reconciliation run is applying corrections. Try again when it finishes."))
    doc.reload()
    doc.reconciled_on = now_datetime()
    doc.status = "Reconciled"
    doc.update_counts()
    doc.save()
    _commit()
    notify(doc.name, doc.status)


def scheduled():
    """hourly_long: correct eligible absences in the lookback window (disabled by default).

    A run document is kept only when something was corrected or failed, so hourly
    re-evaluation of review-only records does not create clutter.
    """
    settings = get_settings()
    if not cint(settings.enabled):
        return
    if frappe.db.exists(
        RECONCILIATION,
        {"run_type": "Scheduled", "status": "In Progress", "modified": [">", add_days(now_datetime(), -1)]},
    ):
        return
    run = frappe.new_doc(RECONCILIATION)
    run.update(
        {
            "run_type": "Scheduled",
            "from_date": add_days(nowdate(), -cint(settings.lookback_days)),
            "to_date": nowdate(),
            "status_filter": "All",
            "status": "In Progress",
        }
    )
    cache, rows = {}, []
    try:
        with compat.run_lock(APPLY_LOCK, timeout=JOB_TIMEOUT + 300, blocking_timeout=60):
            for name in absence_names(run):
                row = evaluate(name, settings, cache)
                if row["proposed_action"] == ACTION_REPLACE:
                    if run.is_new():
                        run.insert(ignore_permissions=True)
                        _commit()
                    row.update(apply_one(name, settings, run, cache, check_permissions=False))
                if row["proposed_action"] in (ACTION_REPLACE, ACTION_ERROR):
                    rows.append(row)
    except LockError:
        return  # another apply phase is running; the next hourly run retries
    if run.is_new() and not rows:
        return
    if run.is_new():
        run.insert(ignore_permissions=True)
    run.reload()
    run.set("items", rows)
    run.status = "Reconciled"
    run.previewed_on = run.reconciled_on = now_datetime()
    run.update_counts()
    run.save(ignore_permissions=True)
    _commit()
