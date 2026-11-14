from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")

transcriber = read("app/services/local_voice_transcriber.py")
user_service = read("app/services/user_service.py")
handlers = read("app/bot/handlers.py")
main = read("app/main.py")

assert '("cpu", "int8", None, 1)' not in transcriber
assert '("cpu", "int8", self.language, 5)' in transcriber
assert "if voice_to_text:" in user_service
assert "MissingSalePhotoAlert.voice_to_text.is_(None)" in user_service
assert "elif reason_text:" in user_service
assert "MissingSalePhotoAlert.reason_text.is_(None)" in user_service
assert "Missing reason recorded:" in handlers
assert "Missing reason not linked:" in handlers
assert 'BUILD_ID = "20260823-reason-voice-reliability-v20"' in main

print("VOICE LINK V15 VALIDATION OK")
