"""Use HRMS's own rules while accommodating older v16 patch releases."""
import inspect


def calculate(shift, logs, employee, day):
    parameters = inspect.signature(shift.get_attendance).parameters
    if "working_hours_threshold_for_absent" not in parameters:
        return shift.get_attendance(logs)
    absent = float(shift.working_hours_threshold_for_absent or 0)
    half = float(shift.working_hours_threshold_for_half_day or 0)
    if hasattr(shift, "is_half_holiday") and shift.is_half_holiday(employee, day):
        absent, half = absent / 2, half / 2
    return shift.get_attendance(logs, absent, half)


def create_attendance(logs, result, day, shift):
    from hrms.hr.doctype.employee_checkin.employee_checkin import mark_attendance_and_link_log
    status, hours, late, early, in_time, out_time = result
    kwargs = dict(logs=logs, attendance_status=status, attendance_date=day,
                  working_hours=hours, late_entry=late, early_exit=early,
                  in_time=in_time, out_time=out_time, shift=shift.name)
    if "overtime_type" in inspect.signature(mark_attendance_and_link_log).parameters:
        kwargs["overtime_type"] = logs[0].get("overtime_type")
    return mark_attendance_and_link_log(**kwargs)
