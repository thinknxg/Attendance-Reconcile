"""Run in an installed disposable bench site on each supported major version."""
from datetime import timedelta
from uuid import uuid4

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate, nowdate

from attendance_reconciliation.engine import NeedsReview, reconcile


class TestLiveReconciliation(FrappeTestCase):
    def setUp(self):
        frappe.set_user('Administrator')
        companies = frappe.get_all('Company', pluck='name', limit=1)
        if not companies:
            self.skipTest('Create a test Company before running this suite')
        suffix = uuid4().hex[:10]
        self.day = getdate(add_days(nowdate(), -5))
        holiday = frappe.get_doc({
            'doctype': 'Holiday List', 'holiday_list_name': 'AR-' + suffix,
            'from_date': add_days(self.day, -30), 'to_date': add_days(self.day, 30),
        }).insert()
        self.employee = frappe.get_doc({
            'doctype': 'Employee', 'first_name': 'AR Test ' + suffix,
            'company': companies[0], 'status': 'Active', 'gender': 'Male',
            'date_of_joining': add_days(self.day, -30), 'holiday_list': holiday.name,
            'naming_series': 'HR-EMP-.#####',
        }).insert()
        self.shift = frappe.get_doc({
            'doctype': 'Shift Type', 'name': 'AR-' + suffix,
            'start_time': '09:00:00', 'end_time': '17:00:00',
            'enable_auto_attendance': 1, 'mark_auto_attendance_on_holidays': 1,
            'holiday_list': holiday.name,
            'process_attendance_after': add_days(self.day, -1),
            'last_sync_of_checkin': get_datetime(add_days(self.day, 2)),
            'determine_check_in_and_check_out': 'Alternating entries as IN and OUT during the same shift',
            'working_hours_calculation_based_on': 'First Check-in and Last Check-out',
            'working_hours_threshold_for_absent': 4,
            'working_hours_threshold_for_half_day': 7,
        }).insert()
        assignment = frappe.get_doc({
            'doctype': 'Shift Assignment', 'employee': self.employee.name,
            'shift_type': self.shift.name, 'start_date': self.day, 'end_date': self.day,
        }).insert()
        assignment.submit()
        self.logs = [frappe.get_doc({
            'doctype': 'Employee Checkin', 'employee': self.employee.name,
            'time': get_datetime(self.day) + timedelta(hours=hour),
            'log_type': log_type,
        }).insert() for hour, log_type in [(9, 'IN'), (17, 'OUT')]]
        self.absence = frappe.get_doc({
            'doctype': 'Attendance', 'employee': self.employee.name,
            'attendance_date': self.day, 'shift': self.shift.name, 'status': 'Absent',
        }).insert()
        self.absence.submit()
        self.absence.custom_ar_approved = 1
        self.absence.save()
        self.settings = frappe.get_single('Attendance Reconciliation Settings')
        self.settings.freeze_through = None
        self.settings.sync_grace_minutes = 0

    def test_preview_apply_and_repeat(self):
        preview = reconcile(self.absence.name, self.settings)
        self.assertEqual(preview['status'], 'Present')
        self.assertEqual(frappe.db.get_value('Attendance', self.absence.name, 'docstatus'), 1)
        result = reconcile(self.absence.name, self.settings, apply=True)
        self.assertEqual(frappe.db.get_value('Attendance', self.absence.name, 'docstatus'), 2)
        self.assertEqual(frappe.db.get_value('Attendance', result['replacement'], 'status'), 'Present')
        for log in self.logs:
            self.assertEqual(frappe.db.get_value('Employee Checkin', log.name, 'attendance'), result['replacement'])
        with self.assertRaises(NeedsReview):
            reconcile(self.absence.name, self.settings, apply=True)

    def test_unapproved_skip_is_preserved(self):
        frappe.db.set_value('Employee Checkin', self.logs[0].name, 'skip_auto_attendance', 1)
        with self.assertRaises(NeedsReview):
            reconcile(self.absence.name, self.settings, apply=True)
        self.assertEqual(frappe.db.get_value('Attendance', self.absence.name, 'docstatus'), 1)
        self.assertEqual(frappe.db.get_value('Employee Checkin', self.logs[0].name, 'skip_auto_attendance'), 1)

    def test_approved_skip_is_reset(self):
        frappe.db.set_value('Employee Checkin', self.logs[0].name,
                            {'skip_auto_attendance': 1, 'custom_ar_reset_skip_approved': 1})
        result = reconcile(self.absence.name, self.settings, apply=True)
        self.assertEqual(result['action'], 'Corrected')
        self.assertEqual(frappe.db.get_value('Employee Checkin', self.logs[0].name, 'skip_auto_attendance'), 0)

    def test_failure_can_be_rolled_back(self):
        from unittest.mock import patch
        frappe.db.savepoint('ar_live_failure')
        with patch('attendance_reconciliation.engine.create_attendance', return_value=None):
            with self.assertRaises(NeedsReview):
                reconcile(self.absence.name, self.settings, apply=True)
        frappe.db.rollback(save_point='ar_live_failure')
        self.assertEqual(frappe.db.get_value('Attendance', self.absence.name, 'docstatus'), 1)
        for log in self.logs:
            self.assertFalse(frappe.db.get_value('Employee Checkin', log.name, 'attendance'))
