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

    # Always deliver the FINAL version: a motion beat with a VO line gets its
    # voice merged over the clip via Shotstack (sandbox = free). Lip-sync beats
    # already carry their voice.
    if clip.overlay_text or ((not clip.lipsync) and clip.audio_path):
        print("[MERGE] final (VO + caption) via Shotstack, free ...")
        local = _merge(video_url, clip.audio_path if not clip.lipsync else None,
                       clip.duration, os.path.join(out, "final.mp4"),
                       caption=clip.overlay_text)

    print(f"\n✅ scene done: {local}\n   raw: {out}/raw.json")


def _merge(video_url: str, audio_path: str | None, duration: float, dest: str,
           caption: str = "") -> str:
    import assembly
    import requests
    length = round(duration or 10, 3)
    tracks = []
    if caption:
        tracks.append({"clips": [{
            "asset": {"type": "title", "text": assembly._strip_emoji(caption),
                      "style": "subtitle", "size": "medium", "position": "bottom"},
            "start": 0, "length": length}]})
    if audio_path:
        vo_url = assembly._ingest_upload(audio_path)
        tracks.append({"clips": [{"asset": {"type": "audio", "src": vo_url},
                                  "start": 0, "length": length}]})
    tracks.append({"clips": [{"asset": {"type": "video", "src": video_url},
                              "start": 0, "length": length, "fit": "cover"}]})
    edit = {"timeline": {"background": "#000000", "tracks": tracks},
            "output": {"format": "mp4", "size": {"width": config.WIDTH, "height": config.HEIGHT}, "fps": 30}}
    r = requests.post(assembly._base("edit") + "/render", json=edit,
                      headers=assembly._headers(), timeout=60)
    r.raise_for_status()
    url = assembly._poll_render(r.json()["response"]["id"])
    return assembly._download(url, dest)


if __name__ == "__main__":
    main()
