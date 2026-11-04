# Copyright (c) 2026, thinkNXG and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class thinkNXGAttendanceReconcileSettings(Document):
    def validate(self):
        if not 1 <= cint(self.lookback_days) <= 92:
            frappe.throw(_("Lookback Days must be between 1 and 92."))
        if not 0 <= cint(self.sync_grace_minutes) <= 1440:
            frappe.throw(_("Grace Minutes must be between 0 and 1440."))
        if not 1 <= cint(self.max_records_per_run) <= 5000:
            frappe.throw(_("Max Absences per Manual Preview must be between 1 and 5000."))
