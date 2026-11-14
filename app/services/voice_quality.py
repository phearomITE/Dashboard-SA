from __future__ import annotations

from collections import Counter
import re


LEGACY_VOICE_PREFIX = "[Voice received -"


def is_low_quality_transcript(raw: str | None) -> bool:
    """Reject obvious ASR hallucinations without rejecting normal short reasons."""
    text = " ".join((raw or "").split()).strip()
    if not text or text.startswith(LEGACY_VOICE_PREFIX):
        return True

    # Failed decoders/models sometimes return rows made mostly of question
    # marks or Unicode replacement characters. Those values previously
    # appeared as "????????" in the Excel Voice To Text filter.
    visible = [char for char in text if not char.isspace()]
    damaged = sum(char in {"?", "�"} for char in visible)
    if visible and damaged / len(visible) >= 0.25:
        return True
    if "???" in text or "���" in text:
        return True

    compact = [char for char in text if char.isalnum()]
    if not compact:
        return True
    if len(compact) < 20:
        return False

    counts = Counter(compact)
    if counts.most_common(1)[0][1] / len(compact) >= 0.38:
        return True
    if len(counts) / len(compact) <= 0.10:
        return True
    if re.search(r"(.)\1{7,}", "".join(compact)):
        return True

    words = re.findall(r"\S+", text.casefold())
    if len(words) >= 6:
        word_counts = Counter(words)
        if word_counts.most_common(1)[0][1] / len(words) >= 0.60:
            return True
    return False


def clean_voice_export_text(raw: str | None) -> str:
    """Hide legacy placeholders and obvious gibberish from new exports."""
    text = " ".join((raw or "").split()).strip()
    return "" if is_low_quality_transcript(text) else text
