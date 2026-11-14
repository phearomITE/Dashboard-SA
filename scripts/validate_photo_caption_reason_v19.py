"""Regression checks for photo-caption missing reasons (V19)."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
HANDLERS = (ROOT / "app" / "bot" / "handlers.py").read_text(encoding="utf-8")
USER_SERVICE = (ROOT / "app" / "services" / "user_service.py").read_text(
    encoding="utf-8"
)

assert 'BUILD_ID = "20260823-reason-voice-reliability-v20"' in MAIN
assert "| filters.VOICE | filters.AUDIO | filters.PHOTO" in MAIN
assert "raw_reason = (message.text or message.caption or \"\").strip()" in HANDLERS
assert "if message.photo and not replied_to_bot:" in HANDLERS
assert "if raw_reason and looks_like_sale_form(raw_reason):" in HANDLERS
assert "if replied is None and raw_reason and looks_like_sale_form(raw_reason):" not in HANDLERS
assert "save_missing_reason_reply(" in HANDLERS

# Cross-user protection remains authoritative: group + alert message + numeric
# Telegram ID must all match before a caption can update an export row.
assert "MissingSalePhotoAlert.alert_message_id == alert_message_id" in USER_SERVICE
assert "MissingSalePhotoAlert.telegram_user_id == user.telegram_user_id" in USER_SERVICE
assert "SAUser.telegram_user_id == sender_user_id" in USER_SERVICE

print("PHOTO-CAPTION REASON V19 REGRESSION OK")
