"""One absence per transaction savepoint; never invoke the committing shift runner."""
from datetime import timedelta

import frappe
from frappe.utils import cint, getdate, get_datetime, now_datetime

from attendance_reconciliation.compat import calculate, create_attendance


class NeedsReview(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise NeedsReview(reason)


def plan(attendance, settings, check_permissions=True):
    """Resolve shifts in memory. Preview does not save check-ins or cancel records."""
    a = attendance
    if check_permissions:
        a.check_permission("read")
        a.check_permission("cancel")
    require(a.docstatus == 1 and a.status == "Absent", "No longer a submitted absence")
    require(a.custom_ar_auto_generated or a.custom_ar_approved,
            "Historical/manual absence requires HR approval")
    day = getdate(a.attendance_date)
    require(not settings.freeze_through or day > getdate(settings.freeze_through),
            "Date is frozen for reconciliation")
    require(not a.leave_application and not a.leave_type, "Attendance has leave references")
    require(not frappe.db.exists("Leave Application", {
        "employee": a.employee, "docstatus": 1, "status": "Approved",
        "from_date": ["<=", day], "to_date": [">=", day],
    }), "Approved leave overlaps the date")
    require(not frappe.db.exists("Salary Slip", {
        "employee": a.employee, "docstatus": 1,
        "start_date": ["<=", day], "end_date": [">=", day],
    }), "Submitted salary slip overlaps the date")
    # Conservative: do not infer which record to replace on a multi-shift day.
    require(not frappe.db.exists("Attendance", {
        "employee": a.employee, "attendance_date": day, "docstatus": ["<", 2],
        "name": ["!=", a.name],
    }), "Another active attendance exists on this date")
    rows = frappe.get_all("Employee Checkin", filters={
        "employee": a.employee,
        "time": ["between", [get_datetime(day) - timedelta(days=1),
                              get_datetime(day) + timedelta(days=2)]],
    }, pluck="name", order_by="time, name")
    logs = []
    for name in rows:
        log = frappe.get_doc("Employee Checkin", name)
        # Temporarily remove only the target link in memory to permit Fetch Shift.
        linked = log.attendance
        log.attendance = None
        log.fetch_shift()
        log.attendance = linked
        if log.offshift or not log.shift or not log.shift_start:
            continue
        if getdate(log.shift_start) != day:
            continue
        if check_permissions:
            log.check_permission("read")
            log.check_permission("write")
        require(not linked or linked == a.name, "Check-in is linked to another attendance")
        require(not log.skip_auto_attendance or log.custom_ar_reset_skip_approved,
                "Skipped check-in requires explicit HR approval: " + log.name)
        logs.append(log)
    require(bool(logs), "No check-ins resolve to a shift on this date")
    require(len({(x.shift, x.shift_start) for x in logs}) == 1,
            "Multiple shifts resolve to this date; HR review required")
    shift = frappe.get_doc("Shift Type", logs[0].shift)
    require(cint(shift.enable_auto_attendance), "Auto attendance is disabled for shift")
    require(shift.process_attendance_after and shift.last_sync_of_checkin,
            "Shift processing dates are missing")
    require(all(get_datetime(x.time) >= get_datetime(shift.process_attendance_after) for x in logs),
            "Check-ins precede Process Attendance After")
    end = get_datetime(logs[0].shift_actual_end)
    margin = timedelta(minutes=cint(settings.sync_grace_minutes))
    require(end + margin < now_datetime(), "Shift or sync grace period has not finished")
    require(end < get_datetime(shift.last_sync_of_checkin),
            "Last Sync of Checkin has not passed the shift's actual end")
    require(shift.should_mark_attendance(a.employee, day), "Shift holiday policy excludes this date")
    result = calculate(shift, logs, a.employee, day)
    status, hours, _, _, in_time, out_time = result
    require(status in ("Present", "Half Day"), "Check-ins still calculate as Absent or Skip")
    require(hours > 0 and in_time and out_time and get_datetime(out_time) > get_datetime(in_time),
            "A valid IN/OUT interval is required")
    return shift, logs, result


def reconcile(name, settings, apply=False, check_permissions=True):
    """Caller rolls back this entire operation on every failure, including audit failures."""
    if apply:
        employee = frappe.db.get_value("Attendance", name, "employee")
        Employee = frappe.qb.DocType("Employee")
        (frappe.qb.from_(Employee).select(Employee.name)
         .where(Employee.name == employee).for_update()).run()
        Attendance = frappe.qb.DocType("Attendance")
        (frappe.qb.from_(Attendance).select(Attendance.name)
         .where(Attendance.name == name).for_update()).run()
        # Hold existing punches stable through cancellation and relinking.
        Checkin = frappe.qb.DocType("Employee Checkin")
        (frappe.qb.from_(Checkin).select(Checkin.name)
         .where(Checkin.employee == employee).for_update()).run()
    a = frappe.get_doc("Attendance", name)
    shift, logs, result = plan(a, settings, check_permissions)
    output = dict(attendance=name, employee=a.employee, date=str(a.attendance_date),
                  shift=shift.name, status=result[0], working_hours=result[1],
                  checkins=[x.name for x in logs], action="Ready")
    if not apply:
        return output
    if check_permissions:
        frappe.has_permission("Attendance", "create", throw=True)
        frappe.has_permission("Attendance", "submit", throw=True)
    before = {x.name: dict(skip_auto_attendance=x.skip_auto_attendance,
                          attendance=x.attendance, shift=frappe.db.get_value("Employee Checkin", x.name, "shift"))
              for x in logs}
    a.cancel()
    for log in logs:
        log.attendance = None
        log.skip_auto_attendance = 0
        log.custom_ar_reset_skip_approved = 0
        log.save()
    new = create_attendance(logs, result, getdate(a.attendance_date), shift)
    require(new is not None and new.docstatus == 1 and new.status == result[0],
            "HRMS did not create the expected submitted attendance")
    require(all(frappe.db.get_value("Employee Checkin", x.name, "attendance") == new.name for x in logs),
            "HRMS did not link every check-in")
    audit = frappe.get_doc({
        "doctype": "Attendance Reconciliation Log", "employee": a.employee,
        "attendance_date": a.attendance_date, "previous_attendance": a.name,
        "replacement_attendance": new.name, "result": new.status,
        "details": frappe.as_json({"before": before, "after": output}),
    }).insert(ignore_permissions=True)
    new.add_comment("Info", "Reconciled absence {0}; audit {1}".format(a.name, audit.name))
    output.update(action="Corrected", replacement=new.name, audit=audit.name)
    return output
