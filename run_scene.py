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
    clip = clips[args.beat - 1]
    print(f"\nbeat {args.beat}: route={'lipsync' if clip.lipsync else 'motion'}, "
          f"speaker={clip.speaker or '-'}, dur={clip.duration:.0f}s")
    print(f"line: {clip.vo_line or '—'}")

    out = os.path.join(config.WORK_DIR, f"scene-test-{os.path.basename(args.job)}-{args.beat:02d}")
    os.makedirs(out, exist_ok=True)

    if clip.vo_line:
        sp = os.path.join(args.job, "screenplay.txt")
        screenplay = open(sp).read() if os.path.exists(sp) else ""
        vmap = casting.cast(args.job, clips, screenplay)
        print("[VO] ...")
        voiceover.synthesize_per_beat([clip], out, vmap)

    print("[GEN] uploading frame ...")
    clip.start_frame_url = video_gen.upload_file(clip.storyboard_image_path)
    if clip.lipsync:
        clip.audio_url = video_gen.upload_file(clip.audio_path)
        video_url, raw = video_gen.lipsync_from_image(
            recipe.lipsync_model, clip.start_frame_url, clip.audio_url)
    else:
        video_url, raw = video_gen.image_to_video(
            clip.start_frame_url, clip.motion_prompt,
            duration=int(clip.duration or config.DEFAULT_CLIP_SECONDS),
            model_id=recipe.motion_model)

    local = video_gen.download(video_url, os.path.join(out, "clip.mp4"))
    with open(os.path.join(out, "raw.json"), "w") as f:
        json.dump(raw, f, indent=2, default=str)
    print(f"\n✅ scene done: {local}\n   raw: {out}/raw.json")


if __name__ == "__main__":
    main()
