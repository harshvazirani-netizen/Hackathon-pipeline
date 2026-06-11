"""
Localize the spoken dialogue (config.VOICE_LANGUAGE; default Hindi).

Each VO line is translated to natural, spoken Hindi (Devanagari) the way Indian
reels actually talk — so the cast Indian voice speaks Hindi rather than English.
Numbers, ₹ amounts, % and brand/English product names are preserved. The source
line is kept on the clip as vo_original; on-screen caption text (overlay_text) is
NOT touched here. No-op if target is "en" or a line is already in the target.
"""
from __future__ import annotations

import json
import os
import re

import config


def localize(clips, target: str | None = None) -> None:
    target = target or config.VOICE_LANGUAGE
    if target != "hi":
        return
    todo = [c for c in clips if c.vo_line and not re.search(r"[ऀ-ॿ]", c.vo_line)]
    if not todo or not os.getenv("ANTHROPIC_API_KEY"):
        return

    translations = _translate([c.vo_line for c in todo])
    for c, hi in zip(todo, translations):
        if hi:
            c.vo_original = c.vo_line
            c.vo_line = hi
            print(f"[localize] {c.vo_original[:34]!r} -> {hi[:40]!r}")


_TOOL = {
    "name": "submit_translations",
    "description": "Return the Hindi translation for each line, in order.",
    "input_schema": {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "hindi": {"type": "string"},
            },
            "required": ["index", "hindi"],
        }}},
        "required": ["lines"],
    },
}


def _translate(lines: list[str]) -> list[str]:
    from anthropic import Anthropic

    numbered = "\n".join(f"{i}: {ln}" for i, ln in enumerate(lines))
    resp = Anthropic().messages.create(
        model=config.DIRECTOR_MODEL,
        max_tokens=2000,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_translations"},
        messages=[{"role": "user", "content": (
            "Translate each ad VO line into natural, conversational spoken HINDI "
            "(Devanagari script) — the way a real Indian social-media reel host talks, "
            "not stiff/formal. Keep it punchy and the same length/energy. PRESERVE: "
            "numbers, ₹ amounts, percentages, and brand/product names (e.g. SudhSvad) — "
            "common English business words (rally, correction, target) may stay as "
            "natural Hinglish if that's how people actually say them. Return one Hindi "
            "line per input index.\n\n"
            f"Lines:\n{numbered}"
        )}],
    )
    out = {ln["index"]: ln["hindi"] for block in resp.content
           if getattr(block, "type", None) == "tool_use" and block.name == "submit_translations"
           for ln in block.input.get("lines", [])}
    return [out.get(i, "") for i in range(len(lines))]
