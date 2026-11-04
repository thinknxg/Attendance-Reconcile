from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from thinknxg_attendance_reconcile import compat
from thinknxg_attendance_reconcile.constants import (
    F_APPROVED,
    F_AUTO_GENERATED,
    F_CONFLICT_ATTENDANCE,
    F_RECONCILED_FROM,
    F_SKIP_APPROVED,
    F_SKIP_ORIGIN,
    SKIP_CONFLICT,
    SKIP_INTENTIONAL,
    SKIP_VALIDATION,
)

CUSTOM_FIELDS = {
    "Attendance": [
        {
            "fieldname": "tnxg_ar_section",
            "label": "thinkNXG Reconciliation",
            "fieldtype": "Section Break",
            "insert_after": "amended_from",
            "collapsible": 1,
        },
        {
            "fieldname": F_AUTO_GENERATED,
            "label": "Created by Auto Attendance",
            "fieldtype": "Check",
            "read_only": 1,
            "no_copy": 1,
            "allow_on_submit": 1,
            "insert_after": "tnxg_ar_section",
            "description": "Set from the marker thinkNXG HR Auto Attendance writes when it marks an absence.",
        },
        {
            "fieldname": F_APPROVED,
            "label": "Approve Absence Reconciliation",
            "fieldtype": "Check",
            "allow_on_submit": 1,
            "no_copy": 1,
            "insert_after": F_AUTO_GENERATED,
            "description": "HR Manager only. Allows this absence to be replaced from valid check-ins when its origin "
            "cannot be proven automatically (manual, amended or older records).",
        },
        {
            "fieldname": F_RECONCILED_FROM,
            "label": "Reconciled From",
            "fieldtype": "Link",
            "options": "Attendance",
            "read_only": 1,
            "no_copy": 1,
            "allow_on_submit": 1,
            "insert_after": F_APPROVED,
        },
    ],
    "Employee Checkin": [
        {
            "fieldname": F_SKIP_ORIGIN,
            "label": "Skip Reason",
            "fieldtype": "Select",
            "options": "\n".join(["", SKIP_CONFLICT, SKIP_VALIDATION, SKIP_INTENTIONAL]),
            "no_copy": 1,
            "insert_after": "skip_auto_attendance",
            "depends_on": "eval:doc.skip_auto_attendance",
            "description": "Recorded automatically when thinkNXG HR skips a log. Set 'Intentional' to keep the "
            "log excluded from reconciliation (HR Manager only).",
        },
        {
            "fieldname": F_CONFLICT_ATTENDANCE,
            "label": "Conflicting Attendance",
            "fieldtype": "Link",
            "options": "Attendance",
            "read_only": 1,
            "no_copy": 1,
            "insert_after": F_SKIP_ORIGIN,
            "depends_on": "eval:doc.skip_auto_attendance",
        },
        {
            "fieldname": F_SKIP_APPROVED,
            "label": "Approve Reconciliation of Skipped Log",
            "fieldtype": "Check",
            "no_copy": 1,
            "insert_after": F_CONFLICT_ATTENDANCE,
            "depends_on": "eval:doc.skip_auto_attendance",
            "description": "HR Manager only. Lets reconciliation clear Skip Auto Attendance for this log.",
        },
    ],
}


def before_install():
    compat.validate_versions()


def create_fields():
    create_custom_fields(CUSTOM_FIELDS, update=True)


def after_install():
    create_fields()
    from thinknxg_attendance_reconcile.patches.v1_0 import backfill_provenance, migrate_legacy_app

    migrate_legacy_app.execute()
    backfill_provenance.execute()


def after_migrate():
    create_fields()
