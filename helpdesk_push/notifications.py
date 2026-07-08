import frappe


def on_todo_insert(doc, method=None):
    if doc.reference_type != "HD Ticket" or not doc.allocated_to:
        return
    _enqueue(doc.allocated_to, doc.reference_name, "new_assignment")


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


def _enqueue(agent, ticket, event):
    frappe.enqueue(
        "helpdesk_push.fcm.notify_agent",
        queue="short",
        enqueue_after_commit=True,
        agent=agent,
        ticket=ticket,
        event=event,
    )
