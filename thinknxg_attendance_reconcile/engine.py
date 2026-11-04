"""Plan and apply the reconciliation of one Absent attendance.

HRMS stays the attendance engine. This module only decides *whether* a correction
is justified and then drives standard HRMS code paths:

* shift resolution  -> hrms get_actual_start_end_datetime_of_shift / EmployeeCheckin.fetch_shift
* status, hours, late entry / early exit -> ShiftType.get_attendance (+ v16 thresholds)
* cancellation + check-in unlinking -> Attendance.cancel() / Attendance.on_cancel
* creation + linking -> mark_attendance_and_link_log (Attendance validations intact)

``plan`` never writes. ``reconcile(apply=True)`` runs inside a savepoint owned by
the caller (service.py), which rolls everything back on any exception.
"""

from dataclasses import dataclass
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

from thinknxg_attendance_reconcile import compat, provenance
from thinknxg_attendance_reconcile.constants import (
    ACTION_NO_CHANGE,
    ACTION_REPLACE,
    ACTION_REVIEW,
    ACTION_WAIT,
    AUDIT_LOG,
    F_CONFLICT_ATTENDANCE,
    F_RECONCILED_FROM,
    F_SKIP_APPROVED,
    F_SKIP_ORIGIN,
    SKIP_CONFLICT,
)

STRICT_LOG_TYPE = "Strictly based on Log Type in Employee Checkin"
CHECKIN_EXTRA_FIELDS = (F_SKIP_ORIGIN, F_CONFLICT_ATTENDANCE, F_SKIP_APPROVED)


class Outcome(Exception):
    """A decision that is not a correction. ``action`` is shown in preview/history."""

    action = ACTION_REVIEW

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class NeedsReview(Outcome):
    action = ACTION_REVIEW


class NoChange(Outcome):
    action = ACTION_NO_CHANGE


class Wait(Outcome):
    """Not yet decidable (shift running or biometric sync incomplete); retried later."""

    action = ACTION_WAIT


def review_unless(condition, reason):
    if not condition:
        raise NeedsReview(reason)


@dataclass
class Plan:
    attendance: object
    shift: object
    rows: list
    result: tuple
    skip_labels: dict


def _window(day):
    start = get_datetime(day) - timedelta(days=1)
    return start, start + timedelta(days=3)


def _checkins(employee, day):
    start, end = _window(day)
    meta = frappe.get_meta("Employee Checkin")
    fields = compat.checkin_fields() + [f for f in CHECKIN_EXTRA_FIELDS if meta.has_field(f)]
    return frappe.get_all(
        "Employee Checkin",
        filters=[["employee", "=", employee], ["time", ">=", start], ["time", "<", end]],
        fields=fields,
        order_by="time asc, name asc",
    )


def _shift_doc(name, cache):
    if name not in cache:
        cache[name] = frappe.get_doc("Shift Type", name)
    return cache[name]


def plan(attendance, settings, info=None, check_permissions=True, cache=None):
    """Decide what should happen to ``attendance`` without writing anything.

    ``info`` collects preview details as planning progresses, so callers can show
    them even when an Outcome is raised.
    """
    a = attendance
    info = info if info is not None else frappe._dict()
    cache = cache if cache is not None else {}
    day = getdate(a.attendance_date)
    info.update(
        attendance=a.name,
        employee=a.employee,
        employee_name=a.employee_name,
        company=a.company,
        attendance_date=day,
        existing_status=a.status,
        current_shift=a.shift,
        skip_status="",
        checkins=[],
        checkin_count=0,
    )

    if check_permissions:
        a.check_permission("read")
        review_unless(
            frappe.has_permission("Attendance", "cancel", doc=a),
            _("You do not have permission to cancel this Attendance."),
        )
    if a.docstatus != 1 or a.status != "Absent":
        raise NoChange(_("Attendance is no longer a submitted Absent record."))

    # Rule 3: only absences HRMS generated, or HR explicitly approved.
    info.origin = provenance.absence_origin(a)
    review_unless(
        info.origin,
        _(
            "Origin not established (manual, amended or pre-install record). Verify it and tick "
            "'Approve Absence Reconciliation' on the Attendance to allow correction."
        ),
    )

    if settings.freeze_through and day <= getdate(settings.freeze_through):
        raise NeedsReview(_("Date is on or before the freeze date {0}.").format(settings.freeze_through))

    # Rule 4: leave and payroll.
    review_unless(not a.leave_application and not a.leave_type, _("Attendance references a leave record."))
    leave = frappe.db.get_value(
        "Leave Application",
        {
            "employee": a.employee,
            "docstatus": ["<", 2],
            "status": ["in", ["Open", "Approved"]],
            "from_date": ["<=", day],
            "to_date": [">=", day],
        },
        ["name", "status"],
        as_dict=True,
    )
    review_unless(
        not leave,
        _("Leave Application {0} ({1}) covers this date.").format(
            leave and leave.name, leave and leave.status
        ),
    )
    request = frappe.db.exists(
        "Attendance Request",
        {"employee": a.employee, "docstatus": 1, "from_date": ["<=", day], "to_date": [">=", day]},
    )
    review_unless(not request, _("Attendance Request {0} covers this date.").format(request))
    payroll = compat.finalized_payroll_document(a.employee, day)
    review_unless(not payroll, _("Submitted {0} {1} covers this date.").format(*(payroll or ("", ""))))

    # Other attendance on the same date (multi-shift employees).
    others = frappe.get_all(
        "Attendance",
        filters={
            "employee": a.employee,
            "attendance_date": day,
            "docstatus": ["<", 2],
            "name": ["!=", a.name],
        },
        fields=["name", "shift"],
    )
    review_unless(
        all(o.shift for o in others),
        _("Another attendance without a shift exists on this date: {0}.").format(
            ", ".join(o.name for o in others)
        ),
    )
    taken_shifts = {o.shift for o in others}

    # Resolve each check-in with HRMS shift logic; group by shift start date (overnight safe).
    groups, unresolved_on_day = {}, False
    for row in _checkins(a.employee, day):
        if (
            row.attendance
            and row.attendance != a.name
            and frappe.db.get_value("Attendance", row.attendance, "docstatus") != 2
        ):
            continue  # owned by another active attendance
        resolved = compat.resolve_shift(a.employee, row.time)
        if not resolved:
            unresolved_on_day = unresolved_on_day or getdate(row.time) == day
            continue
        if getdate(resolved.shift_start) != day:
            continue
        row.stored_shift = row.shift
        row.update(resolved)
        groups.setdefault((row.shift, get_datetime(row.shift_start)), []).append(row)

    if not groups:
        if others:
            raise NeedsReview(
                _(
                    "Other attendance exists on this date ({0}) and no remaining check-ins "
                    "belong to this absence; confirm whether it still applies."
                ).format(", ".join(o.name for o in others))
            )
        if unresolved_on_day:
            raise NeedsReview(
                _("Check-ins exist on this date but no Shift Assignment or default shift covers them.")
            )
        raise NoChange(_("No check-ins resolve to a shift on this date."))

    eligible = {key: rows for key, rows in groups.items() if key[0] not in taken_shifts}
    if not eligible:
        raise NoChange(_("Check-ins on this date belong to another attendance's shift."))
    if len(eligible) > 1:
        matching = [key for key in eligible if key[0] == a.shift]
        review_unless(
            len(matching) == 1,
            _("Check-ins resolve to several shifts on this date ({0}); HR must decide.").format(
                ", ".join(sorted({key[0] for key in eligible}))
            ),
        )
        key = matching[0]
    else:
        (key,) = eligible
    rows = eligible[key]
    shift = _shift_doc(key[0], cache)
    info.update(proposed_shift=shift.name, checkins=[r.name for r in rows], checkin_count=len(rows))

    review_unless(
        cint(shift.enable_auto_attendance),
        _("Auto Attendance is disabled on Shift Type {0}.").format(shift.name),
    )
    review_unless(
        shift.process_attendance_after and shift.last_sync_of_checkin,
        _("Shift Type {0} has no Process Attendance After / Last Sync of Checkin.").format(shift.name),
    )
    review_unless(
        all(get_datetime(r.time) >= get_datetime(shift.process_attendance_after) for r in rows),
        _("Check-ins precede Process Attendance After on Shift Type {0}.").format(shift.name),
    )
    if shift.determine_check_in_and_check_out == STRICT_LOG_TYPE:
        missing = [r.name for r in rows if not r.log_type]
        review_unless(
            not missing,
            _("Log Type is required by the shift but missing on: {0}.").format(", ".join(missing)),
        )

    # Rule 2: skipped logs participate only with an established, correctable reason.
    labels, blocked = {}, []
    for r in rows:
        allowed, label = provenance.skip_decision(r, a.name, cint(settings.auto_reset_conflict_skips))
        if label:
            labels[r.name] = label
        if not allowed:
            blocked.append(f"{r.name} ({label})")
    info.skip_status = ", ".join(f"{n}: {label}" for n, label in labels.items())
    review_unless(not blocked, _("Skipped check-ins need HR review: {0}.").format(", ".join(blocked)))

    # Completion: shift over (+grace) and biometric sync confirmed past actual end.
    actual_end = get_datetime(rows[0].shift_actual_end)
    if now_datetime() < actual_end + timedelta(minutes=cint(settings.sync_grace_minutes)):
        raise Wait(_("Shift (plus grace period) has not finished."))
    if get_datetime(shift.last_sync_of_checkin) <= actual_end:
        raise Wait(
            _("Last Sync of Checkin ({0}) has not passed the shift's actual end ({1}).").format(
                shift.last_sync_of_checkin, actual_end
            )
        )
    review_unless(
        shift.should_mark_attendance(a.employee, day),
        _("Shift holiday policy does not mark attendance on this date."),
    )

    # Rule 1: HRMS calculates; a punch alone never makes an employee Present.
    result = compat.calculate(shift, rows, a.employee, day)
    status, hours, _late, _early, in_time, out_time = result
    info.update(proposed_status=status, working_hours=flt(hours, 2), in_time=in_time, out_time=out_time)
    if status == "Absent":
        raise NoChange(_("HRMS recalculates Absent ({0} h worked).").format(flt(hours, 2)))
    review_unless(status in ("Present", "Half Day"), _("Unexpected calculated status {0}.").format(status))
    review_unless(
        flt(hours) > 0 and in_time and out_time and get_datetime(out_time) > get_datetime(in_time),
        _("No valid IN/OUT pair (e.g. a single punch); HR must decide."),
    )

    info.action = ACTION_REPLACE
    info.reason = _("{0} absence; HRMS calculates {1} ({2} h) on {3}.").format(
        info.origin, status, flt(hours, 2), shift.name
    )
    return Plan(a, shift, rows, result, labels)


def _lock(name):
    """Row-lock employee, absence and the check-in window against concurrent workers."""
    employee, day = frappe.db.get_value("Attendance", name, ["employee", "attendance_date"])
    start, end = _window(day)
    for doctype, condition in (
        ("Employee", lambda t: t.name == employee),
        ("Attendance", lambda t: t.name == name),
        ("Employee Checkin", lambda t: (t.employee == employee) & (t.time >= start) & (t.time < end)),
    ):
        table = frappe.qb.DocType(doctype)
        frappe.qb.from_(table).select(table.name).where(condition(table)).for_update().run()


def _rejection_reason(log_names):
    for name in log_names:
        for comment in frappe.get_all(
            "Comment",
            filters={"reference_doctype": "Employee Checkin", "reference_name": name},
            fields=["content"],
            order_by="creation desc",
            limit=1,
        ):
            if provenance.is_skip_comment(comment.content):
                return frappe.utils.strip_html(comment.content)
    return _("HRMS did not create the attendance.")


def reconcile(name, settings, apply=False, check_permissions=True, run=None, info=None, cache=None):
    """Preview (apply=False) or correct one absence. Raises Outcome when not corrected."""
    info = info if info is not None else frappe._dict()
    if apply:
        _lock(name)
    a = frappe.get_doc("Attendance", name)
    p = plan(a, settings, info, check_permissions=check_permissions, cache=cache)
    if not apply:
        return info

    if check_permissions:
        review_unless(
            frappe.has_permission("Attendance", "create") and frappe.has_permission("Attendance", "submit"),
            _("You need create and submit permission on Attendance."),
        )
    day = getdate(a.attendance_date)
    before = {
        r.name: frappe._dict(
            time=r.time,
            log_type=r.log_type,
            previous_shift=r.stored_shift,
            previous_skip=cint(r.skip_auto_attendance),
            previous_attendance=r.attendance,
            skip_origin=p.skip_labels.get(r.name, ""),
        )
        for r in p.rows
    }

    # 1. Cancel through the document lifecycle; HRMS on_cancel unlinks its check-ins.
    a.flags.ignore_permissions = not check_permissions
    a.cancel()

    # 2. Re-fetch shift on each log exactly like HRMS bulk_fetch_shift, clearing only proven skips.
    logs = []
    for r in p.rows:
        log = frappe.get_doc("Employee Checkin", r.name)
        review_unless(not log.attendance, _("Check-in {0} is linked to another attendance.").format(log.name))
        if log.skip_auto_attendance:
            log.skip_auto_attendance = 0
            if log.meta.has_field(F_SKIP_APPROVED):
                log.set(F_SKIP_APPROVED, 0)
            if log.meta.has_field(F_SKIP_ORIGIN) and log.get(F_SKIP_ORIGIN) == SKIP_CONFLICT:
                log.set(F_SKIP_ORIGIN, None)
                log.set(F_CONFLICT_ATTENDANCE, None)
        log.fetch_shift()
        review_unless(
            log.shift == r.shift and get_datetime(log.shift_start) == get_datetime(r.shift_start),
            _("Shift resolution for {0} changed during reconciliation.").format(log.name),
        )
        log.flags.ignore_validate = True
        log.flags.ignore_permissions = not check_permissions
        log.save()
        logs.append(frappe._dict({f: log.get(f) for f in compat.checkin_fields()}))

    # 3. Create and link through HRMS (all Attendance validations run).
    new = compat.create_attendance(logs, p.result, day, p.shift.name)
    review_unless(
        new is not None,
        _("HRMS rejected the replacement: {0}").format(_rejection_reason([log.name for log in logs])),
    )
    review_unless(
        new.docstatus == 1 and new.status == p.result[0],
        _("HRMS did not create the expected submitted attendance."),
    )
    linked = set(frappe.get_all("Employee Checkin", filters={"attendance": new.name}, pluck="name"))
    review_unless(linked >= {log.name for log in logs}, _("HRMS did not link every check-in."))
    new.db_set(F_RECONCILED_FROM, a.name, update_modified=False)

    audit = write_audit(info, a, new, p, before, logs, run)
    a.add_comment(
        "Info", _("Replaced by {0} (thinkNXG Attendance Reconcile, audit {1}).").format(new.name, audit)
    )
    new.add_comment("Info", _("Replaces absence {0}: {1} (audit {2}).").format(a.name, info.reason, audit))
    info.update(result="Corrected", replacement_attendance=new.name, audit_log=audit)
    return info


def write_audit(info, previous, new, p, before, logs, run):
    status, hours, late, early, in_time, out_time = p.result
    doc = frappe.get_doc(
        {
            "doctype": AUDIT_LOG,
            "processing_status": "Corrected",
            "employee": previous.employee,
            "employee_name": previous.employee_name,
            "company": previous.company,
            "attendance_date": previous.attendance_date,
            "reconciliation": run.name if run else None,
            "run_type": run.run_type if run else "Manual",
            "reason": info.reason,
            "absence_origin": info.origin,
            "previous_attendance": previous.name,
            "original_status": previous.status,
            "original_shift": previous.shift,
            "corrected_attendance": new.name,
            "new_status": status,
            "corrected_shift": p.shift.name,
            "working_hours": hours,
            "late_entry": cint(late),
            "early_exit": cint(early),
            "in_time": in_time,
            "out_time": out_time,
            "executed_by": frappe.session.user,
            "executed_on": now_datetime(),
            "checkins": [
                {
                    "employee_checkin": log.name,
                    "time": before[log.name].time,
                    "log_type": before[log.name].log_type,
                    "previous_shift": before[log.name].previous_shift,
                    "new_shift": log.shift,
                    "previous_skip_auto_attendance": before[log.name].previous_skip,
                    "new_skip_auto_attendance": cint(log.skip_auto_attendance),
                    "skip_origin": before[log.name].skip_origin,
                    "previous_attendance": before[log.name].previous_attendance,
                    "new_attendance": new.name,
                }
                for log in logs
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def write_failure_audit(info, error, run):
    """Record a failed apply after its savepoint was rolled back."""
    if not info.get("attendance"):
        return None
    doc = frappe.get_doc(
        {
            "doctype": AUDIT_LOG,
            "processing_status": "Failed",
            "employee": info.employee,
            "employee_name": info.employee_name,
            "company": info.company,
            "attendance_date": info.attendance_date,
            "reconciliation": run.name if run else None,
            "run_type": run.run_type if run else "Manual",
            "reason": info.get("reason"),
            "absence_origin": info.get("origin"),
            "previous_attendance": info.attendance,
            "original_status": info.existing_status,
            "original_shift": info.current_shift,
            "corrected_shift": info.get("proposed_shift"),
            "new_status": info.get("proposed_status"),
            "executed_by": frappe.session.user,
            "executed_on": now_datetime(),
            "error_details": error,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
