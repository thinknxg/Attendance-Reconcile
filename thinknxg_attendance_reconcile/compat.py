"""Single compatibility layer for Frappe/HRMS v15 and v16.

Everything that differs between the two HRMS majors is detected here by
signature or metadata, never by scattered version checks elsewhere. Verified
against HRMS version-15 (15.64.x) and version-16 (16.53.x) sources:

* ShiftType.get_attendance(logs) in v15; get_attendance(logs, absent, half) in v16,
  where ShiftType._process halves both thresholds on half holidays.
* mark_attendance_and_link_log gained ``overtime_type`` in v16.
* Shift resolution (get_actual_start_end_datetime_of_shift) is identical; v16 adds
  ``overtime_type`` to the returned details.
* Overtime Slip (submittable, employee/start_date/end_date) exists only in v16.
* ``frappe.in_test`` (v16) replaces ``frappe.flags.in_test`` (v15).
"""

import inspect

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

SUPPORTED_MAJORS = (15, 16)


def major(version):
    return int(str(version).split(".")[0])


def validate_versions():
    """Fail with a useful message on unsupported or mismatched installations."""
    import hrms

    frappe_major, hrms_major = major(frappe.__version__), major(hrms.__version__)
    if frappe_major not in SUPPORTED_MAJORS or hrms_major not in SUPPORTED_MAJORS:
        frappe.throw(
            _("thinkNXG Attendance Reconcile supports thinkNXG Framework and thinkNXG HR v15 and v16 only.")
        )
    if frappe_major != hrms_major:
        frappe.throw(_("thinkNXG Framework and thinkNXG HR must be on the same major version."))
    return frappe_major


def in_test():
    return bool(getattr(frappe, "in_test", False) or frappe.flags.in_test)


def _accepts(func, parameter):
    try:
        return parameter in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def resolve_shift(employee, timestamp):
    """Exactly what EmployeeCheckin.fetch_shift resolves, without mutating a document."""
    from hrms.hr.doctype.shift_assignment.shift_assignment import get_actual_start_end_datetime_of_shift

    timings = get_actual_start_end_datetime_of_shift(employee, get_datetime(timestamp), True)
    if not timings:
        return None
    return frappe._dict(
        shift=timings.shift_type.name,
        shift_start=timings.start_datetime,
        shift_end=timings.end_datetime,
        shift_actual_start=timings.actual_start,
        shift_actual_end=timings.actual_end,
        overtime_type=timings.get("overtime_type") or None,
    )


def calculate(shift, logs, employee, day):
    """Run HRMS's own ShiftType.get_attendance with the thresholds ShiftType._process uses.

    ``logs`` must be frappe._dict rows: HRMS calculate_working_hours subscripts them
    (``d["log_type"]``), which Document objects do not support.
    """
    if not _accepts(shift.get_attendance, "working_hours_threshold_for_absent"):
        return shift.get_attendance(logs)  # v15

    absent = flt(shift.working_hours_threshold_for_absent)
    half = flt(shift.working_hours_threshold_for_half_day)
    if hasattr(shift, "is_half_holiday") and shift.is_half_holiday(employee, day):
        absent, half = absent / 2, half / 2
    return shift.get_attendance(logs, absent, half)  # v16


def create_attendance(logs, result, day, shift_name):
    """Create and link attendance through HRMS. Returns None when HRMS rejects it."""
    from hrms.hr.doctype.employee_checkin.employee_checkin import mark_attendance_and_link_log

    status, hours, late, early, in_time, out_time = result
    kwargs = dict(
        logs=logs,
        attendance_status=status,
        attendance_date=day,
        working_hours=hours,
        late_entry=late,
        early_exit=early,
        in_time=in_time,
        out_time=out_time,
        shift=shift_name,
    )
    if _accepts(mark_attendance_and_link_log, "overtime_type"):
        kwargs["overtime_type"] = logs[0].get("overtime_type")
    return mark_attendance_and_link_log(**kwargs)


def checkin_fields():
    """Employee Checkin columns needed for HRMS calculation on this version."""
    fields = [
        "name",
        "employee",
        "log_type",
        "time",
        "shift",
        "shift_start",
        "shift_end",
        "shift_actual_start",
        "shift_actual_end",
        "device_id",
        "attendance",
        "skip_auto_attendance",
        "offshift",
    ]
    if frappe.get_meta("Employee Checkin").has_field("overtime_type"):
        fields.append("overtime_type")
    return fields


# Finalized documents that make an attendance date unsafe to change automatically.
# (doctype, employee field, from field, to field); skipped when the DocType is absent.
FINALIZED_PAYROLL_DOCTYPES = (
    ("Salary Slip", "employee", "start_date", "end_date"),
    ("Overtime Slip", "employee", "start_date", "end_date"),  # v16 only
)


def finalized_payroll_document(employee, day):
    for doctype, emp_field, start, end in FINALIZED_PAYROLL_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            continue
        name = frappe.db.exists(
            doctype, {emp_field: employee, "docstatus": 1, start: ["<=", day], end: [">=", day]}
        )
        if name:
            return doctype, name
    return None


def run_lock(key, timeout, blocking_timeout):
    """Site-scoped Redis lock (redis-py Lock via frappe.cache on both majors)."""
    return frappe.cache.lock(frappe.cache.make_key(key), timeout=timeout, blocking_timeout=blocking_timeout)
