"""Previews are no longer capped by default: move sites still on the old 1000 default to 0 (no limit)."""

import frappe

from thinknxg_attendance_reconcile.constants import SETTINGS


def execute():
    if frappe.db.get_single_value(SETTINGS, "max_records_per_run") in (1000, "1000"):
        frappe.db.set_single_value(SETTINGS, "max_records_per_run", 0)
