"""
Voiceover (Step 3): VO lines -> ElevenLabs audio + word-level captions.

Concatenates the per-beat VO lines into one narration track and asks ElevenLabs
for character-level timestamps, which we group into word captions for the
assembly caption track.

NOTE: the ElevenLabs SDK's timestamp response field names vary across versions.
This handles the common shapes (audio_base64 / audio_base_64, alignment object)
and is the most likely spot to need a one-line tweak on first live run.
"""
from __future__ import annotations

import base64
import os

import config
from schema import AudioTrack, Caption


def synthesize(clips, out_path: str) -> tuple[AudioTrack, list[Caption]]:
    from elevenlabs.client import ElevenLabs  # lazy import

    if not os.getenv("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY not set. Add it to .env.")

    text = " ".join(c.vo_line for c in clips if c.vo_line).strip()
    if not text:
        return AudioTrack(vo_path=None, duration=0.0), []

    client = ElevenLabs()
    resp = client.text_to_speech.convert_with_timestamps(
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
        text=text,
    )

    audio_b64 = _get(resp, "audio_base64") or _get(resp, "audio_base_64")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))

    captions = _captions_from_alignment(_get(resp, "alignment"))
    duration = captions[-1].end if captions else 0.0
    return AudioTrack(vo_path=out_path, duration=duration), captions


def _get(obj, key):
    """Tolerate dict- or attribute-style SDK responses."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _captions_from_alignment(alignment) -> list[Caption]:
    """Group per-character timings into per-word captions."""
    if alignment is None:
        return []
    chars = _get(alignment, "characters") or []
    starts = _get(alignment, "character_start_times_seconds") or []
    ends = _get(alignment, "character_end_times_seconds") or []
    if not (chars and starts and ends):
        return []

    captions: list[Caption] = []
    word, w_start, w_end = "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if word:
                captions.append(Caption(text=word, start=w_start, end=w_end))
                word, w_start, w_end = "", None, None
            continue
        if w_start is None:
            w_start = s
        word += ch
        w_end = e
    if word:
        captions.append(Caption(text=word, start=w_start, end=w_end))
    return captions
