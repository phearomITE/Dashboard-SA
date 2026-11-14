"""Static regression checks for persistent voice history V16."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, *needles: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in text, f"{path}: missing {needle!r}"


require(
    "app/database/models.py",
    "class VoiceReasonHistory",
    'voice_file_id: Mapped[str]',
    'status: Mapped[str]',
)
require(
    "app/database/migrations.py",
    "CREATE TABLE IF NOT EXISTS voice_reason_history",
)
require(
    "app/services/user_service.py",
    "def start_voice_reason_history",
    "def finish_voice_reason_history",
    "SAUser.telegram_user_id == sender_user_id",
)
require(
    "app/bot/handlers.py",
    "start_voice_reason_history(",
    "voice_to_text = await _transcribe_voice_message",
    "async def voice_history",
    "async def retry_voice",
)
handlers = (ROOT / "app/bot/handlers.py").read_text(encoding="utf-8")
assert handlers.index("start_voice_reason_history(") < handlers.index(
    "voice_to_text = await _transcribe_voice_message"
), "voice metadata must be saved before transcription"
require(
    "app/main.py",
    'BUILD_ID = "20260823-reason-voice-reliability-v20"',
    'CommandHandler("voice_history", voice_history)',
    'CommandHandler("retry_voice", retry_voice)',
)

print("VOICE HISTORY V16 REGRESSION OK")
