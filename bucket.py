"""
Timestamp bucketing: given scene windows + a flat timestamped VO list + a flat
timestamped text list, assign each VO segment and text cue to the scene whose
time-bracket contains it. How many VO/text a scene has is COMPUTED from the
timestamps, not authored.

Inputs (absolute seconds):
  scenes: [{start, end, ...scene props (frame, motion_prompt, on_camera_speech...)}]
  vo:     [{start, end, speaker?, line}]      # speaker carries forward if omitted
  text:   [{start, end, text, position?}]
Output: beats[] (the beats.json shape) with per-scene vo_segments + text_cues
(text_cues times made scene-relative).
"""
from __future__ import annotations


def compose_motion(scene: dict) -> str:
    """Build one motion prompt from the structured shot spec (START -> ONE action
    -> END held still -> CAMERA). Falls back to a plain motion_prompt."""
    if scene.get("motion_prompt"):
        return scene["motion_prompt"]
    shot = scene.get("shot") or {}
    parts = []
    if shot.get("start"):  parts.append(f"Start: {shot['start']}.")
    if shot.get("action"): parts.append(f"One action: {shot['action']}.")
    if shot.get("end"):    parts.append(f"End, held still: {shot['end']}.")
    if shot.get("camera"): parts.append(f"Camera: {shot['camera']}.")
    return " ".join(parts)


def bucket_beats(scenes: list[dict], vo: list[dict], text: list[dict]) -> list[dict]:
    scenes = sorted(scenes, key=lambda s: s["start"])
    beats, last_speaker = [], ""
    for i, sc in enumerate(scenes):
        s0, s1 = sc["start"], sc["end"]

        segs = []
        for v in sorted([v for v in vo if s0 <= v["start"] < s1], key=lambda v: v["start"]):
            sp = v.get("speaker") or last_speaker or "VO"   # carry-forward
            last_speaker = sp
            segs.append({"speaker": sp, "line": v["line"]})

        cues = [{"text": t["text"], "start": round(t["start"] - s0, 3),
                 "end": round(t["end"] - s0, 3), "position": t.get("position", "bottom")}
                for t in sorted([t for t in text if s0 <= t["start"] < s1], key=lambda t: t["start"])]

        beat = {k: v for k, v in sc.items() if k not in ("start", "end", "shot")}
        beat.update({"index": i, "duration": round(s1 - s0, 3),
                     "motion_prompt": compose_motion(sc),
                     "negatives": sc.get("negatives", ""),
                     "ambient": sc.get("ambient", ""),
                     "vo_segments": segs, "text_cues": cues})
        beats.append(beat)
    return beats
