"""
QA Layer 2: transcribe the rendered audio (faster-whisper) and compare to the
intended script. Low similarity => the wrong words got spoken / muddy audio.

Install when you reach QA:  pip install faster-whisper   (also needs ffmpeg)
Local + CPU is fine at 10 ads/day. Swap to a hosted Whisper API later if needed.
"""
from __future__ import annotations

import difflib
import re

import config

_MODEL = None


def run(bundle, mp4_path: str) -> tuple[list[str], dict]:
    intended = " ".join(c.vo_line for c in bundle.clips if c.vo_line).strip()
    if not intended:
        return [], {"transcript_similarity": None, "note": "no VO to compare"}

    try:
        transcript = _transcribe(mp4_path)
    except ImportError:
        return (["faster-whisper not installed (pip install faster-whisper)"],
                {"error": "whisper_missing"})

    sim = _similarity(intended, transcript)
    scores = {"transcript_similarity": round(sim, 3),
              "transcript": transcript[:500]}
    failures = []
    if sim < config.QA_TRANSCRIPT_MIN_SIMILARITY:
        failures.append(f"transcript similarity {sim:.2f} < {config.QA_TRANSCRIPT_MIN_SIMILARITY}")
    return failures, scores


def _transcribe(path: str) -> str:
    global _MODEL
    from faster_whisper import WhisperModel  # lazy; raises ImportError if absent
    if _MODEL is None:
        _MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _info = _MODEL.transcribe(path)
    return " ".join(seg.text for seg in segments).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
