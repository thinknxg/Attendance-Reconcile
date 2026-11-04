import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def setup():
    import hrms
    if int(frappe.__version__.split(".")[0]) not in (15, 16):
        frappe.throw("Attendance Reconciliation supports Frappe 15 and 16 only")
    if int(hrms.__version__.split(".")[0]) not in (15, 16):
        frappe.throw("Attendance Reconciliation supports HRMS 15 and 16 only")
    if frappe.__version__.split(".")[0] != hrms.__version__.split(".")[0]:
        frappe.throw("Frappe and HRMS must use matching major versions")
    create_custom_fields({
        "Attendance": [
            {"fieldname": "custom_ar_auto_generated", "label": "Created by Auto Attendance",
             "fieldtype": "Check", "read_only": 1, "no_copy": 1, "insert_after": "status"},
            {"fieldname": "custom_ar_approved", "label": "Approve Absence Reconciliation",
             "description": "Allow this absence to be cancelled and replaced using valid check-ins.",
             "fieldtype": "Check", "allow_on_submit": 1, "permlevel": 1, "no_copy": 1},
        ],
        "Employee Checkin": [
            {"fieldname": "custom_ar_reset_skip_approved", "label": "Approve Reconciliation of Skipped Log",
             "description": "Allow reconciliation to clear Skip Auto Attendance for this log only.",
             "fieldtype": "Check", "permlevel": 1, "no_copy": 1,
             "insert_after": "skip_auto_attendance"},
        ],
    }, update=True)
    # Custom fields at level 1 need explicit HR permissions on their parent DocTypes.
    from frappe.permissions import add_permission, update_permission_property
    for dt in ("Attendance", "Employee Checkin"):
        for role in ("HR Manager", "System Manager"):
            add_permission(dt, role, permlevel=1)
            for prop in ("read", "write"):
                update_permission_property(dt, role, 1, prop, 1)
