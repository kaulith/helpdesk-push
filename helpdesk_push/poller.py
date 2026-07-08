import json
import re

import frappe
import requests

from helpdesk_push.fcm import send_to_agent

STATE = "HD Push Poll State"


def poll_all():
    conf = frappe.conf
    site = conf.get("poll_site_url")
    key = conf.get("poll_api_key")
    secret = conf.get("poll_api_secret")
    agent = conf.get("poll_agent_email")
    notify = conf.get("poll_notify_agent")
    if not (site and key and secret and agent and notify):
        return
    if not frappe.db.exists("HD Push Device", {"agent": notify}):
        return

    try:
        _run(_Remote(site, key, secret), agent, notify)
    except requests.RequestException:
        pass
    except Exception:
        frappe.log_error(frappe.get_traceback(), "helpdesk_push.poller")


def _run(client, agent, notify):
    assigned = client.assigned_ticket_ids(agent)
    tickets = client.tickets(assigned) if assigned else []
    comments = client.recent_comments()

    state = _load_state()
    if not state.get("primed"):
        _save_state(_snapshot(assigned, tickets, comments))
        return

    subjects = {t["name"]: (t.get("subject") or "") for t in tickets}

    seen_assignments = set(state.get("assignments", []))
    for ticket in assigned - seen_assignments:
        send_to_agent(
            notify,
            f"New ticket assigned · #{ticket}",
            subjects.get(ticket, ""),
            {"ticketId": ticket, "type": "new_assignment"},
        )

    prev_replies = state.get("replies", {})
    for ticket in tickets:
        name = ticket["name"]
        reply = ticket.get("last_customer_response") or ""
        if reply and prev_replies.get(name) != reply:
            send_to_agent(
                notify,
                f"Customer replied · #{name}",
                subjects.get(name, ""),
                {"ticketId": name, "type": "customer_reply"},
            )

    seen_comments = set(state.get("comment_ids", []))
    username = agent.split("@")[0]
    for comment in comments:
        name = comment.get("name")
        if not name or name in seen_comments or comment.get("commented_by") == agent:
            continue
        content = comment.get("content") or ""
        mentioned = agent.lower() in content.lower() or f"@{username}".lower() in content.lower()
        ticket = comment.get("reference_ticket")
        if not (mentioned or ticket in assigned):
            continue
        plain = re.sub(r"<[^>]*>", "", content).strip()
        title = f"You were mentioned · #{ticket}" if mentioned else f"New comment · #{ticket}"
        send_to_agent(notify, title, plain[:120], {"ticketId": ticket or "", "type": "new_comment"})

    _save_state(_snapshot(assigned, tickets, comments, seen_comments))


class _Remote:
    def __init__(self, site, key, secret):
        self.site = site.rstrip("/")
        self.headers = {"Authorization": f"token {key}:{secret}"}

    def _get(self, path, params):
        resp = requests.get(f"{self.site}{path}", params=params, headers=self.headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def assigned_ticket_ids(self, agent):
        rows = self._get(
            "/api/resource/ToDo",
            {
                "filters": json.dumps(
                    [
                        ["reference_type", "=", "HD Ticket"],
                        ["allocated_to", "=", agent],
                        ["status", "=", "Open"],
                    ]
                ),
                "fields": json.dumps(["reference_name"]),
                "limit_page_length": 500,
            },
        )
        return {row["reference_name"] for row in rows if row.get("reference_name")}

    def tickets(self, ids):
        out = []
        ids = list(ids)
        for start in range(0, len(ids), 50):
            out += self._get(
                "/api/resource/HD Ticket",
                {
                    "filters": json.dumps([["name", "in", ids[start : start + 50]]]),
                    "fields": json.dumps(["name", "subject", "status", "last_customer_response"]),
                    "limit_page_length": 100,
                },
            )
        return out

    def recent_comments(self):
        return self._get(
            "/api/resource/HD Ticket Comment",
            {
                "fields": json.dumps(["name", "content", "commented_by", "reference_ticket"]),
                "order_by": "creation desc",
                "limit_page_length": 100,
            },
        )


def _snapshot(assigned, tickets, comments, extra_comment_ids=frozenset()):
    comment_ids = [c["name"] for c in comments if c.get("name")] + list(extra_comment_ids)
    return {
        "primed": True,
        "assignments": list(assigned),
        "replies": {t["name"]: (t.get("last_customer_response") or "") for t in tickets},
        "comment_ids": comment_ids[:500],
    }


def _load_state():
    raw = frappe.db.get_single_value(STATE, "state")
    return frappe.parse_json(raw) if raw else {}


def _save_state(data):
    doc = frappe.get_single(STATE)
    doc.state = frappe.as_json(data)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
