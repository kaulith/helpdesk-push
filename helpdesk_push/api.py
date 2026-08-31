from urllib.parse import quote

import frappe
import requests
from frappe.rate_limiter import rate_limit
from frappe.utils import now

OAUTH_APP_NAME = "Helpdesk Mobile"
OAUTH_REDIRECT_URI = "helpdesk://oauth/callback"


# OAuth client ids are public by design, and the app has to read this one before
# it can sign anybody in, so there is nobody to authenticate yet.
@frappe.whitelist(allow_guest=True)
@rate_limit(limit=20, seconds=60)
def get_oauth_client_id():
    return {"client_id": _mobile_oauth_client()}


@frappe.whitelist(methods=["POST"])
def create_oauth_client():
    frappe.only_for("System Manager")

    name = _mobile_oauth_client()
    client = frappe.get_doc("OAuth Client", name) if name else frappe.new_doc("OAuth Client")
    client.app_name = OAUTH_APP_NAME
    client.scopes = "all openid"
    client.redirect_uris = OAUTH_REDIRECT_URI
    client.default_redirect_uri = OAUTH_REDIRECT_URI
    client.grant_type = "Authorization Code"
    client.response_type = "Code"
    # The app ships no client secret, so the token exchange is carried by PKCE.
    client.token_endpoint_auth_method = "None"
    client.allowed_roles = []
    client.append("allowed_roles", {"role": "Desk User"})
    client.save()

    return {"client_id": client.name}


def _mobile_oauth_client():
    return frappe.db.get_value("OAuth Client", {"app_name": OAUTH_APP_NAME}, "name")


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=20, seconds=60)
def register_device(device_token, agent_email):
    caller = _caller_email()
    if not caller or not _may_register_devices(caller):
        frappe.throw("Unauthorized", frappe.AuthenticationError)
    if not _is_agent(agent_email):
        frappe.throw("Unknown agent", frappe.ValidationError)

    existing = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if existing:
        frappe.db.set_value("HD Push Device", existing, {"agent": agent_email, "last_seen": now()})
    else:
        frappe.get_doc(
            {
                "doctype": "HD Push Device",
                "device_token": device_token,
                "agent": agent_email,
                "last_seen": now(),
            }
        ).insert(ignore_permissions=True)
    return {"success": True, "agent": agent_email}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=20, seconds=60)
def unregister_device(device_token):
    caller = _caller_email()
    if not caller or not _may_register_devices(caller):
        frappe.throw("Unauthorized", frappe.AuthenticationError)

    name = frappe.db.exists("HD Push Device", {"device_token": device_token})
    if name:
        frappe.delete_doc("HD Push Device", name, ignore_permissions=True, force=True)
    return {"success": True}


# The app sends X-Remote-Token only when its Helpdesk site is a different bench
# from this one; on a shared bench it authenticates here directly, so its key
# never leaves the site that issued it.
def _caller_email():
    if not _remote_token():
        return None if frappe.session.user in (None, "Guest") else frappe.session.user
    resp = _remote_get("api/method/frappe.auth.get_logged_user")
    if not resp or resp.status_code != 200:
        return None
    return resp.json().get("message") or None


# Membership, not identity: the app registers the device for whichever agent it
# watches, so the caller is often not agent_email. Customers hold Helpdesk logins
# too, so without this they could point an agent's notifications at their device.
def _may_register_devices(email):
    return _is_agent(email) or "System Manager" in _roles(email)


def _is_agent(email):
    if not _remote_token():
        return bool(frappe.db.exists("HD Agent", email))
    resp = _remote_get(f"api/resource/HD%20Agent/{quote(email, safe='')}")
    return bool(resp) and resp.status_code == 200


def _roles(email):
    if not _remote_token():
        return frappe.get_roles(email)
    resp = _remote_get(f"api/resource/User/{quote(email, safe='')}")
    if not resp or resp.status_code != 200:
        return []
    return [role.get("role") for role in resp.json().get("data", {}).get("roles") or []]


def _remote_token():
    return frappe.get_request_header("X-Remote-Token")


def _remote_get(path):
    token = _remote_token()
    site = frappe.conf.get("poll_site_url")
    if not (token and site):
        return None
    try:
        return requests.get(
            f"{site.rstrip('/')}/{path}",
            headers={"Authorization": token},
            timeout=10,
        )
    except requests.RequestException:
        return None
