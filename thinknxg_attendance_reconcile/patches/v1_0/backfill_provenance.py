"""Stamp historical records with the markers thinkNXG HR already wrote.

* Attendance carrying an HRMS auto-attendance Comment -> tnxg_ar_auto_generated.
* Skipped Employee Checkin carrying the HRMS "Reason for skipping auto attendance"
  Comment -> tnxg_ar_skip_origin (+ conflicting attendance when the error links one).

Only marker comments are read; nothing is inferred from ownership. Idempotent.
"""

import frappe

from thinknxg_attendance_reconcile import provenance
from thinknxg_attendance_reconcile.constants import (
    F_AUTO_GENERATED,
    F_CONFLICT_ATTENDANCE,
    F_SKIP_ORIGIN,
    SKIP_CONFLICT,
    SKIP_VALIDATION,
)


def execute():
    from thinknxg_attendance_reconcile.install import create_fields

    create_fields()
    Comment = frappe.qb.DocType("Comment")
    Attendance = frappe.qb.DocType("Attendance")
    rows = (
        frappe.qb.from_(Comment)
        .join(Attendance)
        .on(Attendance.name == Comment.reference_name)
        .select(Comment.reference_name, Comment.content)
        .where(
            (Comment.reference_doctype == "Attendance")
            & (Comment.comment_type == "Comment")
            & (Attendance.status == "Absent")
            & (Attendance.docstatus == 1)
            & (Attendance[F_AUTO_GENERATED] == 0)
        )
    ).run(as_dict=True)
    names = sorted({r.reference_name for r in rows if provenance.is_auto_absent_comment(r.content)})
    for chunk in (names[i : i + 500] for i in range(0, len(names), 500)):
        frappe.db.set_value("Attendance", {"name": ["in", chunk]}, F_AUTO_GENERATED, 1, update_modified=False)

    Checkin = frappe.qb.DocType("Employee Checkin")
    rows = (
        frappe.qb.from_(Comment)
        .join(Checkin)
        .on(Checkin.name == Comment.reference_name)
        .select(Comment.reference_name, Comment.content, Comment.creation)
        .where(
            (Comment.reference_doctype == "Employee Checkin")
            & (Comment.comment_type == "Comment")
            & (Checkin.skip_auto_attendance == 1)
            & (Checkin[F_SKIP_ORIGIN].isnull() | (Checkin[F_SKIP_ORIGIN] == ""))
        )
        .orderby(Comment.creation)
    ).run(as_dict=True)
    latest = {}
    for r in rows:  # ordered by creation: the last skip comment wins
        if provenance.is_skip_comment(r.content):
            latest[r.reference_name] = sorted(provenance.attendance_links(r.content))
    for name, links in latest.items():
        frappe.db.set_value(
            "Employee Checkin",
            name,
            {
                F_SKIP_ORIGIN: SKIP_CONFLICT if links else SKIP_VALIDATION,
                F_CONFLICT_ATTENDANCE: links[0] if links else None,
            },
            update_modified=False,
        )
