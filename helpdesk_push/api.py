import frappe
from frappe.utils import now


@frappe.whitelist()
def register_device(device_token, agent_email=None):
    agent = agent_email or frappe.session.user
    existing = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if existing:
        frappe.db.set_value(
            "HD Push Device", existing, {"agent": agent, "last_seen": now()}
        )
    else:
        frappe.get_doc(
            {
                "doctype": "HD Push Device",
                "device_token": device_token,
                "agent": agent,
                "last_seen": now(),
            }
        ).insert(ignore_permissions=True)
    return {"success": True, "agent": agent}


@frappe.whitelist()
def unregister_device(device_token, agent_email=None):
    name = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if name:
        frappe.delete_doc("HD Push Device", name, ignore_permissions=True, force=True)
    return {"success": True}
