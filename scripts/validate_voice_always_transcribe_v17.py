"""Static regression checks for the V17 always-transcribe voice flow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
handlers = (ROOT / "app/bot/handlers.py").read_text(encoding="utf-8")
service = (ROOT / "app/services/user_service.py").read_text(encoding="utf-8")
main = (ROOT / "app/main.py").read_text(encoding="utf-8")

save_at = handlers.index("start_voice_reason_history(")
transcribe_at = handlers.index("voice_to_text = await _transcribe_voice_message")
assert save_at < transcribe_at, "voice metadata must be saved before transcription"

between = handlers[save_at:transcribe_at]
assert "if not linked:" not in between, (
    "unlinked official voice must not return before Faster-Whisper"
)
assert "for attempt in range(1, 4):" in handlers
assert "converted to text successfully" in handlers
assert "transcribed_unlinked" in service
assert "return True" in service[service.index('history.status = "transcribed_unlinked"'):]
assert 'BUILD_ID = "20260823-reason-voice-reliability-v20"' in main

print("VOICE ALWAYS-TRANSCRIBE V17 REGRESSION OK")
