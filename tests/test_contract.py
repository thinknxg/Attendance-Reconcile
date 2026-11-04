"""Framework-free checks for pure logic. The bench suite in
thinknxg_attendance_reconcile/tests/test_reconcile.py is the real acceptance test.

    PYTHONPATH=. python -m unittest discover -s tests -v
"""

import sys
import types
import unittest
from datetime import date

frappe = types.ModuleType("frappe")
frappe.utils = types.ModuleType("frappe.utils")
frappe.utils.flt = lambda v, p=None: float(v or 0)
frappe.utils.get_datetime = lambda v: v
frappe._ = lambda msg, lang=None: {"ar": {"Reason for skipping auto attendance:": "سبب التخطي:"}}.get(
    lang, {}
).get(msg, msg)
frappe._dict = dict
frappe.local = types.SimpleNamespace(cache={"tnxg_ar_languages": ["ar", "en"]})
frappe.flags = types.SimpleNamespace(in_test=False)
sys.modules.setdefault("frappe", frappe)
sys.modules.setdefault("frappe.utils", frappe.utils)

from thinknxg_attendance_reconcile import compat, provenance  # noqa: E402

V15_SKIP = (
    "<strong>Reason for skipping auto attendance:</strong><br>Attendance for employee "
    "<strong>HR-EMP-1</strong> is already marked for the date <strong>02-11-2026</strong>: "
    '<a href="https://erp.example.com/app/attendance/HR-ATT-2026-00012">HR-ATT-2026-00012</a>'
)
V16_OVERLAP = (
    "<strong>Reason for skipping auto attendance:</strong><br>Attendance for employee "
    "<strong>HR-EMP-1</strong> is already marked for an overlapping shift <strong>Day</strong>: "
    '<a href="https://erp.example.com/desk/attendance/HR-ATT-2026-00013">HR-ATT-2026-00013</a>'
)


class ProvenanceParsing(unittest.TestCase):
    def test_v15_and_v16_links(self):
        self.assertEqual(provenance.attendance_links(V15_SKIP), {"HR-ATT-2026-00012"})
        self.assertEqual(provenance.attendance_links(V16_OVERLAP), {"HR-ATT-2026-00013"})

    def test_skip_prefix_in_english_and_translation(self):
        self.assertTrue(provenance.is_skip_comment(V15_SKIP))
        self.assertTrue(provenance.is_skip_comment("<strong>سبب التخطي:</strong><br>x"))
        self.assertFalse(provenance.is_skip_comment("Reason: something else"))

    def test_auto_absent_comment_exact_match(self):
        self.assertTrue(
            provenance.is_auto_absent_comment(
                "<div>Employee was marked Absent due to missing Employee Checkins.</div>"
            )
        )
        self.assertFalse(provenance.is_auto_absent_comment("Marked absent by HR"))

    def test_quoted_names_are_unquoted(self):
        self.assertEqual(provenance.attendance_links('href="/app/attendance/ATT%2F1"'), {"ATT/1"})


class Shift:
    working_hours_threshold_for_absent = 4
    working_hours_threshold_for_half_day = 8

    def __init__(self, half_holiday=False):
        self.half_holiday = half_holiday

    def is_half_holiday(self, employee, day):
        return self.half_holiday


class VersionAdapters(unittest.TestCase):
    def test_v15_signature(self):
        shift = types.SimpleNamespace(get_attendance=lambda logs: ("Present", len(logs)))
        self.assertEqual(compat.calculate(shift, [1, 2], "E", date.today()), ("Present", 2))

    def test_v16_thresholds_and_half_holiday(self):
        for half_holiday, expected in ((False, (4, 8)), (True, (2, 4))):
            shift = Shift(half_holiday)
            shift.get_attendance = (
                lambda logs, working_hours_threshold_for_absent, working_hours_threshold_for_half_day: (
                    working_hours_threshold_for_absent,
                    working_hours_threshold_for_half_day,
                )
            )
            self.assertEqual(compat.calculate(shift, [], "E", date.today()), expected)

    def test_overtime_type_passed_only_when_supported(self):
        module = types.ModuleType("hrms.hr.doctype.employee_checkin.employee_checkin")
        for name in ("hrms", "hrms.hr", "hrms.hr.doctype", "hrms.hr.doctype.employee_checkin"):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules[module.__name__] = module
        result = ("Present", 8, 0, 0, None, None)

        def v15(
            logs,
            attendance_status,
            attendance_date,
            working_hours=None,
            late_entry=False,
            early_exit=False,
            in_time=None,
            out_time=None,
            shift=None,
        ):
            return "v15"

        def v16(
            logs,
            attendance_status,
            attendance_date,
            working_hours=None,
            late_entry=False,
            early_exit=False,
            in_time=None,
            out_time=None,
            shift=None,
            overtime_type=None,
        ):
            return overtime_type

        module.mark_attendance_and_link_log = v15
        self.assertEqual(compat.create_attendance([{}], result, date.today(), "S"), "v15")
        module.mark_attendance_and_link_log = v16
        self.assertEqual(compat.create_attendance([{"overtime_type": "OT"}], result, date.today(), "S"), "OT")

    def test_major_parsing(self):
        self.assertEqual(compat.major("16.0.0-dev"), 16)
        self.assertEqual(compat.major("15.64.7"), 15)


if __name__ == "__main__":
    unittest.main()
