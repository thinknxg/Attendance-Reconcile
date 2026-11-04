app_name = "thinknxg_attendance_reconcile"
app_title = "thinkNXG Attendance Reconcile"
app_publisher = "thinkNXG"
app_description = (
    "Safely reconcile Absent attendance after late shift assignment or delayed biometric sync in thinkNXG HR"
)
app_email = ""
app_license = "MIT"
required_apps = ["frappe", "erpnext", "hrms"]

before_install = "thinknxg_attendance_reconcile.install.before_install"
after_install = "thinknxg_attendance_reconcile.install.after_install"
after_migrate = "thinknxg_attendance_reconcile.install.after_migrate"

doc_events = {
    "Attendance": {
        "validate": "thinknxg_attendance_reconcile.events.guard_attendance",
        "before_update_after_submit": "thinknxg_attendance_reconcile.events.guard_attendance",
    },
    "Employee Checkin": {"validate": "thinknxg_attendance_reconcile.events.guard_checkin"},
    "Comment": {"after_insert": "thinknxg_attendance_reconcile.events.on_comment_insert"},
}

scheduler_events = {"hourly_long": ["thinknxg_attendance_reconcile.service.scheduled"]}

permission_query_conditions = {
    "thinkNXG Attendance Reconciliation": "thinknxg_attendance_reconcile.permissions.reconciliation_query",
}
has_permission = {
    "thinkNXG Attendance Reconciliation": "thinknxg_attendance_reconcile.permissions.reconciliation_permission",
}
