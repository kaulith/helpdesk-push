import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
import requests

from helpdesk_push.fcm import send_to_agent

STATE = "HD Push Poll State"
SLA_EXEMPT = {"Closed", "Resolved", "Replied"}
SLA_WINDOW_HOURS = 2


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

    tz = ZoneInfo(conf.get("poll_timezone") or "Asia/Kolkata")
    try:
        client = _Remote(site, key, secret)
        comments = client.recent_comments()
        state = _load_state()
        for agent in agents:
            _poll_agent(client, agent, comments, state, tz)
        _save_state(state)
    except requests.RequestException:
        pass
    except Exception:
        frappe.log_error(frappe.get_traceback(), "helpdesk_push.poller")


def _poll_agent(client, agent, comments, state, tz):
    assigned = client.assigned_ticket_ids(agent)
    tickets = client.tickets(assigned) if assigned else []

    seen = state.get(agent)
    if not seen or not seen.get("primed"):
        state[agent] = _snapshot(assigned, tickets, comments, tz)
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

    warnable = _sla_warnable(tickets, tz)
    prev_sla = set(seen.get("sla_warned", []))
    for ticket in warnable - prev_sla:
        hours = _hours_until(_ticket_field(tickets, ticket, "response_by"), tz)
        detail = f"First response due in {hours}h" if hours is not None else "First response SLA approaching"
        send_to_agent(
            agent,
            f"⚠️ SLA warning · #{ticket}",
            f"{detail} — {subjects.get(ticket, '')}".rstrip(" —"),
            {"ticketId": ticket, "type": "sla_warning"},
        )

    state[agent] = _snapshot(assigned, tickets, comments, tz, seen_comments, prev_sla | warnable)


class _Remote:
    def __init__(self, site, key, secret):
        self.site = site.rstrip("/")
        self.headers = {"Authorization": f"token {key}:{secret}"}

    def _get(self, path, params):
        resp = requests.get(f"{self.site}{path}", params=params, headers=self.headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def assigned_ticket_ids(self, agent):
        ids = set()
        start = 0
        page = 500
        while True:
            rows = self._get(
                "/api/resource/HD Ticket",
                {
                    "filters": json.dumps([["_assign", "like", f"%{agent}%"]]),
                    "fields": json.dumps(["name"]),
                    "order_by": "creation asc",
                    "limit_start": start,
                    "limit_page_length": page,
                },
            )
            ids.update(row["name"] for row in rows if row.get("name"))
            if len(rows) < page:
                break
            start += page
        return ids

    def tickets(self, ids):
        out = []
        ids = list(ids)
        for start in range(0, len(ids), 50):
            out += self._get(
                "/api/resource/HD Ticket",
                {
                    "filters": json.dumps([["name", "in", ids[start : start + 50]]]),
                    "fields": json.dumps(
                        ["name", "subject", "status", "last_customer_response", "response_by"]
                    ),
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


def _sla_warnable(tickets, tz):
    now = datetime.now(tz)
    horizon = now + timedelta(hours=SLA_WINDOW_HOURS)
    warnable = set()
    for ticket in tickets:
        if ticket.get("status") in SLA_EXEMPT:
            continue
        due = _parse_dt(ticket.get("response_by"), tz)
        if due and now <= due <= horizon:
            warnable.add(ticket["name"])
    return warnable


def _hours_until(response_by, tz):
    due = _parse_dt(response_by, tz)
    if not due:
        return None
    return round((due - datetime.now(tz)).total_seconds() / 3600, 1)


def _parse_dt(value, tz):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
    except ValueError:
        return None


def _ticket_field(tickets, name, field):
    for ticket in tickets:
        if ticket["name"] == name:
            return ticket.get(field)
    return None


def _snapshot(assigned, tickets, comments, tz, extra_comment_ids=frozenset(), sla_carry=None):
    comment_ids = [c["name"] for c in comments if c.get("name")] + list(extra_comment_ids)
    if sla_carry is None:
        sla = _sla_warnable(tickets, tz)
    else:
        open_nonexempt = {t["name"] for t in tickets if t.get("status") not in SLA_EXEMPT}
        sla = sla_carry & open_nonexempt
    return {
        "primed": True,
        "assignments": list(assigned),
        "replies": {t["name"]: (t.get("last_customer_response") or "") for t in tickets},
        "comment_ids": comment_ids[:500],
        "sla_warned": list(sla),
    }


def _load_state():
    raw = frappe.db.get_single_value(STATE, "state")
    return frappe.parse_json(raw) if raw else {}


def _save_state(data):
    doc = frappe.get_single(STATE)
    doc.state = frappe.as_json(data)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
