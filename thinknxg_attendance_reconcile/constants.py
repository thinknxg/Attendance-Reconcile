"""Names shared by every module. Keep DocType/field names in one place."""

APP = "thinknxg_attendance_reconcile"

SETTINGS = "thinkNXG Attendance Reconcile Settings"
RECONCILIATION = "thinkNXG Attendance Reconciliation"
RECONCILIATION_ITEM = "thinkNXG Attendance Reconciliation Item"
AUDIT_LOG = "thinkNXG Attendance Reconcile Log"
AUDIT_LOG_CHECKIN = "thinkNXG Attendance Reconcile Log Checkin"

HR_ROLES = ("HR Manager", "System Manager")

# Custom fields added to standard HRMS DocTypes (see install.py).
F_AUTO_GENERATED = "tnxg_ar_auto_generated"  # Attendance: created by HRMS auto attendance
F_APPROVED = "tnxg_ar_approved"  # Attendance: HR approved reconciliation of this absence
F_RECONCILED_FROM = "tnxg_ar_reconciled_from"  # Attendance: replacement of a cancelled absence
F_SKIP_ORIGIN = "tnxg_ar_skip_origin"  # Employee Checkin: why skip_auto_attendance is set
F_CONFLICT_ATTENDANCE = "tnxg_ar_conflict_attendance"  # Employee Checkin: attendance HRMS collided with
F_SKIP_APPROVED = "tnxg_ar_reset_skip_approved"  # Employee Checkin: HR approved clearing the skip

SKIP_CONFLICT = "Attendance Conflict"
SKIP_VALIDATION = "Validation Error"
SKIP_INTENTIONAL = "Intentional"

# Proposed actions shown in preview.
ACTION_REPLACE = "Replace Attendance"
ACTION_NO_CHANGE = "No Change"
ACTION_WAIT = "Wait for Sync"
ACTION_REVIEW = "HR Review"
ACTION_ERROR = "Error"

# Item results after a reconcile attempt.
RESULT_PENDING = "Pending"
RESULT_CORRECTED = "Corrected"

# Fields of the legacy (pre-rebrand) app, migrated by patches/v1_0.
LEGACY_FIELDS = {
    "Attendance": {
        "custom_ar_auto_generated": F_AUTO_GENERATED,
        "custom_ar_approved": F_APPROVED,
    },
    "Employee Checkin": {"custom_ar_reset_skip_approved": F_SKIP_APPROVED},
}
LEGACY_LOG = "Attendance Reconciliation Log"
