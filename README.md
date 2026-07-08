# Helpdesk Push

Custom Frappe app that sends FCM push notifications to the Helpdesk mobile app on ticket events. Runs on the same site as Frappe Helpdesk.

## What it does

- Stores per-agent device tokens (`HD Push Device` DocType).
- Fires FCM on:
  - ticket assigned to an agent (`ToDo` insert on `HD Ticket`)
  - new customer reply on an assigned ticket (`Communication` received on `HD Ticket`)
- Exposes `register_device` / `unregister_device` for the app.

## Configure

Store the Firebase service-account JSON in site config, either inline:

```bash
bench --site <site> set-config -p fcm_service_account '<full-json>'
```

or as a file + path:

```bash
bench --site <site> set-config fcm_service_account_path sites/<site>/private/files/firebase-service-account.json
```

## Polling an external Helpdesk site

Hooks only fire on this site. To get notifications from a Helpdesk site you don't
control (no app-install rights there), a cron job (every minute) polls its REST API
and pushes via FCM. Set in site config:

```bash
bench --site <site> set-config poll_site_url https://support.frappe.io
bench --site <site> set-config poll_api_key <system_manager_key>
bench --site <site> set-config -p poll_api_secret <system_manager_secret>
```

`poll_api_key` must be a System Manager on the remote site — the poller queries per
agent (`allocated_to`) across all tickets. It polls for each distinct agent that has
a registered `HD Push Device` and pushes to that agent's devices, so one admin key
serves every agent using the app. `poll_site_url` also verifies device registrations
(guest `register_device` echoes the caller's token back to this site's
`get_logged_user`). Leave keys unset to disable polling. First run per agent primes
state silently (no backfill storm); after that only new replies, assignments,
@mentions, and SLA warnings notify.

SLA warnings fire once when a ticket's first-response deadline (`response_by`) falls
within the next 2 hours and the ticket isn't already responded/resolved/closed.
Deadlines are read in the remote site's timezone — set `poll_timezone` (an IANA name
like `Asia/Kolkata`, the default) if the remote site isn't on IST:

```bash
bench --site <site> set-config poll_timezone Asia/Kolkata
```

## API

- `POST /api/method/helpdesk_push.api.register_device` — `{ device_token, agent_email }`
- `POST /api/method/helpdesk_push.api.unregister_device` — `{ device_token, agent_email }`

Both require an authenticated request (API key + secret).
