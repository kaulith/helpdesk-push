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
with an agent's key and pushes via FCM. Set in site config:

```bash
bench --site <site> set-config poll_site_url https://support.frappe.io
bench --site <site> set-config poll_api_key <agent_key>
bench --site <site> set-config -p poll_api_secret <agent_secret>
bench --site <site> set-config poll_agent_email <agent@remote>
bench --site <site> set-config poll_notify_agent <agent_on_this_site>
```

`poll_agent_email` filters "assigned to me" / @mentions on the remote site.
`poll_notify_agent` is the local `HD Push Device` agent to push to. Leave any key
unset to disable polling. First run primes state silently (no backfill storm);
subsequent runs notify only on new replies, assignments, and @mentions.

## API

- `POST /api/method/helpdesk_push.api.register_device` — `{ device_token, agent_email }`
- `POST /api/method/helpdesk_push.api.unregister_device` — `{ device_token, agent_email }`

Both require an authenticated request (API key + secret).
