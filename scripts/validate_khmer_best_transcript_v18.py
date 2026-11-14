"""Regression: a non-empty Khmer model result is never erased as a failure."""

from pathlib import Path
from threading import Lock
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.local_voice_transcriber import LocalVoiceTranscriber


transcriber = LocalVoiceTranscriber.__new__(LocalVoiceTranscriber)
transcriber.model_name = "offline-test"
transcriber.language = "km"
transcriber.requested_device = "cpu"
transcriber.requested_compute_type = "int8"
transcriber.cpu_fallback = False
transcriber._models = {}
transcriber._load_lock = Lock()
transcriber._inference_lock = Lock()
transcriber._decode_audio = lambda _path: np.zeros(16000, dtype=np.float32)

khmer_candidate = "អ" * 80
transcriber._attempt = lambda *_args, **_kwargs: khmer_candidate

result = transcriber._transcribe_sync(Path("offline.ogg"))
assert result == khmer_candidate

handlers = (ROOT / "app/bot/handlers.py").read_text(
    encoding="utf-8"
)
assert "_voice_transcription_errors" in handlers
assert "Cause:" in handlers

print("KHMER BEST-TRANSCRIPT V18 REGRESSION OK")
