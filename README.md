# Attendance Reconciliation for Frappe HR

Custom app targeting matching Frappe / ERPNext / HRMS v15 or v16 installations.
Version: 0.1.0. No core files are edited.

## What it does

Reprocesses **existing submitted Absent Attendance** after late shift assignments
or late biometric imports. It fetches shifts from check-in timestamps, uses HRMS's
attendance calculator, cancels the eligible absence and creates submitted Present
or Half Day Attendance. Check-ins are linked to the replacement. Cancelled records
are retained, with an immutable reconciliation audit and run history.

Includes an HR settings form with Preview and Run buttons, background jobs, and an
optional hourly scheduler. Automatic processing is disabled on installation.
The job rereads and revalidates records rather than trusting an earlier preview.

This app does not import biometric data, assign missing shifts, create attendance
for dates with no existing absence, or force Present from an isolated punch.
The biometric integration must update Last Sync of Checkin only after importing
all punches through that timestamp. Elapsed time alone cannot prove sync completeness.

## Installation

Use a Linux bench/server with the matching major versions of Frappe, ERPNext and
HRMS already installed. Windows is suitable for viewing this source package;
install it into your Frappe server. Take a site backup and install on staging first.

Install directly from this repository. The app's internal name is
`attendance_reconciliation`:

```sh
cd /path/to/frappe-bench
bench --site your-site backup --with-files
bench get-app attendance_reconciliation https://github.com/thinknxg/Attendance-Reconcile.git --branch main
bench --site your-site install-app attendance_reconciliation
bench --site your-site migrate
bench build --app attendance_reconciliation
bench restart
```

For Frappe Cloud or managed hosting, put this source in your own Git repository and
use your host's custom-app deployment workflow. Custom apps require a compatible plan.

The install/migrate hook creates three custom fields and grants HR Manager and
System Manager read/write access at permission level 1 on Attendance and Employee
Checkin so they can approve historical corrections. Existing level 0 permissions
and user restrictions still apply. Review your site's custom permission setup.

## First use

1. Correct Shift Assignments / Employee Default Shift and complete biometric sync.
2. Open **Attendance Reconciliation Settings** from the Desk search.
3. Set the lookback (default 30 days), grace period after actual shift end (60 minutes),
   and optional **Do Not Change Attendance On or Before** cutoff. Save.
4. For older or manually created absences, open Attendance and check
   **Approve Absence Reconciliation**, then save.
5. For skipped check-ins that should participate, open each relevant Employee
   Checkin and check **Approve Reconciliation of Skipped Log**, then save.
   Intentional exclusions must remain unapproved.
6. Click **Preview Reconciliation**, choose dates and optionally an employee.
   Review Ready / Review / Error rows. Narrow the selection if it exceeds 200 absences.
7. Click **Run Reconciliation** with the desired scope. It queues a background job.
   Open **Attendance Reconciliation Run** for results, and **Attendance
   Reconciliation Log** for successful correction details.
8. Enable hourly reconciliation after validating the staging results.

HR Manager or System Manager is required. Read, write, cancel, create and submit
permissions are checked where appropriate; the background job retains its requesting
user. A manager can read their own run history; System Manager can read all runs.
Employee restrictions also apply to the employee-linked correction audit.

## Eligibility and safety

* Existing absence must have an app-recorded auto-attendance origin or explicit
  HR approval. Future automatic Attendance is stamped by the Shift Type subclass.
  Historical origin is never guessed from ownership or comments.
* **Skipped logs always require explicit per-log approval**, including skips made
  by HRMS itself: the standard checkbox does not reliably record the skip reason.
  Once used, that approval and the Skip Auto Attendance flag are cleared.
* Shift assignment must resolve to exactly one shift instance on the attendance
  date. Overnight punches are grouped using the shift start date, not calendar date.
* Date must pass the freeze cutoff, shift end plus grace period, and Last Sync of
  Checkin. Process Attendance After and the shift holiday policy are respected.
* Submitted overlapping Salary Slips, approved leave, leave-linked Attendance,
  and any other active Attendance on the same date block correction.
* A valid IN/OUT interval and positive working hours are required. Insufficient
  hours remain Absent; Half Day follows the configured shift thresholds.
* V16 calculation supports its explicit thresholds, half-holiday adjustments and
  overtime parameter when available. Earlier signatures use the V15 path.
* Each correction uses a savepoint. Failure to create/link the replacement or write
  its audit rolls back cancellation and check-in changes for that employee/date.
  Earlier successful corrections remain committed if a later record fails.
* A site-scoped Redis lock serializes app runs. Employee, target Attendance and
  existing check-ins are locked during correction. HRMS duplicate validation remains
  active. Standard auto-attendance and biometric imports can still run independently;
  if they race and cause a conflict, the correction should roll back and be retried.
* Manual runs process up to 200 absences. Hourly runs page through the lookback
  using a name cursor, so review-only records cannot starve later eligible records.
  Very large sites should tune queue timeouts and lookback on staging.

The app overrides the Shift Type controller only to stamp provenance around standard
auto-attendance processing. If another app overrides Shift Type, combine the
controllers or resolve hook priority before deployment. Otherwise origin stamping
may be unavailable. HR approval is still available for unstamped absences.

## Tests and verification status

Local verification: **30 framework-free contract tests passed**, plus Python syntax,
DocType JSON and JavaScript syntax checks. These exercise version adapters,
eligibility rules, preview behavior, repeat processing, rollback orchestration,
queue responses and pagination. They use test doubles; they do not validate a real
Frappe database, permissions engine or Redis.

A live bench suite is included. Run on disposable staging sites for **each** major
version after installation and with a test Company configured:

```sh
bench --site test-site set-config allow_tests true
bench --site test-site run-tests --app attendance_reconciliation \
  --module attendance_reconciliation.tests.test_live
```

The live suite tests preview/apply/repeat, approved and unapproved skips, and database
rollback. It has not been executed in the authoring environment because Frappe,
HRMS, a database and Redis are not installed here. Do not treat this package as
production-certified until both version-specific staging runs pass.

Additional staging acceptance checks: late shift assignment; overnight shift;
strict IN/OUT pairing; multiple valid punch pairs; Half Day thresholds; V16 half
holidays and overtime; salary/leave/freeze blocks; an HR user restricted to one
employee; concurrent standard auto-attendance and imports; and a lookback with more
than 200 review-only absences followed by eligible corrections.

Framework-free tests (from this folder):

```sh
PYTHONPATH=. python -m unittest discover -s tests -v
```

## Operations

Keep scheduler, long-queue worker and Redis running. Check Error Log / RQ Job for
technical failures and run history for review reasons. Hourly jobs reconcile changes
within the lookback; there are no immediate import or shift-assignment event jobs.

Set the freeze cutoff when payroll is finalized. The app blocks submitted Salary
Slips; it does not infer closure in external payroll systems or custom workflows.
It never recalculates payroll automatically.

To stop automation, uncheck Enable Hourly Reconciliation. Back up audit data before
uninstalling; custom fields and custom permission rows are deliberately not deleted
automatically. Existing corrections are not undone by disabling or uninstalling.

## References checked

* [HRMS V15 Employee Checkin](https://github.com/frappe/hrms/blob/version-15/hrms/hr/doctype/employee_checkin/employee_checkin.py)
* [HRMS V16 Employee Checkin](https://github.com/frappe/hrms/blob/version-16/hrms/hr/doctype/employee_checkin/employee_checkin.py)
* [HRMS V15 Shift Type](https://github.com/frappe/hrms/blob/version-15/hrms/hr/doctype/shift_type/shift_type.py)
* [HRMS V16 Shift Type](https://github.com/frappe/hrms/blob/version-16/hrms/hr/doctype/shift_type/shift_type.py)
* [Frappe V15 background jobs](https://github.com/frappe/frappe/blob/version-15/frappe/utils/background_jobs.py)

License: MIT. HRMS remains a separately installed upstream dependency.
