# Global false-mention fix: all groups and linked photo/GPS workflows

## Confirmed case

- Group: R6-SA-BTY8
- SA: Primg Sokim (SA, BTY8, R6)
- Alert window: 03/08/2026 13:43-14:06
- Form/photo: 14:01
- GPS reply: 14:02

Both activities are inside the same window, so this SA must not be mentioned.
This case exposed a system-wide identity-linking weakness; the correction is in
the shared handlers and matcher used by every group, user, role and window.

## Root cause and correction

The old matcher required the independently stored photo row and PinHistory row
to resolve to the same current SA row. A legacy duplicate or stale canonical ID
could separate those rows even though the Telegram user correctly replied to
their own photo with GPS.

The reply workflow now writes atomic proof on SalePhotoReport. A linked report
with both reply latitude and longitude satisfies form/photo + GPS when its
canonical or stable Telegram identity resolves to the official SA. Both reply
directions verify the photo sender and GPS sender are the same account. Cross-SA
replies are never treated as linked proof.

## All-groups validation

`scripts/validate_all_groups_no_wrong_mentions.py` validates the shared logic
against all 65 configured groups and all 210 official users. It covers 1,478
group/flow/window combinations, all 210 linked-reply identities, and cross-user
protection wherever a flow contains at least two users.

## Apply and validate

```powershell
cd D:\Bot\telegram_sa_location_alert_bot_v4

.\.venv\Scripts\python.exe -m py_compile app\bot\handlers.py
.\.venv\Scripts\python.exe -m py_compile app\services\user_service.py
.\.venv\Scripts\python.exe scripts\validate_atomic_photo_gps.py
.\.venv\Scripts\python.exe scripts\validate_all_groups_no_wrong_mentions.py
.\.venv\Scripts\python.exe scripts\validate_project.py
.\.venv\Scripts\python.exe scripts\validate_new_features.py

docker compose up -d
.\.venv\Scripts\python.exe -m app.main
```

The Compose file starts PostgreSQL only. Run `app.main` once; do not start a
second bot process concurrently.
