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
    its slot, the VO is sped up to fit — ElevenLabs speed (its 1.2x cap) then
    ffmpeg atempo up to a combined config.MAX_VO_SPEED (1.5x), both pitch-preserved.
    The voiceover is NEVER cut: if a line still overruns at max speed, the scene is
    extended so the whole line plays (lines that fit keep their exact slot).
    Returns captions with absolute timeline positions."""
    os.makedirs(work_dir, exist_ok=True)
    voice_map = voice_map or {}
    captions: list[Caption] = []
    offset = 0.0
    for clip in clips:
        segs = [s for s in clip.segments() if s.line]
        if not segs:
            offset += clip.duration
            continue

        if len(segs) == 1:
            beat_caps, path = _one_segment(segs[0], clip, work_dir, voice_map)
        else:
            beat_caps, path = _multi_segment(segs, clip, work_dir, voice_map)
        clip.audio_path = path
        # NEVER cut the voiceover. We first try to hold the screenplay slot by
        # speeding the line up (<=config.MAX_VO_SPEED, pitch-preserved) — so lines
        # that fit keep their exact slot. If a line STILL overruns at max speed, we
        # extend the scene to play the whole line (the clip is trimmed to this), so
        # no word is ever clipped at the cut.
        spoken = beat_caps[-1].end if beat_caps else 0.0
        if not clip.duration:
            clip.duration = round(spoken + 0.15, 2)
        elif spoken > clip.duration + 0.05:
            print(f"[VO] beat {clip.index}: line {spoken:.1f}s > {clip.duration:.0f}s slot "
                  f"even at {config.MAX_VO_SPEED}x — extending scene to fit (no cut).")
            clip.duration = round(spoken + 0.1, 2)

        for c in beat_caps:
            captions.append(Caption(text=c.text, start=c.start + offset, end=c.end + offset))
        offset += clip.duration
    return captions


def _one_segment(seg, clip, work_dir, voice_map):
    """Single speaker: TTS once, pace to the scene slot if it overruns.

    Two-stage fit, both pitch-preserved: (1) ElevenLabs speed, capped at its 1.2x
    API limit; (2) if still long, ffmpeg atempo time-compresses up to a COMBINED
    config.MAX_VO_SPEED (1.5x). Only past that do we stretch the scene."""
    voice = voice_map.get(seg.speaker or "VO")
    audio_b64, alignment = _tts(seg.line, voice_id=voice)
    caps = _captions_from_alignment(alignment)
    spoken = caps[-1].end if caps else 0.0
    target = clip.duration
    el_speed = 1.0
    if target and spoken > target + 0.15:
        el_speed = min(1.2, spoken / target)
        print(f"[VO] beat {clip.index}: {spoken:.1f}s > {target:.0f}s slot -> ElevenLabs {el_speed:.2f}x")
        audio_b64, alignment = _tts(seg.line, voice_id=voice, speed=el_speed)
        caps = _captions_from_alignment(alignment)
        spoken = caps[-1].end if caps else 0.0
    path = os.path.join(work_dir, f"vo_{clip.index:02d}.mp3")
    with open(path, "wb") as f:
        f.write(base64.b64decode(audio_b64))
    # Still over after ElevenLabs' 1.2x cap? Time-compress with ffmpeg atempo up to
    # the combined MAX_VO_SPEED before falling back to stretching the scene.
    if target and spoken > target + 0.15:
        room = config.MAX_VO_SPEED / el_speed          # extra speed still allowed
        factor = min(room, spoken / target)
        if factor > 1.01:
            caps = _atempo(path, factor, caps)
            spoken = caps[-1].end if caps else spoken
            print(f"[VO] beat {clip.index}: atempo {factor:.2f}x -> {spoken:.1f}s "
                  f"(combined {el_speed * factor:.2f}x, cap {config.MAX_VO_SPEED}x)")
    if target and spoken > target + 0.3:
        print(f"[VO] beat {clip.index} still {spoken:.1f}s in a {target:.0f}s slot at max speed "
              f"— scene will extend to fit (no cut)")
    return caps, path


def _multi_segment(segs, clip, work_dir, voice_map):
    """Multiple speakers in one scene: TTS each in its own voice, then stitch into
    one scene mp3 (Shotstack, free). Captions accumulate across segments."""
    seg_paths, caps, t = [], [], 0.0
    for j, seg in enumerate(segs):
        voice = voice_map.get(seg.speaker or "VO")
        audio_b64, alignment = _tts(seg.line, voice_id=voice)
        p = os.path.join(work_dir, f"vo_{clip.index:02d}_{j}.mp3")
        with open(p, "wb") as f:
            f.write(base64.b64decode(audio_b64))
        seg_paths.append(p)
        seg_caps = _captions_from_alignment(alignment)
        for c in seg_caps:
            caps.append(Caption(text=c.text, start=c.start + t, end=c.end + t))
        t += (seg_caps[-1].end if seg_caps else 0.0)
        print(f"[VO] beat {clip.index} seg {j} ({seg.speaker}): {seg.line[:40]!r}")
    merged = _concat_audio(seg_paths, os.path.join(work_dir, f"vo_{clip.index:02d}.mp3"))
    if clip.duration and t > clip.duration + 0.15:
        factor = min(config.MAX_VO_SPEED, t / clip.duration)
        if factor > 1.01:
            caps = _atempo(merged, factor, caps)
            t = caps[-1].end if caps else t
            print(f"[VO] beat {clip.index} multi: atempo {factor:.2f}x -> {t:.1f}s")
    if clip.duration and t > clip.duration + 0.3:
        print(f"[VO] ⚠ beat {clip.index} multi-speaker VO {t:.1f}s > {clip.duration:.0f}s slot")
    return caps, merged


def _atempo(path: str, factor: float, caps: list[Caption]) -> list[Caption]:
    """Time-compress an mp3 by `factor` IN PLACE (pitch-preserved, ffmpeg atempo)
    and scale the word-caption timings to match. atempo handles 0.5–2.0 in one
    pass (our factor is <=1.5). Fails soft: on any error keep the original."""
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        print("[VO] ffmpeg not found — skipping atempo (scene will stretch instead)")
        return caps
    tmp = path + ".tmp.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "quiet", "-i", path,
             "-filter:a", f"atempo={factor:.3f}", tmp],
            check=True,
        )
        os.replace(tmp, path)
    except Exception as e:                               # noqa: BLE001
        print(f"[VO] atempo failed ({e}); keeping original speed")
        if os.path.exists(tmp):
            os.remove(tmp)
        return caps
    return [Caption(text=c.text, start=c.start / factor, end=c.end / factor) for c in caps]


def _concat_audio(paths: list[str], dest: str) -> str:
    """Stitch mp3 segments into one, in order. MP3 is a frame stream, so byte
    concatenation plays back sequentially — no ffmpeg needed."""
    with open(dest, "wb") as out:
        for p in paths:
            with open(p, "rb") as f:
                out.write(f.read())
    return dest


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

def _clean_delivery(text: str) -> str:
    """Strip per-line delivery cues like (whispering)/(urgent) so they aren't spoken.
    Keep '...' and '—' — ElevenLabs renders those as natural pauses."""
    import re
    text = re.sub(r"\([^)]*\)", "", text)          # remove (performance cues)
    text = text.replace("·", " ").replace("|", " ")
    return re.sub(r"\s+", " ", text).strip()


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
        text=_clean_delivery(text),
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
