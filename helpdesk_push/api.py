import frappe
import requests
from frappe.utils import now


@frappe.whitelist(allow_guest=True)
def register_device(device_token):
    agent = _verified_agent()
    if not agent:
        frappe.throw("Could not verify agent", frappe.AuthenticationError)

    existing = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if existing:
        frappe.db.set_value("HD Push Device", existing, {"agent": agent, "last_seen": now()})
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


@frappe.whitelist(allow_guest=True)
def unregister_device(device_token):
    name = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if name:
        frappe.delete_doc("HD Push Device", name, ignore_permissions=True, force=True)
    return {"success": True}


def _verified_agent():
    token = frappe.get_request_header("X-Remote-Token")
    site = frappe.conf.get("poll_site_url")
    if not (token and site):
        return None
    try:
        resp = requests.get(
            f"{site.rstrip('/')}/api/method/frappe.auth.get_logged_user",
            headers={"Authorization": token},
            timeout=10,
        )
    except requests.RequestException:
        return None
    if resp.status_code == 200:
        return resp.json().get("message")
    return None
