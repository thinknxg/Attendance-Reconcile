import frappe
from uuid import uuid4
from frappe.utils import add_days, cint, getdate, nowdate

from attendance_reconciliation.engine import NeedsReview, reconcile


def authorize():
    frappe.only_for(("HR Manager", "System Manager"))


def dates(start, end):
    if not start or not end:
        frappe.throw("Select From Date and To Date")
    start, end = getdate(start), getdate(end)
    if start > end or (end - start).days > 92 or end > getdate(nowdate()):
        frappe.throw("Select a past/current range of at most 93 days")
    return start, end


@frappe.whitelist(methods=["POST"])
def preview(from_date, to_date, employee=None):
    authorize()
    return process(from_date, to_date, employee, apply=False)


@frappe.whitelist(methods=["POST"])
def run(from_date, to_date, employee=None):
    authorize()
    # Execute as the requesting user, preserving document and employee permissions.
    dates(from_date, to_date)
    job_id = "attendance-reconciliation-" + uuid4().hex
    frappe.enqueue("attendance_reconciliation.api.process", queue="long", job_id=job_id,
                         timeout=1800, enqueue_after_commit=True,
                         from_date=from_date, to_date=to_date, employee=employee,
                         apply=True, requested_by=frappe.session.user)
    return {"job_id": job_id}


def process(from_date, to_date, employee=None, apply=False, requested_by=None, after_name=None):
    previous_user = frappe.session.user
    if requested_by:
        frappe.set_user(requested_by)
    try:
        authorize()
        start, end = dates(from_date, to_date)
        settings = frappe.get_single("Attendance Reconciliation Settings")
        filters = {"docstatus": 1, "status": "Absent", "attendance_date": ["between", [start, end]]}
        if employee:
            filters["employee"] = employee
        if after_name:
            filters["name"] = [">", after_name]
        # Permission-aware list; bounded batches are explicit in the response.
        names = frappe.get_list("Attendance", filters=filters, pluck="name",
                                order_by="name", page_length=201)
        results = []
        lock_key = frappe.cache.make_key("attendance-reconciliation")
        with frappe.cache.lock(lock_key, timeout=1900, blocking_timeout=1):
            for name in names[:200]:
                frappe.db.savepoint("ar_item")
                try:
                    row = reconcile(name, settings, apply=apply)
                    if apply:
                        frappe.db.commit()
                except NeedsReview as error:
                    frappe.db.rollback(save_point="ar_item")
                    row = {"attendance": name, "action": "Review", "reason": str(error)}
                except Exception:
                    frappe.db.rollback(save_point="ar_item")
                    error = frappe.log_error(title="Attendance reconciliation failed")
                    row = {"attendance": name, "action": "Error", "error_log": error.name}
                results.append(row)
        report = {"rows": results, "truncated": len(names) > 200,
                  "next_cursor": names[199] if len(names) > 200 else None}
        if apply:
            run_log = frappe.get_doc({"doctype": "Attendance Reconciliation Run",
                "from_date": start, "to_date": end, "requested_by": frappe.session.user,
                "details": frappe.as_json(report)}).insert(ignore_permissions=True)
            frappe.db.commit()
            report["run_log"] = run_log.name
        return report
    finally:
        if requested_by:
            frappe.set_user(previous_user)


def scheduled():
    settings = frappe.get_single("Attendance Reconciliation Settings")
    if not cint(settings.enabled):
        return
    cursor = None
    while True:
        report = process(add_days(nowdate(), -cint(settings.lookback_days)), nowdate(),
                         apply=True, after_name=cursor)
        cursor = report["next_cursor"]
        if not cursor:
            break


def run_query(user=None):
    user = user or frappe.session.user
    if "System Manager" in frappe.get_roles(user):
        return ""
    return "`tabAttendance Reconciliation Run`.`owner` = " + frappe.db.escape(user)


def run_permission(doc, user=None, permission_type=None):
    user = user or frappe.session.user
    return user == doc.owner or "System Manager" in frappe.get_roles(user)
