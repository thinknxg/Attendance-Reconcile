import frappe
from frappe.model.document import Document


class AttendanceReconciliationSettings(Document):
    def validate(self):
        if not 1 <= int(self.lookback_days or 0) <= 92:
            frappe.throw("Lookback Days must be between 1 and 92")
        if not 0 <= int(self.sync_grace_minutes or 0) <= 1440:
            frappe.throw("Sync Grace Minutes must be between 0 and 1440")
