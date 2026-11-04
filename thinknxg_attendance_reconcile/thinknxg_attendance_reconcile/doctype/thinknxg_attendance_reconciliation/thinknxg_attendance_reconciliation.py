# Copyright (c) 2026, thinkNXG and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from thinknxg_attendance_reconcile import compat, service
from thinknxg_attendance_reconcile.constants import (
    ACTION_ERROR,
    ACTION_NO_CHANGE,
    ACTION_REPLACE,
    ACTION_REVIEW,
    ACTION_WAIT,
    HR_ROLES,
    RESULT_CORRECTED,
)

MAX_RANGE_DAYS = 93
ACTIVE = ("Queued", "In Progress")


class thinkNXGAttendanceReconciliation(Document):
    def validate(self):
        if getdate(self.from_date) > getdate(self.to_date):
            frappe.throw(_("From Date cannot be after To Date."))
        if getdate(self.to_date) > getdate(nowdate()):
            frappe.throw(_("To Date cannot be in the future."))
        if (
            self.run_type != "Scheduled"
            and (getdate(self.to_date) - getdate(self.from_date)).days >= MAX_RANGE_DAYS
        ):
            frappe.throw(_("Select a range of at most {0} days.").format(MAX_RANGE_DAYS))
        if self.is_new():
            self.requested_by = frappe.session.user
        if (
            self.has_value_changed("from_date")
            or self.has_value_changed("to_date")
            or any(
                self.has_value_changed(f)
                for f in ("company", "employee", "department", "shift_type", "status_filter")
            )
        ):
            if not self.is_new() and self.status not in ACTIVE:
                # Filters changed: the stored preview no longer matches them.
                self.set("items", [])
                self.status = "Draft"
                self.update_counts()

    def on_trash(self):
        if self.status in ACTIVE:
            frappe.throw(_("Cannot delete a run while it is queued or in progress."))

    def update_counts(self):
        actions = [row.proposed_action for row in self.items]
        self.total_records = len(self.items)
        self.eligible_count = actions.count(ACTION_REPLACE)
        self.review_count = actions.count(ACTION_REVIEW)
        self.waiting_count = actions.count(ACTION_WAIT)
        self.no_change_count = actions.count(ACTION_NO_CHANGE)
        self.error_count = actions.count(ACTION_ERROR) + sum(
            1 for r in self.items if r.result == ACTION_ERROR
        )
        self.corrected_count = sum(1 for r in self.items if r.result == RESULT_CORRECTED)

    @frappe.whitelist(methods=["POST"])
    def preview(self):
        return self._enqueue("preview")

    @frappe.whitelist(methods=["POST"])
    def reconcile_selected(self):
        return self._enqueue("selected")

    @frappe.whitelist(methods=["POST"])
    def reconcile_all_eligible(self):
        return self._enqueue("all")

    def _enqueue(self, action):
        frappe.only_for(HR_ROLES)
        self.check_permission("write")
        if self.run_type == "Scheduled":
            frappe.throw(_("Scheduled runs are read-only. Create a new reconciliation."))
        current = frappe.db.get_value(
            self.doctype, self.name, ["status", "job_id"], as_dict=True, for_update=True
        )
        if current and current.status in ACTIVE and current.job_id and self._job_active(current.job_id):
            frappe.throw(_("This run is already queued or in progress."))
        if action != "preview" and current.status not in ("Previewed", "Reconciled"):
            frappe.throw(_("Run Preview before reconciling."))
        if action == "selected" and not any(
            r.selected and r.proposed_action == ACTION_REPLACE for r in self.items
        ):
            frappe.throw(_("Select at least one eligible row."))

        self.job_id = f"thinknxg-attendance-reconcile::{self.name}"
        self.status = "Queued"
        self.save()  # persists the user's row selection
        frappe.enqueue(
            "thinknxg_attendance_reconcile.service.run_job",
            queue="long",
            timeout=service.JOB_TIMEOUT,
            job_id=self.job_id,
            deduplicate=True,
            enqueue_after_commit=True,
            now=compat.in_test(),
            name=self.name,
            action=action,
            user=frappe.session.user,
        )
        return _("Queued. This page refreshes when the background job finishes.")

    @staticmethod
    def _job_active(job_id):
        try:
            from frappe.utils.background_jobs import is_job_enqueued

            return is_job_enqueued(job_id)
        except Exception:
            return True
