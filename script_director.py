"""
Creative director (front of the generation module): a refined ad script -> beats.

Uses Claude to split the script into 3-6 sequential visual beats. Each beat gets
a keyframe image prompt (3D/Pixar look), a motion prompt, and the VO line spoken
over it. One hero character is defined once (character_bible) and referenced in
every beat so the character stays consistent across this ad's clips.

Output (dict):
  {
    "character_bible": "...",
    "hook_text": "...", "cta_text": "...",
    "beats": [{"vo_line","keyframe_prompt","motion_prompt","est_seconds"}, ...]
  }
"""
from __future__ import annotations

import os
import config

_SYSTEM = (
    "You are a creative director for short vertical (9:16) performance video ads. "
    "Ad-type: cartoon_edit, rendered in a polished 3D / Pixar-like style. "
    "Characters are fresh per script (no recurring mascot), but the hero character "
    "MUST stay visually consistent across this ad's beats."
)

_TOOL = {
    "name": "submit_storyboard",
    "description": "Return the storyboard for this ad.",
    "input_schema": {
        "type": "object",
        "properties": {
            "character_bible": {
                "type": "string",
                "description": "One vivid, fixed description of the hero character "
                               "(species, colors, outfit, proportions) to repeat in every keyframe prompt.",
            },
            "hook_text": {"type": "string", "description": "Punchy on-screen opener (<= 6 words)."},
            "cta_text": {"type": "string", "description": "Call to action (<= 6 words)."},
            "beats": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vo_line": {"type": "string", "description": "Narration spoken over this beat."},
                        "keyframe_prompt": {
                            "type": "string",
                            "description": "Detailed 3D/Pixar still-image prompt for this beat. "
                                           "MUST embed the character_bible so the character is consistent. "
                                           "Vertical 9:16 framing.",
                        },
                        "motion_prompt": {
                            "type": "string",
                            "description": "How the still should animate (camera + character motion), 1-2 sentences.",
                        },
                        "est_seconds": {"type": "integer", "description": "Beat length, 3-8 seconds."},
                    },
                    "required": ["vo_line", "keyframe_prompt", "motion_prompt", "est_seconds"],
                },
            },
        },
        "required": ["character_bible", "hook_text", "cta_text", "beats"],
    },
}


def direct(script: str, target_seconds: int = config.TARGET_AD_SECONDS) -> dict:
    from anthropic import Anthropic  # lazy: import only when called

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set. Add it to .env.")

    client = Anthropic()
    user = (
        f"Refined ad script:\n\"\"\"\n{script}\n\"\"\"\n\n"
        f"Target total length ~{target_seconds}s. Produce {config.MIN_BEATS}-{config.MAX_BEATS} beats. "
        f"Call submit_storyboard with the result."
    )
    resp = client.messages.create(
        model=config.DIRECTOR_MODEL,
        max_tokens=2000,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_storyboard"},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_storyboard":
            data = block.input
            _clamp_beats(data)
            return data
    raise RuntimeError("Director did not return a storyboard tool call.")


def _clamp_beats(data: dict) -> None:
    beats = data.get("beats", [])[: config.MAX_BEATS]
    for b in beats:
        b["est_seconds"] = max(3, min(8, int(b.get("est_seconds", config.DEFAULT_CLIP_SECONDS))))
    data["beats"] = beats
