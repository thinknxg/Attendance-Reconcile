// Copyright (c) 2026, thinkNXG and contributors
// For license information, please see license.txt

const TNXG_ARC_ACTIVE = ["Queued", "In Progress"];

frappe.ui.form.on("thinkNXG Attendance Reconciliation", {
	onload(frm) {
		if (frm.is_new()) {
			frm.set_value("to_date", frm.doc.to_date || frappe.datetime.get_today());
			frm.set_value("from_date", frm.doc.from_date || frappe.datetime.add_days(frappe.datetime.get_today(), -30));
		}
		frm.set_query("employee", () => ({ filters: frm.doc.company ? { company: frm.doc.company } : {} }));
		frm.set_query("department", () => ({ filters: frm.doc.company ? { company: frm.doc.company } : {} }));
		frappe.realtime.on("tnxg_attendance_reconcile", (data) => {
			if (data && data.name === frm.doc.name) frm.reload_doc();
		});
	},

	refresh(frm) {
		const grid = frm.get_field("items").grid;
		grid.cannot_add_rows = true;
		frm.set_df_property("items", "cannot_delete_rows", 1);

		frm.add_custom_button(__("View Reconciliation History"), () =>
			frappe.set_route("List", "thinkNXG Attendance Reconcile Log", frm.doc.employee ? { employee: frm.doc.employee } : {})
		);
		if (frm.is_new() || frm.doc.run_type === "Scheduled") return;

		const busy = TNXG_ARC_ACTIVE.includes(frm.doc.status);
		if (busy) {
			frm.dashboard.set_headline(__("Background job {0}. This page refreshes when it finishes.", [frm.doc.status.toLowerCase()]), "blue");
			return;
		}
		frm.add_custom_button(__("Preview Reconciliation"), () => run(frm, "preview"));
		const pending = (frm.doc.items || []).some((r) => r.proposed_action === "Replace Attendance" && r.result === "Pending");
		if (["Previewed", "Reconciled"].includes(frm.doc.status) && pending) {
			frm.add_custom_button(__("Reconcile Selected"), () => run(frm, "reconcile_selected"), __("Reconcile"));
			frm.add_custom_button(__("Reconcile All Eligible"), () => run(frm, "reconcile_all_eligible"), __("Reconcile"));
		}
		if (frm.doc.truncated) {
			frm.dashboard.set_headline(__("More absences match than the per-run limit. Narrow the filters to see the rest."), "orange");
		}
	},
});

frappe.ui.form.on("thinkNXG Attendance Reconciliation Item", {
	form_render(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.proposed_action !== "Replace Attendance" || row.result !== "Pending") {
			frappe.model.set_value(cdt, cdn, "selected", 0);
		}
	},
	selected(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.selected && (row.proposed_action !== "Replace Attendance" || row.result !== "Pending")) {
			frappe.model.set_value(cdt, cdn, "selected", 0);
			frappe.show_alert({ message: __("Only eligible, pending rows can be selected."), indicator: "orange" });
		}
	},
});

function run(frm, method) {
	const go = () =>
		frm.call({ method, doc: frm.doc, freeze: true, freeze_message: __("Queuing...") }).then((r) => {
			if (r.message) frappe.show_alert({ message: r.message, indicator: "blue" });
			frm.reload_doc();
		});
	if (method === "preview") return frm.is_dirty() ? frm.save().then(go) : go();
	const count = method === "reconcile_all_eligible"
		? frm.doc.items.filter((r) => r.proposed_action === "Replace Attendance" && r.result === "Pending").length
		: frm.doc.items.filter((r) => r.selected && r.proposed_action === "Replace Attendance" && r.result === "Pending").length;
	frappe.confirm(
		__("Cancel {0} Absent attendance record(s) and create thinkNXG HR-calculated replacements? Every record is revalidated first and audited.", [count]),
		go
	);
}
