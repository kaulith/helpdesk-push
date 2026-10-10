import frappe
from frappe.utils import get_fullname


def on_todo_insert(doc, method=None):
    if doc.reference_type != "HD Ticket" or not doc.allocated_to:
        return
    _enqueue(doc.allocated_to, doc.reference_name, "new_assignment")


def on_todo_update(doc, method=None):
    if doc.reference_type != "HD Ticket" or not doc.allocated_to:
        return
    if doc.status != "Cancelled" or not doc.has_value_changed("status"):
        return
    if doc.allocated_to == frappe.session.user:
        return
    _enqueue(doc.allocated_to, doc.reference_name, "unassignment")


def on_reaction_insert(doc, method=None):
    if doc.parenttype != "Comment":
        return
    comment = frappe.db.get_value(
        "Comment",
        doc.parent,
        ["reference_doctype", "reference_name", "comment_email", "owner"],
        as_dict=True,
    )
    if not comment or comment.reference_doctype != "HD Ticket":
        return
    author = comment.comment_email or comment.owner
    if author == doc.user:
        return
    frappe.enqueue(
        "helpdesk_push.fcm.send_to_agent",
        queue="short",
        enqueue_after_commit=True,
        agent=author,
        title=f"Reaction on your comment #{comment.reference_name}",
        body=f"{get_fullname(doc.user)} reacted {doc.emoji}",
        data={"ticketId": str(comment.reference_name), "type": "comment_reaction"},
    )


def on_communication_insert(doc, method=None):
    if doc.reference_doctype != "HD Ticket":
        return
    if doc.sent_or_received != "Received":
        return
    for agent in _ticket_assignees(doc.reference_name):
        _enqueue(agent, doc.reference_name, "customer_reply")


def _ticket_assignees(ticket):
    assigned = frappe.db.get_value("HD Ticket", ticket, "_assign")
    return frappe.parse_json(assigned) if assigned else []


def _enqueue(agent, ticket, event_type):
    frappe.enqueue(
        "helpdesk_push.fcm.notify_agent",
        queue="short",
        enqueue_after_commit=True,
        agent=agent,
        ticket=ticket,
        event_type=event_type,
    )
