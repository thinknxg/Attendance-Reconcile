"""Establish where an Absent attendance and a skipped check-in came from.

HRMS leaves deterministic markers that this module relies on (verified in v15 & v16):

* ShiftType.mark_absent_for_dates_with_no_attendance submits the Absent and adds
  the Comment "Employee was marked Absent due to missing Employee Checkins."
* mark_attendance_and_link_log adds "Employee was marked Absent for not meeting
  the working hours threshold." to threshold absences.
* handle_attendance_exception sets skip_auto_attendance on the logs and adds the
  Comment "<strong>Reason for skipping auto attendance:</strong><br>{error}". For
  DuplicateAttendanceError / OverlappingShiftAttendanceError the error contains a
  get_link_to_form() link to the conflicting Attendance (/app/... v15, /desk/... v16).

Markers are stamped onto custom fields as HRMS writes them (events.on_comment_insert)
and backfilled for history (patches). Anything without a marker is treated as
manual/unknown and goes to HR review. Ownership is never used as evidence.
"""

import re
from urllib.parse import unquote

import frappe

from thinknxg_attendance_reconcile.constants import (
    F_APPROVED,
    F_AUTO_GENERATED,
    F_CONFLICT_ATTENDANCE,
    F_SKIP_APPROVED,
    F_SKIP_ORIGIN,
    SKIP_CONFLICT,
    SKIP_INTENTIONAL,
    SKIP_VALIDATION,
)

AUTO_ABSENT_MESSAGES = (
    "Employee was marked Absent due to missing Employee Checkins.",
    "Employee was marked Absent for not meeting the working hours threshold.",
)
SKIP_REASON_PREFIX = "Reason for skipping auto attendance:"

_ATTENDANCE_LINK = re.compile(r"/(?:app|desk)/attendance/([^\"'<>?#\s]+)")


def _languages():
    key = "tnxg_ar_languages"
    if key not in frappe.local.cache:
        langs = {"en", frappe.db.get_single_value("System Settings", "language") or "en"}
        langs.update(
            frappe.get_all(
                "User",
                filters={"enabled": 1, "user_type": "System User", "language": ["is", "set"]},
                pluck="language",
                distinct=True,
            )
        )
        frappe.local.cache[key] = sorted(langs)
    return frappe.local.cache[key]


def variants(message):
    """The message as HRMS could have written it: English plus languages in use."""
    out = {message}
    for lang in _languages():
        try:
            out.add(frappe._(message, lang=lang))
        except Exception:
            pass
    return out


def _strip(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()


def is_auto_absent_comment(content):
    text = _strip(content)
    return any(text == v for message in AUTO_ABSENT_MESSAGES for v in variants(message))


def is_skip_comment(content):
    text = _strip(content)
    return any(text.startswith(v) for v in variants(SKIP_REASON_PREFIX))


def attendance_links(content):
    return {unquote(m) for m in _ATTENDANCE_LINK.findall(content or "")}


def _comments(doctype, name):
    return frappe.get_all(
        "Comment",
        filters={"reference_doctype": doctype, "reference_name": name, "comment_type": "Comment"},
        fields=["content", "creation"],
        order_by="creation desc",
    )


# ---------------------------------------------------------------- Attendance


def absence_origin(attendance):
    """Return 'HR Approved', 'Auto Attendance' or None (origin not established)."""
    if attendance.get(F_APPROVED):
        return "HR Approved"
    if attendance.get("amended_from"):
        return None  # an amended record is a manual decision
    if attendance.get(F_AUTO_GENERATED):
        return "Auto Attendance"
    if any(is_auto_absent_comment(c.content) for c in _comments("Attendance", attendance.name)):
        return "Auto Attendance"
    return None


# ---------------------------------------------------------- Employee Checkin


def classify_skip(log):
    """Return (origin, conflicting attendance names) for a skipped check-in.

    The stamped field wins; otherwise the latest HRMS skip comment is parsed.
    """
    origin = log.get(F_SKIP_ORIGIN)
    if origin == SKIP_INTENTIONAL:
        return SKIP_INTENTIONAL, set()
    if origin == SKIP_CONFLICT and log.get(F_CONFLICT_ATTENDANCE):
        return SKIP_CONFLICT, {log.get(F_CONFLICT_ATTENDANCE)}
    for comment in _comments("Employee Checkin", log.name):
        if is_skip_comment(comment.content):
            names = attendance_links(comment.content)
            return (SKIP_CONFLICT if names else SKIP_VALIDATION), names
    return origin or None, set()


def skip_decision(log, target, allow_conflict_reset=True):
    """Decide whether a skipped log may participate in reconciling ``target``.

    Returns (allowed, origin label for audit). A conflict skip is correctable only
    when every attendance HRMS collided with is the absence being replaced or is
    already cancelled; the stamped/commented origin proves HRMS made the skip.
    """
    if not log.get("skip_auto_attendance"):
        return True, ""
    if log.get(F_SKIP_APPROVED):
        return True, "HR Approved"
    origin, names = classify_skip(log)
    if origin == SKIP_CONFLICT and names and allow_conflict_reset:
        others = names - {target}
        if not others or all(frappe.db.get_value("Attendance", n, "docstatus") in (2, None) for n in others):
            return True, SKIP_CONFLICT
    return False, origin or "Unknown"


def stamp_from_comment(comment):
    """Persist HRMS markers as they are written (Comment after_insert hook)."""
    if comment.comment_type != "Comment":
        return
    if comment.reference_doctype == "Attendance" and is_auto_absent_comment(comment.content):
        frappe.db.set_value("Attendance", comment.reference_name, F_AUTO_GENERATED, 1, update_modified=False)
    elif comment.reference_doctype == "Employee Checkin" and is_skip_comment(comment.content):
        current = frappe.db.get_value("Employee Checkin", comment.reference_name, F_SKIP_ORIGIN)
        if current == SKIP_INTENTIONAL:
            return
        names = sorted(attendance_links(comment.content))
        frappe.db.set_value(
            "Employee Checkin",
            comment.reference_name,
            {
                F_SKIP_ORIGIN: SKIP_CONFLICT if names else SKIP_VALIDATION,
                F_CONFLICT_ATTENDANCE: names[0] if names else None,
            },
            update_modified=False,
        )
