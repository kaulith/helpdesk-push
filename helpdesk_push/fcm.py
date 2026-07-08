import json
import time

import frappe
import jwt
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

TITLES = {
    "new_assignment": "New ticket assigned",
    "customer_reply": "Customer replied",
}


def notify_agent(agent, ticket, event):
    devices = frappe.get_all("HD Push Device", filters={"agent": agent}, fields=["name", "device_token"])
    if not devices:
        return

    subject = frappe.db.get_value("HD Ticket", ticket, "subject") or ""
    title = f"{TITLES.get(event, 'Helpdesk')} · #{ticket}"
    data = {"ticketId": str(ticket), "type": event}

    for device in devices:
        if not device.device_token:
            continue
        result = _send(device.device_token, title, subject, data)
        if result is False:
            frappe.delete_doc("HD Push Device", device.name, ignore_permissions=True, force=True)


def _send(token, title, body, data):
    sa = _service_account()
    access_token = _access_token(sa)
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in data.items()},
            "android": {"priority": "high"},
        }
    }
    resp = requests.post(
        FCM_URL.format(project_id=sa["project_id"]),
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if resp.status_code == 200:
        return True

    status = None
    try:
        status = resp.json().get("error", {}).get("status")
    except Exception:
        pass
    if resp.status_code == 404 or status in ("NOT_FOUND", "UNREGISTERED"):
        return False

    frappe.log_error(f"FCM send failed {resp.status_code}: {resp.text}", "helpdesk_push")
    return None


def _access_token(sa):
    cache = frappe.cache()
    cached = cache.get_value("fcm_access_token")
    if cached:
        return cached

    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": sa["client_email"],
            "scope": SCOPE,
            "aud": sa.get("token_uri", TOKEN_URL),
            "iat": now,
            "exp": now + 3600,
        },
        sa["private_key"],
        algorithm="RS256",
    )
    resp = requests.post(
        sa.get("token_uri", TOKEN_URL),
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()
    cache.set_value(
        "fcm_access_token",
        token["access_token"],
        expires_in_sec=token.get("expires_in", 3600) - 120,
    )
    return token["access_token"]


def _service_account():
    sa = frappe.conf.get("fcm_service_account")
    if isinstance(sa, str):
        sa = json.loads(sa)
    if not sa:
        path = frappe.conf.get("fcm_service_account_path")
        if path:
            with open(path) as f:
                sa = json.load(f)
    if not sa:
        frappe.throw("fcm_service_account not configured in site_config")
    return sa
