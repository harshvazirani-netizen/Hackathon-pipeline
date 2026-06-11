"""
Timed overlay captions.

The screenplay's per-scene 'text:' is the ONLY caption source, but it must not
sit on screen for the whole scene: each part of the text appears at the moment
the VOICE says the related words. We use the per-word TTS timestamps and have
Claude split the text into 1-3 natural segments and align each to the words it
correlates with (text may be English while the VO is Hindi — semantic match).

Fallback (no key / no word timings / alignment failure): the full text shown
for the full scene, as before.
"""
from __future__ import annotations

import json
import os

import config
from schema import Caption


def align_overlay(clips, word_captions) -> list[Caption]:
    """clips: beats with fixed durations + overlay_text. word_captions: absolute
    per-word Captions from TTS. Returns timed overlay Captions (absolute)."""
    rows, t = [], 0.0
    for c in clips:
        start, end = t, t + c.duration
        t = end
        if not c.overlay_text:
            continue
        words = [{"w": w.text, "s": round(w.start, 2), "e": round(w.end, 2)}
                 for w in word_captions if start - 0.01 <= w.start < end]
        rows.append({"beat": c.index, "text": c.overlay_text,
                     "window": [round(start, 2), round(end, 2)], "words": words})
    if not rows:
        return []
    if not os.getenv("ANTHROPIC_API_KEY") or not any(r["words"] for r in rows):
        return _fallback(clips)
    try:
        return _claude_align(rows)
    except Exception as e:
        print(f"[captions] alignment failed ({e}); using full-scene fallback")
        return _fallback(clips)


def _fallback(clips) -> list[Caption]:
    out, t = [], 0.0
    for c in clips:
        if c.overlay_text:
            out.append(Caption(text=c.overlay_text, start=t, end=t + c.duration))
        t += c.duration
    return out


_TOOL = {
    "name": "submit_timed_captions",
    "description": "Return the on-screen text segments with their display times.",
    "input_schema": {
        "type": "object",
        "properties": {"segments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "One segment of the scene's text, verbatim (do not reword)."},
                "start": {"type": "number", "description": "Seconds (absolute timeline) when it appears."},
                "end": {"type": "number", "description": "Seconds when it disappears."},
            },
            "required": ["text", "start", "end"],
        }}},
        "required": ["segments"],
    },
}


def _claude_align(rows) -> list[Caption]:
    from anthropic import Anthropic

    client = Anthropic()
    resp = client.messages.create(
        model=config.DIRECTOR_MODEL,
        max_tokens=1500,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_timed_captions"},
        messages=[{"role": "user", "content": (
            "Time the on-screen captions of a video ad to the voiceover.\n"
            "For each scene below: split its 'text' into 1-3 natural segments "
            "(split on line breaks or '|' when present; otherwise keep whole; "
            "NEVER reword). Set each segment's start to the moment the "
            "semantically-related words are SPOKEN (the words may be in Hindi "
            "while the text is English — match by meaning). Rules: times stay "
            "inside the scene's window; segments in order, non-overlapping; "
            "each visible at least 1s. A segment ENDS ~0.4s after its related "
            "words finish being spoken — do NOT stretch any segment (including "
            "the last one) to the scene end; once the dialogue it relates to is "
            "over, the text leaves the screen.\n\n"
            f"Scenes:\n{json.dumps(rows, ensure_ascii=False, indent=1)}"
        )}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_timed_captions":
            segs = block.input.get("segments", [])
            out = [Caption(text=s["text"], start=float(s["start"]), end=float(s["end"]))
                   for s in segs if s.get("text")]
            for s in out:
                print(f"[captions] {s.start:>5.1f}-{s.end:<5.1f}  {s.text[:50]}")
            return out
    raise RuntimeError("no segments returned")
