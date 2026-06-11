"""
Voiceover (Stage 3): VO lines -> ElevenLabs audio + word captions.

Two modes, chosen by the recipe:
  - synthesize_per_beat()  (lip-sync types): ONE audio clip per beat, because each
    beat's storyboard frame is driven by its own line. Sets clip.audio_path +
    clip.duration (= the spoken line's length). Returns absolute-timed captions.
  - synthesize()           (pixar): ONE continuous narration track laid over the
    silent animated clips. Returns an AudioTrack + captions.

NOTE: ElevenLabs timestamp field names vary by SDK version; _get() tolerates the
common variants. Likeliest one-line fix on first live run.
"""
from __future__ import annotations

import base64
import os

import config
from schema import AudioTrack, Caption


# ---- per-beat (lip-sync types) -----------------------------------------------

def synthesize_per_beat(clips, work_dir: str) -> list[Caption]:
    """TTS each beat's line to its own file; set clip.audio_path + clip.duration.
    Returns captions with absolute timeline positions."""
    os.makedirs(work_dir, exist_ok=True)
    captions: list[Caption] = []
    offset = 0.0
    for clip in clips:
        if not clip.vo_line:
            offset += clip.duration
            continue
        audio_b64, alignment = _tts(clip.vo_line)
        path = os.path.join(work_dir, f"vo_{clip.index:02d}.mp3")
        with open(path, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        clip.audio_path = path

        beat_caps = _captions_from_alignment(alignment)
        dur = beat_caps[-1].end if beat_caps else clip.duration
        clip.duration = dur
        for c in beat_caps:
            captions.append(Caption(text=c.text, start=c.start + offset, end=c.end + offset))
        offset += dur
    return captions


# ---- continuous (pixar) -------------------------------------------------------

def synthesize(clips, out_path: str) -> tuple[AudioTrack, list[Caption]]:
    text = " ".join(c.vo_line for c in clips if c.vo_line).strip()
    if not text:
        return AudioTrack(vo_path=None, duration=0.0), []
    audio_b64, alignment = _tts(text)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(audio_b64))
    captions = _captions_from_alignment(alignment)
    duration = captions[-1].end if captions else 0.0
    return AudioTrack(vo_path=out_path, duration=duration), captions


# ---- shared -------------------------------------------------------------------

def _tts(text: str):
    from elevenlabs.client import ElevenLabs  # lazy
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY not set. Add it to .env.")
    client = ElevenLabs()
    resp = client.text_to_speech.convert_with_timestamps(
        voice_id=config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
        text=text,
    )
    audio_b64 = _get(resp, "audio_base64") or _get(resp, "audio_base_64")
    return audio_b64, _get(resp, "alignment")


def _get(obj, key):
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
