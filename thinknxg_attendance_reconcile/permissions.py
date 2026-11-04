"""Run documents: System Manager sees all; others see the runs they created."""

import frappe

from thinknxg_attendance_reconcile.constants import RECONCILIATION


def reconciliation_query(user=None):
    user = user or frappe.session.user
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return ""
    return f"`tab{RECONCILIATION}`.`owner` = {frappe.db.escape(user)}"


def reconciliation_permission(doc, user=None, permission_type=None):
    user = user or frappe.session.user
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return True
    if doc.is_new():
        return True
    return doc.owner == user
