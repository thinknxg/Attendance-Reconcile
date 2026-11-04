"""Stamp future auto-attendance without guessing from owner or comments."""
import frappe
from hrms.hr.doctype.shift_type.shift_type import ShiftType


class ReconciliationShiftType(ShiftType):
    @frappe.whitelist()
    def process_auto_attendance(self, *args, **kwargs):
        # Earlier v15 releases process inline; newer releases delegate to _process.
        previous = frappe.flags.ar_auto_attendance
        frappe.flags.ar_auto_attendance = True
        try:
            return super().process_auto_attendance(*args, **kwargs)
        finally:
            frappe.flags.ar_auto_attendance = previous

    def _process(self, logs):
        previous = frappe.flags.ar_auto_attendance
        frappe.flags.ar_auto_attendance = True
        try:
            return super()._process(logs)
        finally:
            frappe.flags.ar_auto_attendance = previous


def stamp_origin(doc, method=None):
    doc.custom_ar_auto_generated = int(bool(frappe.flags.ar_auto_attendance))
