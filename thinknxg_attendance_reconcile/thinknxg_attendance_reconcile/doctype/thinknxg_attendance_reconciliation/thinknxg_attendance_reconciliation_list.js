// Copyright (c) 2026, thinkNXG and contributors
// For license information, please see license.txt

frappe.listview_settings["thinkNXG Attendance Reconciliation"] = {
	get_indicator(doc) {
		const colors = { Draft: "gray", Queued: "blue", "In Progress": "blue", Previewed: "orange", Reconciled: "green", Failed: "red" };
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
