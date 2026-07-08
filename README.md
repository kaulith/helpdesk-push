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

## API

- `POST /api/method/helpdesk_push.api.register_device` — `{ device_token, agent_email }`
- `POST /api/method/helpdesk_push.api.unregister_device` — `{ device_token, agent_email }`

Both require an authenticated request (API key + secret).
