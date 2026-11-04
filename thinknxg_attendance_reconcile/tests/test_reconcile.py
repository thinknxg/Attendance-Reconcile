"""Integration tests. Run on a disposable site for each supported major:

    bench --site test-site set-config allow_tests true
    bench --site test-site run-tests --app thinknxg_attendance_reconcile

Most scenarios create their Absent records and skipped check-ins by running the real
HRMS auto-attendance engine (ShiftType.process_auto_attendance), so the markers and
conflicts under test are the ones HRMS itself produces.
"""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.utils import add_days, get_datetime, getdate, now_datetime, nowdate

from thinknxg_attendance_reconcile import engine, service
from thinknxg_attendance_reconcile.constants import (
    ACTION_NO_CHANGE,
    ACTION_REPLACE,
    ACTION_REVIEW,
    ACTION_WAIT,
    AUDIT_LOG,
    F_APPROVED,
    F_AUTO_GENERATED,
    F_CONFLICT_ATTENDANCE,
    F_RECONCILED_FROM,
    F_SKIP_APPROVED,
    F_SKIP_ORIGIN,
    RECONCILIATION,
    SETTINGS,
    SKIP_CONFLICT,
    SKIP_INTENTIONAL,
)

try:  # v16
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # v15
    from frappe.tests.utils import FrappeTestCase as TestCase


SETTINGS_DICT = frappe._dict(
    sync_grace_minutes=0, freeze_through=None, auto_reset_conflict_skips=1, max_records_per_run=1000
)


class World:
    """One employee, one holiday list and helpers to create shifts and punches."""

    def __init__(self, day=None):
        self.suffix = uuid4().hex[:8]
        self.day = getdate(day or add_days(nowdate(), -3))
        self.company = frappe.get_all("Company", pluck="name", limit=1)[0]
        self.holiday_list = (
            frappe.get_doc(
                {
                    "doctype": "Holiday List",
                    "holiday_list_name": "TNXG-AR-" + self.suffix,
                    "from_date": add_days(self.day, -40),
                    "to_date": add_days(self.day, 40),
                }
            )
            .insert()
            .name
        )
        self.employee = self.make_employee()

    def make_employee(self, **kw):
        name = (
            frappe.get_doc(
                {
                    "doctype": "Employee",
                    "first_name": "TNXG AR " + uuid4().hex[:6],
                    "gender": "Male",
                    "date_of_birth": "1990-01-01",
                    "date_of_joining": self.day,
                    "company": self.company,
                    "status": "Active",
                    "holiday_list": self.holiday_list,
                    "naming_series": "HR-EMP-",
                    **kw,
                }
            )
            .insert()
            .name
        )
        if frappe.db.exists("DocType", "Holiday List Assignment"):  # v16 resolves holidays through it
            hla = frappe.get_doc(
                {
                    "doctype": "Holiday List Assignment",
                    "applicable_for": "Employee",
                    "assigned_to": name,
                    "holiday_list": self.holiday_list,
                    "employee_company": self.company,
                    "from_date": add_days(self.day, -40),
                }
            ).insert()
            hla.submit()
        return name

    def shift(self, start, end, **kw):
        values = {
            "doctype": "Shift Type",
            "name": f"TNXG-AR-{self.suffix}-{start[:2]}{end[:2]}-{uuid4().hex[:4]}",
            "start_time": start,
            "end_time": end,
            "holiday_list": self.holiday_list,
            "enable_auto_attendance": 1,
            "process_attendance_after": self.day,
            "last_sync_of_checkin": now_datetime(),
            "begin_check_in_before_shift_start_time": 60,
            "allow_check_out_after_shift_end_time": 60,
            "determine_check_in_and_check_out": "Alternating entries as IN and OUT during the same shift",
            "working_hours_calculation_based_on": "First Check-in and Last Check-out",
            "working_hours_threshold_for_absent": 4,
            "working_hours_threshold_for_half_day": 7,
        }
        values.update(kw)
        return frappe.get_doc(values).insert()

    def assign(self, shift, employee=None, start=None, end=None):
        doc = frappe.get_doc(
            {
                "doctype": "Shift Assignment",
                "employee": employee or self.employee,
                "shift_type": shift.name,
                "company": self.company,
                "start_date": start or self.day,
                "end_date": end or self.day,
            }
        ).insert()
        doc.submit()
        return doc

    def punch(self, hours, log_type=None, employee=None, day=None, **kw):
        return frappe.get_doc(
            {
                "doctype": "Employee Checkin",
                "employee": employee or self.employee,
                "time": get_datetime(day or self.day) + timedelta(hours=hours),
                "log_type": log_type,
                **kw,
            }
        ).insert()

    def absences(self, employee=None, day=None):
        return frappe.get_all(
            "Attendance",
            filters={
                "employee": employee or self.employee,
                "attendance_date": day or self.day,
                "docstatus": 1,
                "status": "Absent",
            },
            pluck="name",
        )

    def active(self, employee=None, day=None):
        return frappe.get_all(
            "Attendance",
            filters={
                "employee": employee or self.employee,
                "attendance_date": day or self.day,
                "docstatus": 1,
            },
            fields=["name", "status", "shift", "working_hours"],
        )

    def manual_absent(self, shift=None, employee=None):
        doc = frappe.get_doc(
            {
                "doctype": "Attendance",
                "employee": employee or self.employee,
                "attendance_date": self.day,
                "status": "Absent",
                "shift": shift.name if shift else None,
            }
        ).insert()
        doc.submit()
        return doc.name


def run_hrms(shift):
    shift = frappe.get_doc("Shift Type", shift.name)
    shift.process_auto_attendance()


def preview(name, settings=SETTINGS_DICT):
    info = frappe._dict()
    try:
        engine.reconcile(name, settings, apply=False, info=info)
        return ACTION_REPLACE, info
    except engine.Outcome as outcome:
        info.reason = outcome.reason
        return outcome.action, info


def apply(name, settings=SETTINGS_DICT):
    return engine.reconcile(name, settings, apply=True)


class TestThinkNXGAttendanceReconcile(TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        frappe.local.cache.pop("tnxg_ar_languages", None)

    # -- helpers -------------------------------------------------------------
    def late_sync_world(self, start="09:00:00", end="17:00:00", punches=((9, "IN"), (17, "OUT")), **shift_kw):
        """HRMS marks Absent before punches arrive; late punches then collide and are skipped."""
        w = World()
        w.s = w.shift(start, end, **shift_kw)
        w.assign(w.s)
        run_hrms(w.s)
        (w.absent,) = w.absences()
        w.logs = [w.punch(h, t) for h, t in punches]
        run_hrms(w.s)
        return w

    def assertReplaced(self, w, result, status):
        self.assertEqual(result.result, "Corrected")
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, "docstatus"), 2)
        new = frappe.get_doc("Attendance", result.replacement_attendance)
        self.assertEqual((new.docstatus, new.status), (1, status))
        self.assertEqual(new.get(F_RECONCILED_FROM), w.absent)
        for log in w.logs:
            row = frappe.db.get_value(
                "Employee Checkin", log.name, ["attendance", "skip_auto_attendance"], as_dict=True
            )
            self.assertEqual((row.attendance, row.skip_auto_attendance), (new.name, 0))
        self.assertEqual(len(w.active()), 1)
        return new

    # -- HRMS-generated scenarios -------------------------------------------
    def test_late_biometric_conflict_skip_is_corrected(self):
        """Absent exists, late punches were skipped by HRMS due to the conflict -> Present."""
        w = self.late_sync_world()
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, F_AUTO_GENERATED), 1)
        for log in w.logs:
            row = frappe.db.get_value(
                "Employee Checkin",
                log.name,
                ["skip_auto_attendance", F_SKIP_ORIGIN, F_CONFLICT_ATTENDANCE],
                as_dict=True,
            )
            self.assertEqual(
                (row.skip_auto_attendance, row[F_SKIP_ORIGIN], row[F_CONFLICT_ATTENDANCE]),
                (1, SKIP_CONFLICT, w.absent),
            )

        action, info = preview(w.absent)
        self.assertEqual((action, info.proposed_status, info.working_hours), (ACTION_REPLACE, "Present", 8))
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, "docstatus"), 1)  # preview wrote nothing
        self.assertEqual(frappe.db.get_value("Employee Checkin", w.logs[0].name, "skip_auto_attendance"), 1)

        new = self.assertReplaced(w, apply(w.absent), "Present")
        audit = frappe.get_doc(AUDIT_LOG, {"previous_attendance": w.absent})
        self.assertEqual(
            (audit.processing_status, audit.corrected_attendance, audit.new_status),
            ("Corrected", new.name, "Present"),
        )
        self.assertEqual({c.skip_origin for c in audit.checkins}, {SKIP_CONFLICT})
        self.assertEqual({c.previous_skip_auto_attendance for c in audit.checkins}, {1})

    def test_repeat_is_idempotent(self):
        w = self.late_sync_world()
        apply(w.absent)
        action, _info = preview(w.absent)
        self.assertEqual(action, ACTION_NO_CHANGE)
        with self.assertRaises(engine.NoChange):
            apply(w.absent)
        self.assertEqual(len(w.active()), 1)
        self.assertEqual(frappe.db.count(AUDIT_LOG, {"previous_attendance": w.absent}), 1)

    def test_overnight_shift_maps_to_shift_start_date(self):
        w = self.late_sync_world("22:00:00", "06:00:00", punches=((22, "IN"), (30, "OUT")))
        action, info = preview(w.absent)
        self.assertEqual(
            (action, info.proposed_status, getdate(info.attendance_date)), (ACTION_REPLACE, "Present", w.day)
        )
        self.assertReplaced(w, apply(w.absent), "Present")
        self.assertFalse(w.active(day=add_days(w.day, 1)))

    def test_half_day_threshold(self):
        w = self.late_sync_world(punches=((9, "IN"), (14, "OUT")))
        self.assertReplaced(w, apply(w.absent), "Half Day")

    def test_still_absent_under_threshold(self):
        w = self.late_sync_world(punches=((9, "IN"), (11, "OUT")))
        action, info = preview(w.absent)
        self.assertEqual((action, info.proposed_status), (ACTION_NO_CHANGE, "Absent"))

    def test_single_in_punch_needs_review(self):
        w = self.late_sync_world(
            punches=((9, "IN"),), working_hours_threshold_for_absent=0, working_hours_threshold_for_half_day=0
        )
        action, info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)
        self.assertIn("IN/OUT", info.reason)

    def test_multiple_punches_every_valid_pair(self):
        w = self.late_sync_world(
            punches=((9, "IN"), (12, "OUT"), (13, "IN"), (18, "OUT")),
            working_hours_calculation_based_on="Every Valid Check-in and Check-out",
        )
        _action, info = preview(w.absent)
        self.assertEqual((info.proposed_status, info.working_hours, info.checkin_count), ("Present", 8, 4))
        self.assertReplaced(w, apply(w.absent), "Present")

    def test_strict_log_type_first_last(self):
        """Regression: HRMS subscripts logs; Document objects used to crash here."""
        w = self.late_sync_world(
            determine_check_in_and_check_out="Strictly based on Log Type in Employee Checkin"
        )
        _action, info = preview(w.absent)
        self.assertEqual(info.proposed_status, "Present")
        self.assertReplaced(w, apply(w.absent), "Present")

    def test_shift_assigned_later_after_fetch_shift(self):
        """Default shift D marked a threshold absence; HR assigns overlapping shift E and fetches shifts."""
        w = World()
        d = w.shift("09:00:00", "17:00:00")
        e = w.shift("10:00:00", "19:00:00")
        frappe.db.set_value("Employee", w.employee, "default_shift", d.name)
        w.logs = [w.punch(10, "IN"), w.punch(19, "OUT")]  # 10:00 -> D, 19:00 -> off-shift for D
        self.assertEqual(frappe.db.get_value("Employee Checkin", w.logs[1].name, "offshift"), 1)
        run_hrms(d)
        (w.absent,) = w.absences()
        self.assertEqual(frappe.db.get_value("Employee Checkin", w.logs[0].name, "attendance"), w.absent)

        w.assign(e)
        from hrms.hr.doctype.employee_checkin.employee_checkin import bulk_fetch_shift

        bulk_fetch_shift([log.name for log in w.logs])
        self.assertEqual(frappe.db.get_value("Employee Checkin", w.logs[1].name, "shift"), e.name)
        run_hrms(e)  # 19:00 alone collides with the D absence -> HRMS skips it

        action, info = preview(w.absent)
        self.assertEqual(
            (action, info.current_shift, info.proposed_shift, info.proposed_status),
            (ACTION_REPLACE, d.name, e.name, "Present"),
        )
        new = self.assertReplaced(w, apply(w.absent), "Present")
        self.assertEqual((new.shift, new.working_hours), (e.name, 9))

    # -- Skip handling ---------------------------------------------------------
    def test_intentional_skip_is_preserved(self):
        w = World()
        w.s = w.shift("09:00:00", "17:00:00")
        w.assign(w.s)
        run_hrms(w.s)
        (w.absent,) = w.absences()
        w.logs = [w.punch(9, "IN", skip_auto_attendance=1), w.punch(17, "OUT", skip_auto_attendance=1)]

        action, info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)
        self.assertIn("Skipped", info.reason)
        with self.assertRaises(engine.NeedsReview):
            apply(w.absent)
        self.assertEqual(frappe.db.get_value("Employee Checkin", w.logs[0].name, "skip_auto_attendance"), 1)
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, "docstatus"), 1)

        for log in w.logs:  # HR explicitly approves
            log.reload()
            log.set(F_SKIP_APPROVED, 1)
            log.save()
        self.assertReplaced(w, apply(w.absent), "Present")

    def test_conflict_skip_marked_intentional_is_preserved(self):
        w = self.late_sync_world()
        frappe.db.set_value("Employee Checkin", w.logs[0].name, F_SKIP_ORIGIN, SKIP_INTENTIONAL)
        action, _info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)

    def test_conflict_skip_reset_can_be_disabled(self):
        w = self.late_sync_world()
        action, _info = preview(w.absent, frappe._dict(SETTINGS_DICT, auto_reset_conflict_skips=0))
        self.assertEqual(action, ACTION_REVIEW)

    def test_historical_conflict_skip_detected_from_comment(self):
        """Pre-install skips have no stamp; the HRMS comment still proves the conflict."""
        w = self.late_sync_world()
        for log in w.logs:
            frappe.db.set_value(
                "Employee Checkin", log.name, {F_SKIP_ORIGIN: None, F_CONFLICT_ATTENDANCE: None}
            )
        frappe.db.set_value("Attendance", w.absent, F_AUTO_GENERATED, 0)
        self.assertEqual(preview(w.absent)[0], ACTION_REPLACE)
        from thinknxg_attendance_reconcile.patches.v1_0 import backfill_provenance

        backfill_provenance.execute()
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, F_AUTO_GENERATED), 1)
        self.assertEqual(
            frappe.db.get_value("Employee Checkin", w.logs[0].name, F_SKIP_ORIGIN), SKIP_CONFLICT
        )

    # -- Protection rules ------------------------------------------------------
    def test_manual_absent_requires_approval(self):
        w = World()
        w.s = w.shift("09:00:00", "17:00:00")
        w.assign(w.s)
        w.absent = w.manual_absent(w.s)
        w.logs = [w.punch(9, "IN"), w.punch(17, "OUT")]  # resolve to S; never processed (attendance exists)
        action, info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)
        self.assertIn("Origin", info.reason)

        doc = frappe.get_doc("Attendance", w.absent)
        doc.set(F_APPROVED, 1)
        doc.save()
        self.assertReplaced(w, apply(w.absent), "Present")

    def test_non_hr_user_cannot_approve(self):
        w = World()
        w.absent = w.manual_absent()
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": f"tnxg-ar-{w.suffix}@example.com",
                "first_name": "AR",
                "send_welcome_email": 0,
                "roles": [{"role": "HR User"}],
            }
        ).insert(ignore_permissions=True)
        frappe.set_user(user.name)
        try:
            doc = frappe.get_doc("Attendance", w.absent)
            doc.set(F_APPROVED, 1)
            with self.assertRaises(frappe.PermissionError):
                doc.save()
        finally:
            frappe.set_user("Administrator")

    def test_pending_leave_application_blocks(self):
        w = self.late_sync_world()
        leave_type = frappe.get_doc(
            {"doctype": "Leave Type", "leave_type_name": "TNXG LWP " + w.suffix, "is_lwp": 1}
        ).insert()
        frappe.get_doc(
            {
                "doctype": "Leave Application",
                "employee": w.employee,
                "leave_type": leave_type.name,
                "from_date": w.day,
                "to_date": w.day,
                "status": "Open",
                "company": w.company,
                "leave_approver": "Administrator",
                "posting_date": nowdate(),
            }
        ).insert(ignore_permissions=True)
        action, info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)
        self.assertIn("Leave Application", info.reason)

    def test_approved_leave_is_never_replaced(self):
        w = self.late_sync_world()
        leave_type = frappe.get_doc(
            {"doctype": "Leave Type", "leave_type_name": "TNXG LWP " + w.suffix, "is_lwp": 1}
        ).insert()
        leave = frappe.get_doc(
            {
                "doctype": "Leave Application",
                "employee": w.employee,
                "leave_type": leave_type.name,
                "from_date": w.day,
                "to_date": w.day,
                "status": "Approved",
                "company": w.company,
                "leave_approver": "Administrator",
                "posting_date": nowdate(),
            }
        ).insert(ignore_permissions=True)
        leave.submit()
        action, _info = preview(w.absent)
        self.assertIn(action, (ACTION_REVIEW, ACTION_NO_CHANGE))  # HRMS may already mark it On Leave
        self.assertNotIn("Present", {a.status for a in w.active()})
        with self.assertRaises(engine.Outcome):
            apply(w.absent)

    def test_geolocation_tracking_does_not_block_biometric_logs(self):
        """Biometric logs have no coordinates; HRMS validate would reject a normal save."""
        w = self.late_sync_world()
        frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 1)
        try:
            self.assertReplaced(w, apply(w.absent), "Present")
        finally:
            frappe.db.set_single_value("HR Settings", "allow_geolocation_tracking", 0)

    def test_multi_shift_day_only_replaces_its_own_shift(self):
        frappe.db.set_single_value("HR Settings", "allow_multiple_shift_assignments", 1)
        try:
            w = World()
            morning = w.shift(
                "06:00:00",
                "10:00:00",
                working_hours_threshold_for_absent=2,
                working_hours_threshold_for_half_day=3,
            )
            evening = w.shift("14:00:00", "22:00:00")
            w.assign(morning)
            w.assign(evening)
            w.punch(6, "IN")
            w.punch(10, "OUT")
            run_hrms(morning)  # morning Present
            run_hrms(evening)  # evening Absent: punches not synced yet
            (w.absent,) = w.absences()
            self.assertEqual(frappe.db.get_value("Attendance", w.absent, "shift"), evening.name)
            w.logs = [w.punch(14, "IN"), w.punch(22, "OUT")]
            run_hrms(evening)  # duplicate with the evening absence -> skipped
            action, info = preview(w.absent)
            self.assertEqual((action, info.proposed_shift), (ACTION_REPLACE, evening.name))
            engine.reconcile(w.absent, SETTINGS_DICT, apply=True)
            self.assertEqual(
                sorted((a.shift, a.status) for a in w.active()),
                sorted([(morning.name, "Present"), (evening.name, "Present")]),
            )
        finally:
            frappe.db.set_single_value("HR Settings", "allow_multiple_shift_assignments", 0)

    def test_finalized_payroll_blocks(self):
        # Creating a real submitted Salary Slip needs a full payroll setup; the guard
        # itself (compat.finalized_payroll_document) is exercised with a stub result.
        w = self.late_sync_world()
        with patch(
            "thinknxg_attendance_reconcile.compat.finalized_payroll_document",
            return_value=("Salary Slip", "SAL-TEST"),
        ):
            action, info = preview(w.absent)
        self.assertEqual(action, ACTION_REVIEW)
        self.assertIn("Salary Slip", info.reason)

    def test_freeze_date_blocks(self):
        w = self.late_sync_world()
        action, _info = preview(w.absent, frappe._dict(SETTINGS_DICT, freeze_through=w.day))
        self.assertEqual(action, ACTION_REVIEW)

    def test_present_attendance_untouched(self):
        w = World()
        s = w.shift("09:00:00", "17:00:00")
        w.assign(s)
        w.punch(9, "IN")
        w.punch(17, "OUT")
        run_hrms(s)
        (present,) = w.active()
        self.assertEqual(present.status, "Present")
        self.assertEqual(preview(present.name)[0], ACTION_NO_CHANGE)

    def test_waits_until_sync_confirmed(self):
        w = self.late_sync_world()
        frappe.db.set_value(
            "Shift Type", w.s.name, "last_sync_of_checkin", get_datetime(w.day) + timedelta(hours=17)
        )
        self.assertEqual(preview(w.absent)[0], ACTION_WAIT)

    # -- Runs, batches, failures ----------------------------------------------
    def test_batch_failure_is_isolated(self):
        first, second = self.late_sync_world(), self.late_sync_world()
        settings = frappe.get_doc(SETTINGS)
        settings.update({"sync_grace_minutes": 0, "freeze_through": None, "auto_reset_conflict_skips": 1})
        settings.save()
        frappe.clear_document_cache(SETTINGS, SETTINGS)

        run = frappe.get_doc(
            {
                "doctype": RECONCILIATION,
                "from_date": first.day,
                "to_date": first.day,
                "company": first.company,
            }
        ).insert()
        run.preview()
        run.reload()
        mine = {r.attendance: r for r in run.items if r.attendance in (first.absent, second.absent)}
        self.assertEqual({r.proposed_action for r in mine.values()}, {ACTION_REPLACE})
        for row in run.items:  # restrict the run to our two records
            row.selected = int(row.attendance in mine)

        original = engine.compat.create_attendance

        def flaky(logs, *args, **kwargs):
            if logs[0].employee == first.employee:
                raise RuntimeError("simulated failure")
            return original(logs, *args, **kwargs)

        with patch("thinknxg_attendance_reconcile.compat.create_attendance", side_effect=flaky):
            run.reconcile_selected()
        run.reload()
        results = {r.attendance: r for r in run.items if r.attendance in mine}
        self.assertEqual(results[first.absent].result, "Error")
        self.assertEqual(results[second.absent].result, "Corrected")
        self.assertEqual(frappe.db.get_value("Attendance", first.absent, "docstatus"), 1)  # rolled back
        self.assertEqual(
            frappe.db.get_value("Employee Checkin", first.logs[0].name, "skip_auto_attendance"), 1
        )
        self.assertEqual(
            frappe.db.get_value(AUDIT_LOG, {"previous_attendance": first.absent}, "processing_status"),
            "Failed",
        )
        self.assertEqual(run.status, "Reconciled")

    def test_scheduled_run_corrects_and_records(self):
        w = self.late_sync_world()
        settings = frappe.get_doc(SETTINGS)
        settings.update({"enabled": 1, "lookback_days": 10, "sync_grace_minutes": 0, "freeze_through": None})
        settings.save()
        frappe.clear_document_cache(SETTINGS, SETTINGS)
        try:
            service.scheduled()
        finally:
            frappe.db.set_single_value(SETTINGS, "enabled", 0)
            frappe.clear_document_cache(SETTINGS, SETTINGS)
        self.assertEqual(frappe.db.get_value("Attendance", w.absent, "docstatus"), 2)
        log = frappe.get_doc(AUDIT_LOG, {"previous_attendance": w.absent})
        self.assertEqual(frappe.db.get_value(RECONCILIATION, log.reconciliation, "run_type"), "Scheduled")

    def test_run_requires_preview_before_reconcile(self):
        w = World()
        run = frappe.get_doc({"doctype": RECONCILIATION, "from_date": w.day, "to_date": w.day}).insert()
        with self.assertRaises(frappe.ValidationError):
            run.reconcile_all_eligible()
