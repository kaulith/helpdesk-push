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
    if not (site and key and secret):
        return

    agents = sorted(
        {d.agent for d in frappe.get_all("HD Push Device", fields=["agent"], distinct=True) if d.agent}
    )
    if not agents:
        return

    try:
        client = _Remote(site, key, secret)
        comments = client.recent_comments()
        state = _load_state()
        for agent in agents:
            _poll_agent(client, agent, comments, state)
        _save_state(state)
    except requests.RequestException:
        pass
    except Exception:
        frappe.log_error(frappe.get_traceback(), "helpdesk_push.poller")


def _poll_agent(client, agent, comments, state):
    assigned = client.assigned_ticket_ids(agent)
    tickets = client.tickets(assigned) if assigned else []

    seen = state.get(agent)
    if not seen or not seen.get("primed"):
        state[agent] = _snapshot(assigned, tickets, comments)
        return

    subjects = {t["name"]: (t.get("subject") or "") for t in tickets}

    for ticket in assigned - set(seen.get("assignments", [])):
        send_to_agent(
            agent,
            f"New ticket assigned · #{ticket}",
            subjects.get(ticket, ""),
            {"ticketId": ticket, "type": "new_assignment"},
        )

    prev_replies = seen.get("replies", {})
    for ticket in tickets:
        name = ticket["name"]
        reply = ticket.get("last_customer_response") or ""
        if reply and prev_replies.get(name) != reply:
            send_to_agent(
                agent,
                f"Customer replied · #{name}",
                subjects.get(name, ""),
                {"ticketId": name, "type": "customer_reply"},
            )

    seen_comments = set(seen.get("comment_ids", []))
    username = agent.split("@")[0]
    for comment in comments:
        cid = comment.get("name")
        if not cid or cid in seen_comments or comment.get("commented_by") == agent:
            continue
        content = comment.get("content") or ""
        mentioned = agent.lower() in content.lower() or f"@{username}".lower() in content.lower()
        ticket = comment.get("reference_ticket")
        if not (mentioned or ticket in assigned):
            continue
        plain = re.sub(r"<[^>]*>", "", content).strip()
        title = f"You were mentioned · #{ticket}" if mentioned else f"New comment · #{ticket}"
        send_to_agent(agent, title, plain[:120], {"ticketId": ticket or "", "type": "new_comment"})

    state[agent] = _snapshot(assigned, tickets, comments, seen_comments)


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
