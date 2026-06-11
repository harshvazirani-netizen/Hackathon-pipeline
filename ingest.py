"""
Ingest (new Stage 1): a job folder -> ad_type + a list of beats paired to frames.

The upstream team already broke the script into beats and drew a storyboard frame
per beat, so we DON'T invent anything. Claude is used only to PARSE the screenplay
into per-beat {dialogue, action, timing}, aligned 1:1 with the storyboard images.

Expected job folder:
  job/
  ├── job.json            # {"ad_type": "ai_human" | "fruit_object" | "pixar_animation"}
  ├── screenplay.txt      # .txt / .fountain / .md  (PDF/.fdx extractors = TODO)
  └── storyboard/
      ├── beat_01.png     # frame for beat 1  (sorted order == beat order)
      ├── beat_02.png
      └── ...

Returns: (ad_type, [Clip, ...]) with each Clip's storyboard_image_path + vo_line +
motion_prompt + duration filled. Generation/voiceover fill the rest.
"""
from __future__ import annotations

import glob
import json
import os

from ad_types import get_recipe
from schema import Clip

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def ingest(job_dir: str) -> tuple[str, list[Clip]]:
    ad_type = _read_ad_type(job_dir)
    recipe = get_recipe(ad_type)

    screenplay = _read_screenplay(job_dir)
    frames = _list_frames(job_dir)
    if not frames:
        raise SystemExit(f"No storyboard images found in {job_dir}/storyboard/")

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
    print(f"[ingest] {ad_type}: {len(clips)} beats paired with storyboard frames")
    return ad_type, clips


def _read_ad_type(job_dir: str) -> str:
    p = os.path.join(job_dir, "job.json")
    if not os.path.exists(p):
        raise SystemExit(f"Missing {p} (must contain {{'ad_type': ...}}).")
    with open(p) as f:
        return json.load(f)["ad_type"]


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
