app_name = "helpdesk_push"
app_title = "Helpdesk Push"
app_publisher = "Kaushal Shriwas"
app_description = "Push notifications for the Helpdesk mobile app"
app_email = "kristopherj554@gmail.com"
app_license = "mit"

doc_events = {
    "ToDo": {
        "after_insert": "helpdesk_push.notifications.on_todo_insert",
    },
    "Communication": {
        "after_insert": "helpdesk_push.notifications.on_communication_insert",
    },
}

scheduler_events = {
    "cron": {
        "* * * * *": ["helpdesk_push.poller.poll_all"],
    },
}
