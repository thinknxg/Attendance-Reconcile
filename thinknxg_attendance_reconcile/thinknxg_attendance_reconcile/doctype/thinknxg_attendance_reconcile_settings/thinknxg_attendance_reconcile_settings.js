// Copyright (c) 2026, thinkNXG and contributors
// For license information, please see license.txt

frappe.ui.form.on("thinkNXG Attendance Reconcile Settings", {
	refresh(frm) {
		frm.add_custom_button(__("New Reconciliation"), () => frappe.new_doc("thinkNXG Attendance Reconciliation"));
		frm.add_custom_button(__("Reconciliation Runs"), () => frappe.set_route("List", "thinkNXG Attendance Reconciliation"));
		frm.add_custom_button(__("Reconciliation History"), () => frappe.set_route("List", "thinkNXG Attendance Reconcile Log"));
	},
});
