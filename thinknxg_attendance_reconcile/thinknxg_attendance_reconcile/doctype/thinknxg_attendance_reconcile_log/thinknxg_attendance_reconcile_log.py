# Copyright (c) 2026, thinkNXG and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class thinkNXGAttendanceReconcileLog(Document):
    """Immutable audit record written by the reconciliation service only."""

    def before_insert(self):
        if not self.flags.ignore_permissions:
            frappe.throw(_("Reconciliation audit records are created by the service only."))

    def validate(self):
        if not self.is_new():
            frappe.throw(_("Reconciliation audit records are immutable."))

    def on_trash(self):
        frappe.throw(_("Reconciliation audit records cannot be deleted."))
