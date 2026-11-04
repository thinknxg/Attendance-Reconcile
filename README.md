# thinkNXG Attendance Reconcile

Safely corrects **Absent** attendance in **thinkNXG HR** (v15 and v16) when the absence was
generated before the right shift or the biometric punches were available:

* the shift was assigned late and **Fetch Shift** was run afterwards;
* biometric check-ins synced after Auto Attendance had already marked the day Absent;
* the late check-ins were then marked **Skip Auto Attendance** because they collided with that absence.

thinkNXG HR stays the attendance engine. The app resolves shifts, calculates status and hours, and
creates and links attendance through the standard thinkNXG HR code. It cancels and replaces the
absence through the normal document lifecycle, and it never edits thinkNXG HR or thinkNXG Framework
core files.

App name: `thinknxg_attendance_reconcile` · Version 1.0.0 · License MIT · Support: https://thinknxg.com/support

## How a correction is decided

For each submitted Absent attendance in scope:

1. **Origin (Rule 3).** The absence must carry the marker thinkNXG HR Auto Attendance writes ("Employee
   was marked Absent due to missing Employee Checkins." or the working-hours-threshold note), or an
   HR Manager must have ticked **Approve Absence Reconciliation**. Amended or manual records go to HR review.
2. **Leave and payroll (Rule 4).** Correction is blocked by:
   * a leave reference on the attendance;
   * an Open or Approved Leave Application;
   * a submitted Attendance Request;
   * a submitted Salary Slip, or an Overtime Slip on v16;
   * the freeze date.
3. **Shift.** Every check-in from the day before to the day after is re-resolved with thinkNXG HR's
   own shift logic (`get_actual_start_end_datetime_of_shift`, the function behind Fetch Shift). Check-ins
   are grouped by **shift start date**, so night shifts belong to the day they began. Check-ins owned by
   another active attendance are left alone. Multiple shift instances on one date go to HR review
   unless exactly one matches the absence's shift.
4. **Skip Auto Attendance (Rule 2).** A skipped check-in takes part only when:
   * thinkNXG HR recorded that it was skipped because it collided with *this* absence, or with an
     attendance that has since been cancelled. The proof is the HRMS "Reason for skipping auto
     attendance" record, which links the conflicting attendance; or
   * an HR Manager ticked **Approve Reconciliation of Skipped Log**.

   Imports that set the skip themselves, validation-error skips, and logs whose **Skip Reason** is
   *Intentional* are never cleared automatically.
5. **Completeness.** The shift's actual end plus the grace period must have passed, and **Last Sync of
   Checkin** must be later than the shift's actual end. Otherwise the row is *Wait for Sync* and is retried later.
6. **Calculation (Rule 1).** `ShiftType.get_attendance` produces the status, using the v16 thresholds and
   half-holiday halving where available. *Absent* means no change. A single punch, or no valid IN/OUT
   pair, goes to HR review. A punch alone never produces Present.
7. **Apply.** Under row locks:
   * the absence is cancelled (thinkNXG HR unlinks its check-ins);
   * each log is re-fetched with `fetch_shift()` and saved the way thinkNXG HR's bulk Fetch Shift does;
   * only proven skips are cleared;
   * `mark_attendance_and_link_log` creates the submitted Present or Half Day attendance with all
     Attendance validations;
   * the audit log is written.

   If anything fails, that employee/date rolls back completely and the run continues with the next record.

Running it again is safe. A replaced absence is cancelled, so a second run reports *No Change*, and
thinkNXG HR's duplicate validation still applies.

## Using it

1. Fix Shift Assignments or default shifts and finish the biometric sync.
2. Open **thinkNXG Attendance Reconcile Settings** and set:
   * lookback;
   * grace minutes;
   * freeze date;
   * whether proven conflict skips may be cleared automatically (default on).
3. Create a **thinkNXG Attendance Reconciliation**. You can filter by:
   * Company, Employee, Department and Shift Type;
   * From and To Date;
   * Reconciliation Status.
4. **Preview Reconciliation** runs in the background and lists each absence with:
   * existing and proposed status, and shift;
   * check-ins found, working hours and Skip Auto Attendance status;
   * proposed action, reason, and whether HR review is required.

   Preview changes nothing.
5. **Reconcile → Reconcile Selected** or **Reconcile All Eligible** queues the corrections. Every row is
   revalidated at execution time.
6. **View Reconciliation History** opens **thinkNXG Attendance Reconcile Log**. Each entry records:
   * the previous and corrected attendance, statuses and shifts;
   * every check-in with its previous and new shift, skip flag and skip origin;
   * the reason, who ran it and when;
   * failures, with error details.
7. After validating on staging, tick **Enable Hourly Reconciliation**. Scheduled runs keep a record only
   when they corrected something or hit an error.

For an absence or skipped log that needs a human decision, verify it, tick the approval box on the
Attendance or Employee Checkin (HR Manager only), and preview again.

Access: HR Manager or System Manager. Background jobs run as the requesting user, so document and
employee permissions apply. System Manager sees all runs; other users see runs they created. Audit
logs follow Employee user permissions.

## Installation

Back up first and install on staging before production.

```sh
cd /path/to/frappe-bench
bench --site your-site backup --with-files
bench get-app thinknxg_attendance_reconcile https://github.com/thinknxg/Attendance-Reconcile.git
bench --site your-site install-app thinknxg_attendance_reconcile
bench --site your-site migrate
bench build --app thinknxg_attendance_reconcile
bench restart
```

On thinkNXG Cloud (https://thinknxgcloud.com), add this repository through the custom-app deployment workflow.

Installation adds these custom fields:

* On **Attendance**: *Created by Auto Attendance*, *Approve Absence Reconciliation* and *Reconciled From*.
* On **Employee Checkin**: *Skip Reason*, *Conflicting Attendance* and *Approve Reconciliation of Skipped Log*.

It then backfills the auto-attendance and skip markers from the records thinkNXG HR has already written.
No permissions are customised: approval fields are protected by validation hooks. Keep the scheduler,
a `long` queue worker and Redis running.

### Upgrading from the pre-rebrand `attendance_reconciliation` app

```sh
bench get-app thinknxg_attendance_reconcile https://github.com/thinknxg/Attendance-Reconcile.git
bench --site your-site install-app thinknxg_attendance_reconcile   # copies custom_ar_* values and legacy logs
bench --site your-site migrate
# verify the migrated data, then:
bench --site your-site uninstall-app attendance_reconciliation
bench remove-app attendance_reconciliation
```

Uninstalling the old app drops its DocTypes, so verify the migrated **thinkNXG Attendance Reconcile Log**
entries first. Its custom fields (`custom_ar_*`) and the custom permission rows it added on Attendance and
Employee Checkin stay behind. Remove them manually if you no longer want them.

## Tests

```sh
bench --site test-site set-config allow_tests true
bench --site test-site run-tests --app thinknxg_attendance_reconcile
PYTHONPATH=. python -m unittest discover -s tests -v    # framework-free checks
```

The bench suite has 26 tests. It uses the real Auto Attendance engine to produce absences and
conflict skips, and passes on thinkNXG HR 15.64 and 16.53.

## Known limits

* An absence on a date with no check-ins for its employee is reported as *No Change*. Missing punches
  must be imported first.
* A finalized payroll recorded outside Salary Slip or Overtime Slip, such as an external payroll system,
  is not detected. Use the freeze date.
* If another app overrides the thinkNXG HR attendance functions this app calls, test the combination
  on staging.

Documentation: https://docs.thinknxg.com/thinknxghr
