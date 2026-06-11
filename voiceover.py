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

def synthesize_per_beat(clips, work_dir: str, voice_map: dict | None = None) -> list[Caption]:
    """TTS each beat's line to its own file; set clip.audio_path.
    voice_map ({speaker: voice_id}, from casting.cast) picks each beat's voice.

    The screenplay's scene time is AUTHORITATIVE: clip.duration is the fixed
    timeline slot and is never overwritten. If a spoken line runs longer than
    its slot, the VO is regenerated faster (ElevenLabs speed, capped 1.2x);
    if it still doesn't fit, we warn (QA layer 1 also checks duration).
    Returns captions with absolute timeline positions."""
    os.makedirs(work_dir, exist_ok=True)
    voice_map = voice_map or {}
    captions: list[Caption] = []
    offset = 0.0
    for clip in clips:
        if not clip.vo_line:
            offset += clip.duration
            continue
        voice = voice_map.get(clip.speaker or "VO")
        audio_b64, alignment = _tts(clip.vo_line, voice_id=voice)
        beat_caps = _captions_from_alignment(alignment)
        spoken = beat_caps[-1].end if beat_caps else 0.0

        target = clip.duration
        if target and spoken > target + 0.15:
            speed = min(1.2, spoken / target)
            print(f"[VO] beat {clip.index}: {spoken:.1f}s > {target:.0f}s slot -> retry at {speed:.2f}x")
            audio_b64, alignment = _tts(clip.vo_line, voice_id=voice, speed=speed)
            beat_caps = _captions_from_alignment(alignment)
            spoken = beat_caps[-1].end if beat_caps else 0.0
            if spoken > target + 0.3:
                print(f"[VO] ⚠ beat {clip.index} still {spoken:.1f}s in a {target:.0f}s slot "
                      f"(line too long for the scene time)")

        path = os.path.join(work_dir, f"vo_{clip.index:02d}.mp3")
        with open(path, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        clip.audio_path = path
        if not target:
            clip.duration = spoken  # no slot given -> audio defines it
        for c in beat_caps:
            captions.append(Caption(text=c.text, start=c.start + offset, end=c.end + offset))
        offset += clip.duration
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

def _tts(text: str, voice_id: str | None = None, speed: float | None = None):
    from elevenlabs.client import ElevenLabs  # lazy
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY not set. Add it to .env.")
    client = ElevenLabs()
    kwargs = {}
    if speed:
        kwargs["voice_settings"] = {"speed": max(0.7, min(1.2, speed))}
    resp = client.text_to_speech.convert_with_timestamps(
        voice_id=voice_id or config.ELEVENLABS_VOICE_ID,
        model_id=config.ELEVENLABS_MODEL,
        text=text,
        **kwargs,
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
