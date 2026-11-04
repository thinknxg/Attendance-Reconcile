"""Carry data over from the pre-rebrand ``attendance_reconciliation`` app.

Copies its custom-field values (custom_ar_*) into the tnxg_ar_* fields and its
Attendance Reconciliation Log rows into thinkNXG Attendance Reconcile Log. Safe to
run repeatedly; does nothing on sites that never had the legacy app.
"""

import frappe

from thinknxg_attendance_reconcile.constants import AUDIT_LOG, F_RECONCILED_FROM, LEGACY_FIELDS, LEGACY_LOG


def execute():
    from thinknxg_attendance_reconcile.install import create_fields

    create_fields()
    for doctype, mapping in LEGACY_FIELDS.items():
        columns = set(frappe.db.get_table_columns(doctype))
        for old, new in mapping.items():
            if old not in columns:
                continue
            names = frappe.get_all(doctype, filters={old: 1, new: 0}, pluck="name")
            for chunk in (names[i : i + 500] for i in range(0, len(names), 500)):
                frappe.db.set_value(doctype, {"name": ["in", chunk]}, new, 1, update_modified=False)

    if not frappe.db.table_exists(LEGACY_LOG):
        return
    migrated = "\n".join(
        frappe.get_all(AUDIT_LOG, filters={"details": ["like", '%"legacy_log"%']}, pluck="details")
    )
    for row in frappe.get_all(LEGACY_LOG, fields=["*"], order_by="creation asc"):
        if f'"legacy_log": "{row.name}"' in migrated:
            continue
        new_status = (
            frappe.db.get_value("Attendance", row.replacement_attendance, "status")
            if row.replacement_attendance
            else None
        )
        doc = frappe.get_doc(
            {
                "doctype": AUDIT_LOG,
                "processing_status": "Corrected",
                "employee": row.employee,
                "attendance_date": row.attendance_date,
                "previous_attendance": row.previous_attendance,
                "original_status": "Absent",
                "corrected_attendance": row.replacement_attendance,
                "new_status": row.result or new_status,
                "executed_by": row.owner,
                "executed_on": row.creation,
                "run_type": "Manual",
                "reason": "Migrated from legacy Attendance Reconciliation Log " + row.name,
                "details": frappe.as_json({"legacy_log": row.name, "legacy_details": row.details}),
            }
        )
        doc.insert(ignore_permissions=True, ignore_links=True)
        if row.replacement_attendance and row.previous_attendance:
            frappe.db.set_value(
                "Attendance",
                row.replacement_attendance,
                F_RECONCILED_FROM,
                row.previous_attendance,
                update_modified=False,
            )
