frappe.ui.form.on('Attendance Reconciliation Settings', {
  refresh(frm) {
    const dialog = (apply) => {
      const d = new frappe.ui.Dialog({
        title: apply ? __('Run Attendance Reconciliation') : __('Preview Attendance Reconciliation'),
        fields: [
          {fieldname: 'from_date', label: __('From Date'), fieldtype: 'Date', reqd: 1,
           default: frappe.datetime.add_days(frappe.datetime.get_today(), -30)},
          {fieldname: 'to_date', label: __('To Date'), fieldtype: 'Date', reqd: 1,
           default: frappe.datetime.get_today()},
          {fieldname: 'employee', label: __('Employee'), fieldtype: 'Link', options: 'Employee'}
        ],
        primary_action_label: apply ? __('Queue Corrections') : __('Preview'),
        primary_action(values) {
          if (frm.is_dirty()) {
            frappe.msgprint(__('Save settings before continuing.')); return;
          }
          frappe.call({
            method: 'attendance_reconciliation.api.' + (apply ? 'run' : 'preview'),
            args: values, freeze: true,
            callback(r) {
              d.hide();
              if (apply) {
                frappe.msgprint(__('Job queued. Open Attendance Reconciliation Run for the result. Job: {0}',
                  [frappe.utils.escape_html(r.message.job_id)]));
              } else {
                const rows = r.message.rows;
                const esc = value => frappe.utils.escape_html(String(value ?? ''));
                const body = rows.map(x => '<tr>' +
                  [x.attendance, x.action, x.status, x.working_hours, x.reason || x.error_log]
                    .map(v => '<td>' + esc(v) + '</td>').join('') + '</tr>').join('');
                frappe.msgprint({title: __('Reconciliation Preview'), wide: true,
                  message: '<p>' + __('Run recalculates and revalidates all records before applying changes.') +
                    '</p><table class="table table-bordered"><thead><tr>' +
                    ['Attendance','Action','Expected status','Hours','Reason / error'].map(v => '<th>' + __(v) + '</th>').join('') +
                    '</tr></thead><tbody>' + body + '</tbody></table>' +
                    (r.message.truncated ? '<p>' + __('More than 200 absences found. Narrow the date range or employee filter.') + '</p>' : '')});
              }
            }
          });
        }
      });
      d.show();
    };
    frm.add_custom_button(__('Preview Reconciliation'), () => dialog(false));
    frm.add_custom_button(__('Run Reconciliation'), () => dialog(true));
    frm.add_custom_button(__('Run History'), () => frappe.set_route('List', 'Attendance Reconciliation Run'));
  }
});
