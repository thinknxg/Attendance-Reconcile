"""Document event handlers. No standard HRMS controller is overridden or patched."""

import frappe
from frappe import _

from thinknxg_attendance_reconcile import provenance
from thinknxg_attendance_reconcile.constants import (
    F_APPROVED,
    F_AUTO_GENERATED,
    F_CONFLICT_ATTENDANCE,
    F_RECONCILED_FROM,
    F_SKIP_APPROVED,
    F_SKIP_ORIGIN,
    HR_ROLES,
)


def on_comment_insert(doc, method=None):
    """Stamp HRMS auto-attendance / conflict-skip markers as HRMS writes them."""
    provenance.stamp_from_comment(doc)


def _is_hr_user():
    if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate:
        return True
    return frappe.session.user == "Administrator" or bool(set(HR_ROLES) & set(frappe.get_roles()))


def _changed(doc, fieldname):
    if doc.is_new():
        return bool(doc.get(fieldname))
    return doc.has_value_changed(fieldname)


def guard_attendance(doc, method=None):
    """Only HR may approve reconciliation; provenance fields are system-maintained."""
    if not doc.meta.has_field(F_APPROVED):
        return
    if _changed(doc, F_APPROVED) and not _is_hr_user():
        frappe.throw(_("Only an HR Manager can approve absence reconciliation."), frappe.PermissionError)
    for field in (F_AUTO_GENERATED, F_RECONCILED_FROM):
        if not doc.is_new() and doc.has_value_changed(field) and not frappe.flags.in_patch:
            frappe.throw(
                _("{0} is maintained by thinkNXG Attendance Reconcile.").format(doc.meta.get_label(field))
            )


def guard_checkin(doc, method=None):
    if not doc.meta.has_field(F_SKIP_APPROVED):
        return
    for field in (F_SKIP_APPROVED, F_SKIP_ORIGIN):
        if _changed(doc, field) and not _is_hr_user():
            frappe.throw(
                _("Only an HR Manager can change {0}.").format(doc.meta.get_label(field)),
                frappe.PermissionError,
            )
    if not doc.skip_auto_attendance and doc.get(F_SKIP_APPROVED):
        doc.set(F_SKIP_APPROVED, 0)  # nothing left to approve
    if _changed(doc, F_CONFLICT_ATTENDANCE) and not doc.is_new() and not frappe.flags.in_patch:
        frappe.throw(
            _("{0} is maintained by thinkNXG Attendance Reconcile.").format(
                doc.meta.get_label(F_CONFLICT_ATTENDANCE)
            )
        )
