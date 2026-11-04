app_name = "attendance_reconciliation"
app_title = "Attendance Reconciliation"
app_publisher = "Attendance Reconciliation Contributors"
app_description = "Reconcile absences after delayed shift assignment or biometric sync"
app_email = ""
app_license = "MIT"
required_apps = ["frappe", "erpnext", "hrms"]
after_install = "attendance_reconciliation.install.setup"
after_migrate = "attendance_reconciliation.install.setup"
override_doctype_class = {
    "Shift Type": "attendance_reconciliation.events.ReconciliationShiftType"
}
doc_events = {
    "Attendance": {"before_submit": "attendance_reconciliation.events.stamp_origin"}
}
scheduler_events = {"hourly": ["attendance_reconciliation.api.scheduled"]}
doctype_js = {"Attendance Reconciliation Settings": "public/js/settings.js"}
permission_query_conditions = {
    "Attendance Reconciliation Run": "attendance_reconciliation.api.run_query"
}
has_permission = {
    "Attendance Reconciliation Run": "attendance_reconciliation.api.run_permission"
}
