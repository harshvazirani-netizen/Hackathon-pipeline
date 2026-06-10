"""
QA Layer 1 (cheapest): technical checks via ffprobe/ffmpeg.

Checks: duration vs intended, 9:16 aspect, exact resolution, audio present,
audio not clipping, black frames, frozen frames, captions-within-duration.

Returns (failures: list[str], scores: dict). The gate decides pass/fail; in
calibration mode failures are logged but not enforced.

Needs the ffmpeg/ffprobe binaries:  brew install ffmpeg
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import config


def run(bundle, mp4_path: str) -> tuple[list[str], dict]:
    failures: list[str] = []
    scores: dict = {}

    if not (shutil.which("ffprobe") and shutil.which("ffmpeg")):
        return (["ffmpeg/ffprobe not installed (brew install ffmpeg)"],
                {"error": "ffmpeg_missing"})

    meta = _ffprobe(mp4_path)
    vstream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), {})
    astreams = [s for s in meta.get("streams", []) if s.get("codec_type") == "audio"]

    # Duration
    duration = float(meta.get("format", {}).get("duration", 0.0))
    intended = bundle.timing.total_duration or sum(c.duration for c in bundle.clips)
    scores["duration_s"] = round(duration, 2)
    scores["intended_s"] = round(intended, 2)
    if intended and abs(duration - intended) > config.QA_DURATION_TOLERANCE_S:
        failures.append(f"duration {duration:.1f}s off intended {intended:.1f}s")

    # Resolution + aspect
    w, h = int(vstream.get("width", 0)), int(vstream.get("height", 0))
    scores["resolution"] = f"{w}x{h}"
    if (w, h) != (config.WIDTH, config.HEIGHT):
        failures.append(f"resolution {w}x{h} != {config.RESOLUTION}")
    if h == 0 or round(w / h, 3) != round(9 / 16, 3):
        failures.append(f"aspect not 9:16 ({w}x{h})")

    # Audio present + not clipping
    scores["has_audio"] = bool(astreams)
    if not astreams:
        failures.append("no audio stream")
    else:
        max_dbfs = _max_volume_dbfs(mp4_path)
        scores["audio_max_dbfs"] = max_dbfs
        if max_dbfs is not None and max_dbfs > config.QA_AUDIO_MAX_DBFS:
            failures.append(f"audio clipping (max {max_dbfs} dBFS)")

    # Black / frozen frames
    black = _black_seconds(mp4_path)
    scores["black_seconds"] = round(black, 2)
    if black > 0.5:
        failures.append(f"{black:.1f}s of black frames")
    if _has_freeze(mp4_path):
        scores["frozen"] = True
        failures.append("frozen frames detected")

    # Captions fit within the video
    if bundle.captions:
        last = max(c.end for c in bundle.captions)
        scores["last_caption_end"] = round(last, 2)
        if duration and last > duration + 0.5:
            failures.append(f"captions run past end ({last:.1f}s > {duration:.1f}s)")

    return failures, scores


def _ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    try:
        return json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _ffmpeg_stderr(args: list[str]) -> str:
    out = subprocess.run(["ffmpeg", "-hide_banner", *args, "-f", "null", "-"],
                         capture_output=True, text=True)
    return out.stderr or ""


def _max_volume_dbfs(path: str):
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB",
                  _ffmpeg_stderr(["-i", path, "-af", "volumedetect"]))
    return float(m.group(1)) if m else None


def _black_seconds(path: str) -> float:
    err = _ffmpeg_stderr(["-i", path, "-vf", "blackdetect=d=0.3:pic_th=0.98"])
    return sum(float(d) for d in re.findall(r"black_duration:(\d+\.?\d*)", err))


def _has_freeze(path: str) -> bool:
    err = _ffmpeg_stderr(["-i", path, "-vf", "freezedetect=n=-60dB:d=0.6"])
    return "freeze_start" in err
