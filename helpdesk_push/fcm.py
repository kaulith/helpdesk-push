import json
import time

import frappe
import jwt
import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
# The Firebase project belongs to the mobile app, so only its own bench holds the
# service account; every other site sends through that bench.
RELAY_URL = "https://helpdesk-mb.fsn.frappe.cloud/api/method/helpdesk_push.api.relay_push"

TITLES = {
    "new_assignment": "New ticket assigned",
    "customer_reply": "Customer replied",
    "unassignment": "Unassigned from ticket",
}


def notify_agent(agent, ticket, event_type):
    subject = frappe.db.get_value("HD Ticket", ticket, "subject") or ""
    title = f"{TITLES.get(event_type, 'Helpdesk')} #{ticket}"
    send_to_agent(agent, title, subject, {"ticketId": str(ticket), "type": event_type})


def send_to_agent(agent, title, body, data):
    devices = frappe.get_all("HD Push Device", filters={"agent": agent}, fields=["name", "device_token"])
    for device in devices:
        if not device.device_token:
            continue
        result = _send(device.device_token, title, body, data)
        if result is False:
            frappe.delete_doc("HD Push Device", device.name, ignore_permissions=True, force=True)


def _send(token, title, body, data):
    sa = service_account()
    if not sa:
        return _send_through_relay(token, title, body, data)
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

    error = {}
    try:
        error = resp.json().get("error", {})
    except Exception:
        pass
    # FCM names the token as the bad field when it is not one it ever issued.
    malformed_token = any(
        violation.get("field") == "message.token"
        for detail in error.get("details", [])
        for violation in detail.get("fieldViolations", [])
    )
    if resp.status_code == 404 or error.get("status") in ("NOT_FOUND", "UNREGISTERED") or malformed_token:
        return False

    frappe.log_error(f"FCM send failed {resp.status_code}: {resp.text}", "helpdesk_push")
    return None


def _send_through_relay(token, title, body, data):
    resp = requests.post(
        frappe.conf.get("push_relay_url") or RELAY_URL,
        json={"device_token": token, "title": title, "body": body, "data": data},
        timeout=10,
    )
    if resp.status_code != 200:
        frappe.log_error(f"Push relay failed {resp.status_code}: {resp.text}", "helpdesk_push")
        return None
    return resp.json().get("message", {}).get("delivered")


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


def service_account():
    sa = frappe.conf.get("fcm_service_account")
    if isinstance(sa, str):
        sa = json.loads(sa)
    if not sa:
        path = frappe.conf.get("fcm_service_account_path")
        if path:
            with open(path) as f:
                sa = json.load(f)
    return sa
