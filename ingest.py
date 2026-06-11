"""
Ingest (new Stage 1): a job folder -> ad_type + a list of beats paired to frames.

The upstream team already broke the script into beats and drew a storyboard frame
per beat, so we DON'T invent anything. Claude is used only to PARSE the screenplay
into per-beat {dialogue, action, timing}, aligned 1:1 with the storyboard images.

Expected job folder:
  job/
  ├── job.json            # {"ad_type": "ai_human"|"fruit_object"|"pixar_animation"}
  │                       #   or {"ad_type":"auto"} / omit it -> detect from the storyboard
  ├── screenplay.txt      # .txt / .fountain / .md  (PDF/.fdx extractors = TODO)
  └── storyboard/
      ├── beat_01.png     # frame for beat 1  (sorted order == beat order)
      ├── beat_02.png
      └── ...

Returns: (recipe, [Clip, ...]) — recipe is a named preset OR a generic recipe built
on the fly for storyboards outside the 3 presets. Each Clip has
storyboard_image_path + vo_line + motion_prompt + duration filled.
"""
from __future__ import annotations

import glob
import json
import os

from ad_types import RECIPES, AdTypeRecipe, get_recipe, generic_recipe
from schema import Clip

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def ingest(job_dir: str) -> tuple[AdTypeRecipe, list[Clip]]:
    screenplay = _read_screenplay(job_dir)
    frames = _list_frames(job_dir)
    if not frames:
        raise SystemExit(f"No storyboard images found in {job_dir}/storyboard/")

    # The storyboard decides the type (explicit job.json is only an optional
    # override). Anything outside the 3 presets gets a generic recipe.
    recipe = _resolve_recipe(job_dir, frames[0], screenplay)

    beats = _parse_beats(screenplay, len(frames), recipe.director_focus)

    n = min(len(beats), len(frames))
    if len(beats) != len(frames):
        print(f"[ingest] WARNING: {len(beats)} screenplay beats vs {len(frames)} "
              f"storyboard frames — aligning first {n} by order.")

    clips: list[Clip] = []
    for i in range(n):
        b = beats[i]
        clips.append(Clip(
            index=i,
            vo_line=b.get("vo_line", ""),
            motion_prompt=b.get("motion_prompt", ""),
            duration=float(b.get("duration_seconds", 5) or 5),
            storyboard_image_path=frames[i],
            animator_model=recipe.animator_model,
        ))
    print(f"[ingest] {recipe.name} (lip-sync={recipe.needs_lipsync}): "
          f"{len(clips)} beats paired with storyboard frames")
    return recipe, clips


def _resolve_recipe(job_dir: str, first_frame: str, screenplay: str) -> AdTypeRecipe:
    """Decide the recipe by LOOKING AT the storyboard. Order:
      1. explicit job.json ad_type naming a known preset -> use it (override).
      2. else classify from the first frame:
           - matches a known preset -> that recipe
           - anything else ('other') -> generic recipe routed by needs_lipsync.
    We never reject a storyboard for being outside the 3 presets.
    """
    p = os.path.join(job_dir, "job.json")
    if os.path.exists(p):
        with open(p) as f:
            declared = json.load(f).get("ad_type")
        if declared and declared != "auto" and declared in RECIPES:
            print(f"[ingest] ad_type (explicit override): {declared}")
            return get_recipe(declared)

    c = _classify(first_frame, screenplay)
    if c["ad_type"] in RECIPES:
        print(f"[ingest] ad_type (from storyboard): {c['ad_type']}")
        return get_recipe(c["ad_type"])

    label = (c.get("subject") or "other").strip().lower().replace(" ", "_")[:24]
    print(f"[ingest] outside the presets -> generic recipe '{label}' "
          f"(lip-sync={c['needs_lipsync']}): {c.get('reason', '')}")
    return generic_recipe(bool(c["needs_lipsync"]), label)


def _classify(frame_path: str, screenplay: str) -> dict:
    """Look at the first storyboard frame (+ screenplay excerpt) and report what it
    is. Returns one of the 3 presets OR 'other', PLUS needs_lipsync — the one
    property that changes the flow for an unknown type."""
    import base64
    from anthropic import Anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set (needed to read the type from the storyboard).")

    with open(frame_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    media = "image/png" if frame_path.lower().endswith(".png") else "image/jpeg"

    tool = {
        "name": "describe_ad",
        "description": "Report the ad type and whether a character speaks on camera.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ad_type": {"type": "string", "enum": list(RECIPES) + ["other"],
                            "description": "A named preset, or 'other' if none of them."},
                "needs_lipsync": {"type": "boolean",
                                  "description": "True if a character speaks ON camera (visible mouth moving)."},
                "subject": {"type": "string", "description": "Short label, e.g. 'talking dog', 'claymation toy'."},
                "confidence": {"type": "number", "description": "0..1"},
                "reason": {"type": "string"},
            },
            "required": ["ad_type", "needs_lipsync", "subject", "confidence", "reason"],
        },
    }
    client = Anthropic()
    resp = client.messages.create(
        model=__import__("config").DIRECTOR_MODEL,
        max_tokens=400,
        tools=[tool],
        tool_choice={"type": "tool", "name": "describe_ad"},
        messages=[{"role": "user", "content": [
            {"type": "text", "text": (
                "Look at this ad's first storyboard frame + screenplay excerpt and describe it.\n"
                "Known presets:\n"
                "- ai_human: a realistic human person on camera\n"
                "- fruit_object: an anthropomorphized object/fruit character\n"
                "- pixar_animation: a 3D / Pixar-style animated scene\n"
                "If it is none of these, use 'other' and still set needs_lipsync "
                "(does a character speak on camera?).\n\n"
                f"Screenplay excerpt:\n{screenplay[:600]}")},
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": img_b64}},
        ]}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "describe_ad":
            data = block.input
            if data.get("confidence", 0) < 0.6:
                print(f"[ingest] ⚠ low-confidence read ({data.get('confidence')}): {data.get('reason')}")
            return data
    raise RuntimeError("Classify: Claude did not return a description.")


def _read_screenplay(job_dir: str) -> str:
    for name in ("screenplay.txt", "screenplay.fountain", "screenplay.md", "screenplay"):
        p = os.path.join(job_dir, name)
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
    # tolerate any single text-ish file at the top level
    cands = [p for p in glob.glob(os.path.join(job_dir, "*"))
             if p.lower().endswith((".txt", ".fountain", ".md"))]
    if cands:
        with open(cands[0]) as f:
            return f.read()
    raise SystemExit(f"No screenplay text file found in {job_dir} "
                     f"(.txt/.fountain/.md). PDF/.fdx extraction is a TODO.")


def _list_frames(job_dir: str) -> list[str]:
    sb = os.path.join(job_dir, "storyboard")
    files = [p for p in glob.glob(os.path.join(sb, "*")) if p.lower().endswith(_IMG_EXT)]
    return sorted(files)  # filename order == beat order (beat_01, beat_02, ...)


_TOOL = {
    "name": "submit_beats",
    "description": "Return the screenplay parsed into per-beat dialogue, action and timing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "beats": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "vo_line": {"type": "string", "description": "Exact dialogue/narration spoken in this beat."},
                        "motion_prompt": {"type": "string", "description": "The action/camera movement in this beat, 1-2 sentences."},
                        "duration_seconds": {"type": "number", "description": "Beat length in seconds, from the screenplay's timing if present."},
                    },
                    "required": ["vo_line", "motion_prompt", "duration_seconds"],
                },
            }
        },
        "required": ["beats"],
    },
}


def _parse_beats(screenplay: str, n_frames: int, director_focus: str) -> list[dict]:
    from anthropic import Anthropic  # lazy

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set. Add it to .env.")

    client = Anthropic()
    system = (
        "You parse an already-finished screenplay into beats. Do NOT invent or add "
        f"content. Context: {director_focus} The storyboard has {n_frames} frames, one "
        f"per beat — return EXACTLY {n_frames} beats in screenplay order, each matching "
        "its frame. Extract the exact spoken line, the action, and the timing."
    )
    resp = client.messages.create(
        model=__import__("config").DIRECTOR_MODEL,
        max_tokens=3000,
        system=system,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_beats"},
        messages=[{"role": "user", "content": f"Screenplay:\n\"\"\"\n{screenplay}\n\"\"\""}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_beats":
            return block.input.get("beats", [])
    raise RuntimeError("Ingest: Claude did not return parsed beats.")
