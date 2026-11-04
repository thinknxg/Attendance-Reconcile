"""Framework-free contract tests. These do not replace the bench acceptance suite."""
import copy
from datetime import date, datetime
import sys
import types
import unittest
from unittest.mock import Mock, patch


def dt(value=None):
    if value is None:
        return datetime(2026, 11, 4, 12)
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


frappe = types.ModuleType('frappe')
utils = types.ModuleType('frappe.utils')
utils.cint = lambda value: int(value or 0)
utils.getdate = lambda value=None: dt(value).date()
utils.get_datetime = dt
utils.now_datetime = dt
utils.nowdate = lambda: '2026-11-04'
utils.add_days = lambda value, days: (dt(value) + __import__('datetime').timedelta(days=days)).date()
frappe.whitelist = lambda **kw: lambda function: function
frappe.utils = utils
sys.modules['frappe'] = frappe
sys.modules['frappe.utils'] = utils

from attendance_reconciliation import compat, engine, api


class Record(types.SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


class CompatibilityTests(unittest.TestCase):
    def test_v15_calculator(self):
        shift = Record(get_attendance=lambda logs: ('Present', len(logs)))
        self.assertEqual(compat.calculate(shift, [1, 2], 'E', date.today()), ('Present', 2))

    def test_v16_half_holiday(self):
        shift = Record(working_hours_threshold_for_absent=4,
                       working_hours_threshold_for_half_day=8, is_half_holiday=lambda *a: True,
                       get_attendance=lambda logs, working_hours_threshold_for_absent,
                           working_hours_threshold_for_half_day:
                           (working_hours_threshold_for_absent, working_hours_threshold_for_half_day))
        self.assertEqual(compat.calculate(shift, [], 'E', date.today()), (2, 4))

    def test_v16_full_day(self):
        shift = Record(working_hours_threshold_for_absent=4,
                       working_hours_threshold_for_half_day=8, is_half_holiday=lambda *a: False,
                       get_attendance=lambda logs, working_hours_threshold_for_absent,
                           working_hours_threshold_for_half_day:
                           (working_hours_threshold_for_absent, working_hours_threshold_for_half_day))
        self.assertEqual(compat.calculate(shift, [], 'E', date.today()), (4, 8))

    def test_create_adapts_overtime(self):
        module = types.ModuleType('hrms.hr.doctype.employee_checkin.employee_checkin')
        captured = {}
        def v15(logs, attendance_status, attendance_date, working_hours, late_entry,
                early_exit, in_time, out_time, shift):
            captured['shift'] = shift
            return 'V15'
        module.mark_attendance_and_link_log = v15
        path = module.__name__
        with patch.dict(sys.modules, {path: module}):
            self.assertEqual(compat.create_attendance([{}], ('Present', 8, 0, 0, None, None),
                                                      date.today(), Record(name='S')), 'V15')
            def v16(logs, attendance_status, attendance_date, working_hours, late_entry,
                    early_exit, in_time, out_time, shift, overtime_type=None):
                return overtime_type
            module.mark_attendance_and_link_log = v16
            self.assertEqual(compat.create_attendance([{'overtime_type': 'OT'}],
                ('Present', 8, 0, 0, None, None), date.today(), Record(name='S')), 'OT')


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.a = Record(name='A', employee='E', attendance_date='2026-11-02', docstatus=1,
            status='Absent', custom_ar_auto_generated=1, custom_ar_approved=0,
            leave_application=None, leave_type=None, check_permission=Mock(), cancel=Mock())
        self.settings = Record(freeze_through=None, sync_grace_minutes=60)
        self.shift = Record(name='S', enable_auto_attendance=1, process_attendance_after='2026-01-01',
            last_sync_of_checkin='2026-11-03', should_mark_attendance=lambda *a: True)
        self.result = ('Present', 8, False, False, dt('2026-11-02 09:00'), dt('2026-11-02 17:00'))
        self.logs = []
        for i, time in enumerate(('09:00', '17:00')):
            log = Record(name=str(i), employee='E', time=dt('2026-11-02 ' + time), attendance=None,
                shift=None, offshift=1, skip_auto_attendance=0, custom_ar_reset_skip_approved=0,
                check_permission=Mock(), save=Mock())
            def fetch(log=log):
                log.shift, log.offshift = 'S', 0
                log.shift_start = dt('2026-11-02 09:00')
                log.shift_end = dt('2026-11-02 17:00')
                log.shift_actual_end = dt('2026-11-02 18:00')
            log.fetch_shift = fetch
            self.logs.append(log)
        frappe.db = Record(exists=Mock(return_value=False))
        frappe.get_all = Mock(return_value=['0', '1'])
        frappe.get_doc = Mock(side_effect=lambda kind, name: self.shift if kind == 'Shift Type' else self.logs[int(name)])
        self.calculator = patch.object(engine, 'calculate', return_value=self.result)
        self.calculator.start()
        self.addCleanup(self.calculator.stop)

    def plan(self):
        return engine.plan(self.a, self.settings)

    def review(self, text):
        with self.assertRaisesRegex(engine.NeedsReview, text):
            self.plan()

    def test_preview_has_no_writes(self):
        self.assertEqual(self.plan()[2][0], 'Present')
        self.a.cancel.assert_not_called()
        for log in self.logs:
            log.save.assert_not_called()

    def test_manual_requires_approval(self):
        self.a.custom_ar_auto_generated = 0
        self.review('requires HR approval')

    def test_manual_approved(self):
        self.a.custom_ar_auto_generated = 0
        self.a.custom_ar_approved = 1
        self.assertEqual(self.plan()[2][0], 'Present')

    def test_cancelled_is_idempotent(self):
        self.a.docstatus = 2
        self.review('No longer')

    def test_present_is_not_reprocessed(self):
        self.a.status = 'Present'
        self.review('No longer')

    def test_freeze(self):
        self.settings.freeze_through = '2026-11-02'
        self.review('frozen')

    def test_leave_reference(self):
        self.a.leave_application = 'L'
        self.review('leave references')

    def test_approved_leave(self):
        frappe.db.exists.side_effect = lambda kind, filters: kind == 'Leave Application'
        self.review('Approved leave')

    def test_payroll(self):
        frappe.db.exists.side_effect = lambda kind, filters: kind == 'Salary Slip'
        self.review('salary slip')

    def test_other_attendance(self):
        frappe.db.exists.side_effect = lambda kind, filters: kind == 'Attendance'
        self.review('Another active')

    def test_skipped_log(self):
        self.logs[0].skip_auto_attendance = 1
        self.review('Skipped check-in')

    def test_approved_skipped_log(self):
        self.logs[0].skip_auto_attendance = 1
        self.logs[0].custom_ar_reset_skip_approved = 1
        self.assertTrue(self.plan())

    def test_link_to_other_attendance(self):
        self.logs[0].attendance = 'OTHER'
        self.review('another attendance')

    def test_link_to_target_kept_in_preview(self):
        self.logs[0].attendance = 'A'
        self.plan()
        self.assertEqual(self.logs[0].attendance, 'A')

    def test_no_logs(self):
        frappe.get_all.return_value = []
        self.review('No check-ins')

    def test_sync_not_complete(self):
        self.shift.last_sync_of_checkin = '2026-11-02 18:00'
        self.review('Last Sync')

    def test_disabled_shift(self):
        self.shift.enable_auto_attendance = 0
        self.review('disabled')

    def test_holiday(self):
        self.shift.should_mark_attendance = lambda *a: False
        self.review('holiday')

    def test_still_absent(self):
        with patch.object(engine, 'calculate', return_value=('Absent', 1, 0, 0, None, None)):
            self.review('still calculate')

    def test_single_punch_never_present(self):
        with patch.object(engine, 'calculate', return_value=('Present', 0, 0, 0, self.result[4], None)):
            self.review('valid IN/OUT')

    def test_overnight_uses_shift_start_date(self):
        self.logs[1].time = dt('2026-11-03 05:00')
        self.assertEqual(len(self.plan()[1]), 2)


class RunTests(unittest.TestCase):
    def setUp(self):
        frappe.session = Record(user='hr@example.test')
        frappe.only_for = Mock()
        frappe.get_single = Mock(return_value=Record())
        frappe.get_list = Mock(return_value=['A', 'B'])
        frappe.db = Record(savepoint=Mock(), rollback=Mock(), commit=Mock())
        frappe.cache = Record(lock=Mock(return_value=__import__('contextlib').nullcontext()),
                              make_key=lambda key: 'test-site|' + key)
        frappe.log_error = Mock(return_value=Record(name='ERR'))
        frappe.as_json = str
        frappe.get_doc = Mock(return_value=Record(insert=Mock(return_value=Record(name='RUN'))))

    def test_failure_rolls_back_then_continues(self):
        with patch.object(api, 'reconcile', side_effect=[RuntimeError('failed'), {'action': 'Corrected'}]):
            result = api.process('2026-11-01', '2026-11-03', apply=True)
        self.assertEqual([r['action'] for r in result['rows']], ['Error', 'Corrected'])
        frappe.db.rollback.assert_called_once_with(save_point='ar_item')
        self.assertEqual(frappe.db.commit.call_count, 2)  # success and run audit

    def test_enqueue_after_commit_can_return_none(self):
        frappe.enqueue = Mock(return_value=None)
        result = api.run('2026-11-01', '2026-11-03')
        self.assertTrue(result['job_id'].startswith('attendance-reconciliation-'))
        self.assertTrue(frappe.enqueue.call_args.kwargs['enqueue_after_commit'])

    def test_review_rolls_back(self):
        with patch.object(api, 'reconcile', side_effect=engine.NeedsReview('Skipped')):
            result = api.process('2026-11-01', '2026-11-03')
        self.assertEqual(result['rows'][0]['action'], 'Review')
        frappe.db.commit.assert_not_called()

    def test_range_validation(self):
        frappe.throw = Mock(side_effect=ValueError)
        for start, end in [('2026-11-04', '2026-11-01'), ('2026-01-01', '2026-11-01'),
                           ('2026-11-01', '2026-11-05')]:
            with self.assertRaises(ValueError):
                api.dates(start, end)

    def test_batch_reports_cursor(self):
        frappe.get_list.return_value = [str(i) for i in range(201)]
        with patch.object(api, 'reconcile', return_value={'action': 'Ready'}):
            result = api.process('2026-11-01', '2026-11-03')
        self.assertEqual(len(result['rows']), 200)
        self.assertEqual(result['next_cursor'], '199')
        self.assertTrue(result['truncated'])


if __name__ == '__main__':
    unittest.main()
