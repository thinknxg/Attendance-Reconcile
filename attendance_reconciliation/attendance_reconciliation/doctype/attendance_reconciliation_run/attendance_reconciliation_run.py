import frappe
from frappe.model.document import Document


class AttendanceReconciliationRun(Document):
    def before_insert(self):
        if not self.flags.ignore_permissions:
            frappe.throw("Reconciliation audit records are created by the service only")

    def validate(self):
        if not self.is_new():
            frappe.throw("Reconciliation audit records are immutable")

    def on_trash(self):
        frappe.throw("Reconciliation audit records cannot be deleted")
