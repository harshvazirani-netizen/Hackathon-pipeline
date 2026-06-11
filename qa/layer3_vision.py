"""
QA Layer 3 (priciest): sample keyframes from the rendered MP4 and score them with
Claude vision against THIS ad-type's rubric (pulled from its recipe) — so an
ai_human ad is graded on lip-sync + face realism, a fruit_object on object mouth
sync, a pixar on 3D polish.

Needs ffmpeg (frame sampling) + ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile

import config

_JSON_INSTRUCTION = (
    ' Return JSON only: {"score": <0-10>, "garbled": <bool>, "notes": "<short>"}'
)


def run(bundle, mp4_path: str) -> tuple[list[str], dict]:
    if not shutil.which("ffmpeg"):
        return (["ffmpeg not installed (brew install ffmpeg)"], {"error": "ffmpeg_missing"})
    if not os.getenv("ANTHROPIC_API_KEY"):
        return (["ANTHROPIC_API_KEY not set"], {"error": "no_anthropic_key"})

    frames = _sample_frames(mp4_path, config.QA_VISION_SAMPLE_FRAMES)
    if not frames:
        return (["could not sample frames"], {"error": "no_frames"})

    rubric = bundle.vision_rubric or (
        "Score the frames 0-10 for coherence and match to the intended look. "
        "Flag garbled/artifacted.")
    result = _score(frames, rubric)
    score = float(result.get("score", 0))
    scores = {"vision_score": score,
              "garbled": result.get("garbled"),
              "vision_notes": result.get("notes", "")}
    failures = []
    if score < config.QA_VISION_MIN_SCORE:
        failures.append(f"vision score {score} < {config.QA_VISION_MIN_SCORE}")
    if result.get("garbled"):
        failures.append("vision: garbled/artifacted frames")
    return failures, scores


def _sample_frames(path: str, n: int) -> list[bytes]:
    frames = []
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "f_%02d.jpg")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", path,
             "-vf", "thumbnail,fps=1/2", "-frames:v", str(n), out],
            capture_output=True, text=True,
        )
        for fn in sorted(os.listdir(d)):
            with open(os.path.join(d, fn), "rb") as f:
                frames.append(f.read())
    return frames[:n]


def _score(frames: list[bytes], rubric: str) -> dict:
    import json
    from anthropic import Anthropic

    client = Anthropic()
    content = [{"type": "text", "text": rubric + _JSON_INSTRUCTION}]
    for fb in frames:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.b64encode(fb).decode()},
        })

    resp = client.messages.create(
        model=config.VISION_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"score": 0, "garbled": True, "notes": f"unparseable: {text[:120]}"}
