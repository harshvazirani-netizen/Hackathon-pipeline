"""
Run ONE scene/beat of a job through VO + generation (no assembly/QA).
Cheap smoke test before committing to a full ad.

Usage:  python run_scene.py --job examples/gold_reel --beat 1
"""
from __future__ import annotations

import argparse
import json
import os

import config
import ingest as ingest_mod
import casting
import voiceover
import video_gen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--beat", type=int, default=1, help="1-based beat number")
    args = ap.parse_args()

    recipe, clips = ingest_mod.ingest(args.job)
    import localize as localize_mod
    localize_mod.localize(clips)                     # spoken language default (Hindi)
    add_caps = _job_flag(args.job, "add_captions", True)  # off when frames already carry captions
    clip = clips[args.beat - 1]
    print(f"\nbeat {args.beat}: route={'lipsync' if clip.lipsync else 'motion'}, "
          f"speaker={clip.speaker or '-'}, dur={clip.duration:.0f}s, captions={add_caps}")
    print(f"line: {clip.vo_line or '—'}")

    out = os.path.join(config.WORK_DIR, f"scene-test-{os.path.basename(args.job)}-{args.beat:02d}")
    os.makedirs(out, exist_ok=True)

    word_caps = []
    if clip.vo_line:
        sp = os.path.join(args.job, "screenplay.txt")
        screenplay = open(sp).read() if os.path.exists(sp) else ""
        vmap = casting.cast(args.job, clips, screenplay)
        print("[VO] ...")
        word_caps = voiceover.synthesize_per_beat([clip], out, vmap)

    # Build timed text cues for this scene (scene-relative). Prefer the cue sheet
    # (clip.text_cues, each with its own time); else align a single overlay_text to voice.
    cues = []
    if add_caps and clip.text_cues:
        cues = [{"text": tc.text, "start": tc.start, "length": max(tc.end - tc.start, 0.5),
                 "position": tc.position} for tc in clip.text_cues]
    elif add_caps and clip.overlay_text:
        import captions as captions_mod
        cues = [{"text": c.text, "start": c.start, "length": c.end - c.start, "position": "bottom"}
                for c in captions_mod.align_overlay([clip], word_caps)]

    if _job_flag(args.job, "generate_keyframes", False):
        import keyframe as kf_mod
        cb = _job_flag(args.job, "character_bible", "")
        print("[GEN] keyframe from script ...")
        clip.start_frame_url, _ = kf_mod.keyframe_for(clip, recipe, cb)
        print(f"[GEN] keyframe: {clip.start_frame_url[:64]}")
    else:
        print("[GEN] uploading storyboard frame ...")
        clip.start_frame_url = video_gen.upload_file(clip.storyboard_image_path)
    if clip.lipsync:
        clip.audio_url = video_gen.upload_file(clip.audio_path)
        video_url, raw = video_gen.lipsync_from_image(
            recipe.lipsync_model, clip.start_frame_url, clip.audio_url)
    else:
        video_url, raw = video_gen.image_to_video(
            clip.start_frame_url, clip.motion_prompt,
            duration=int(clip.duration or config.DEFAULT_CLIP_SECONDS),
            model_id=recipe.motion_model, low_motion=clip.low_motion)

    local = video_gen.download(video_url, os.path.join(out, "clip.mp4"))
    with open(os.path.join(out, "raw.json"), "w") as f:
        json.dump(raw, f, indent=2, default=str)

    # Always deliver the FINAL version: a motion beat with a VO line gets its
    # voice merged over the clip via Shotstack (sandbox = free). Lip-sync beats
    # already carry their voice.
    if cues or ((not clip.lipsync) and clip.audio_path):
        print("[MERGE] final via Shotstack, free ...")
        local = _merge(video_url, clip.audio_path if not clip.lipsync else None,
                       clip.duration, os.path.join(out, "final.mp4"), cues=cues)

    print(f"\n✅ scene done: {local}\n   raw: {out}/raw.json")


def _job_flag(job_dir: str, key: str, default):
    p = os.path.join(job_dir, "job.json")
    if os.path.exists(p):
        return json.load(open(p)).get(key, default)
    return default


def _merge(video_url: str, audio_path: str | None, duration: float, dest: str,
           cues: list | None = None) -> str:
    import assembly
    import requests
    length = round(duration or 10, 3)
    tracks = []
    if cues:  # timed text cues, each at its own time/position
        tracks.append({"clips": [
            assembly._title(c["text"], c["start"], c["length"], c.get("position", "bottom"))
            for c in cues]})
    if audio_path:
        vo_url = assembly._ingest_upload(audio_path)
        tracks.append({"clips": [{"asset": {"type": "audio", "src": vo_url},
                                  "start": 0, "length": length}]})
    tracks.append({"clips": [{"asset": {"type": "video", "src": video_url},
                              "start": 0, "length": length, "fit": "cover"}]})
    edit = {"timeline": {"background": "#000000", "tracks": tracks,
                         "fonts": [{"src": assembly.CAPTION_FONT_URL}]},
            "output": {"format": "mp4", "size": {"width": config.WIDTH, "height": config.HEIGHT}, "fps": 30}}
    r = requests.post(assembly._base("edit") + "/render", json=edit,
                      headers=assembly._headers(), timeout=60)
    r.raise_for_status()
    url = assembly._poll_render(r.json()["response"]["id"])
    return assembly._download(url, dest)


if __name__ == "__main__":
    main()
