"""Offline/static regression checks for reason and voice reliability V20."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.voice_quality import clean_voice_export_text, is_low_quality_transcript
from app.bot import handlers
from app.services.export_service import _clean_reason


HANDLERS = (ROOT / "app" / "bot" / "handlers.py").read_text(encoding="utf-8")
MAIN = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
SETUP = (ROOT / "SETUP_AND_RUN.ps1").read_text(encoding="utf-8")

# Every Python file must at least parse before deployment.
for path in sorted((ROOT / "app").rglob("*.py")):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

# A replied outlet/order form must go through the normal submission handler,
# never into MissingSalePhotoAlert.reason_text.
assert "if raw_reason and looks_like_sale_form(raw_reason):" in HANDLERS
assert "if replied is None and raw_reason and looks_like_sale_form(raw_reason):" not in HANDLERS
assert "Sales submission excluded from missing reason" in HANDLERS

# Telegram voice notes and audio-file uploads both reach the transcription path.
assert "filters.VOICE | filters.AUDIO | filters.PHOTO" in MAIN
assert "audio = message.voice or message.audio" in HANDLERS
assert "asyncio.wait_for(" in HANDLERS
assert 'VOICE_TRANSCRIPTION_MODEL=large-v3' in (ROOT / ".env").read_text(encoding="utf-8-sig")

# Broken ASR output must not be exported as question marks; normal Khmer text
# remains valid Unicode text.
khmer = "ឡានខូច មិនអាចចូលទីផ្សារបាន"
assert not is_low_quality_transcript(khmer)
assert clean_voice_export_text(khmer) == khmer
assert is_low_quality_transcript("????????????????????")
assert clean_voice_export_text("????????????????????") == ""
historic_form = "ID:90014969 Dealer: TKM2 Outlet Name: Test Telephone:068566544"
assert _clean_reason(historic_form) == ""
assert _clean_reason("ឡានខូច @PIDOL022") == "ឡានខូច"

# Local Windows startup owns the bot process; Docker owns PostgreSQL only.
assert "docker compose up -d postgres" in SETUP
assert "docker compose up -d\n" not in SETUP


async def validate_replied_sale_form_is_not_a_reason() -> None:
    replied_alert = SimpleNamespace(
        message_id=500,
        from_user=SimpleNamespace(is_bot=True),
    )
    message = SimpleNamespace(
        message_id=501,
        reply_to_message=replied_alert,
        text=None,
        caption=(
            "ID:90014969 Dealer: TKM2 Outlet Name: Test "
            "Telephone:068566544 WATER PET 1.5L: 5"
        ),
        photo=[object()],
        voice=None,
        audio=None,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
        effective_message=message,
    )
    original_session = handlers.SessionLocal
    try:
        handlers.SessionLocal = lambda: (_ for _ in ()).throw(
            AssertionError("sale form reached reason database write")
        )
        await handlers.receive_missing_reason_reply(update, SimpleNamespace())
    finally:
        handlers.SessionLocal = original_session


async def validate_audio_file_transcription_path() -> None:
    class FakeTelegramFile:
        file_path = "voice/reason.m4a"

        async def download_as_bytearray(self):
            return bytearray(b"fake-m4a-audio")

    class FakeBot:
        async def get_file(self, _file_id):
            return FakeTelegramFile()

    class FakeTranscriber:
        async def transcribe(self, audio_path):
            assert Path(audio_path).suffix == ".m4a"
            assert Path(audio_path).read_bytes() == b"fake-m4a-audio"
            return "ឡានខូច"

    original_transcriber = handlers.get_local_voice_transcriber
    try:
        handlers.get_local_voice_transcriber = lambda: FakeTranscriber()
        context = SimpleNamespace(bot=FakeBot(), bot_data={})
        audio = SimpleNamespace(
            file_id="audio-1",
            file_name="reason.m4a",
            mime_type="audio/mp4",
        )
        transcript = await handlers._transcribe_voice_message(context, audio)
    finally:
        handlers.get_local_voice_transcriber = original_transcriber
    assert transcript == "ឡានខូច"


asyncio.run(validate_replied_sale_form_is_not_a_reason())
asyncio.run(validate_audio_file_transcription_path())

print("REASON/VOICE V20 REGRESSION OK")
