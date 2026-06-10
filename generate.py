"""
Generation module: storyboard beats -> generated + downloaded clips.

Within-ad consistency strategy (matches "fresh characters per script"):
  - Beat 0 generates the HERO keyframe.
  - Beats 1..N pass that hero keyframe as a reference image so the same 3D
    character recurs across the ad's clips. No cross-ad character DB needed.

Returns a list[Clip] with both fal URLs and local downloaded paths.
"""
from __future__ import annotations

import os

import config
import video_gen
from schema import Clip


def generate_clips(storyboard: dict, ad_id: str) -> list[Clip]:
    beats = storyboard["beats"]
    work = os.path.join(config.WORK_DIR, ad_id)
    os.makedirs(work, exist_ok=True)

    hero_ref: str | None = None
    clips: list[Clip] = []

    for i, beat in enumerate(beats):
        print(f"\n[gen] beat {i + 1}/{len(beats)}")
        kf_prompt = beat["keyframe_prompt"]
        keyframe_url, _kf_raw = video_gen.generate_keyframe(
            kf_prompt, reference_image_url=hero_ref
        )
        if i == 0:
            hero_ref = keyframe_url  # lock the character for the rest of the ad

        dur = int(beat.get("est_seconds", config.DEFAULT_CLIP_SECONDS))
        video_url, _v_raw = video_gen.image_to_video(keyframe_url, beat["motion_prompt"], dur)

        local_path = video_gen.download(video_url, os.path.join(work, f"clip_{i:02d}.mp4"))

        clips.append(Clip(
            index=i,
            vo_line=beat.get("vo_line", ""),
            keyframe_prompt=kf_prompt,
            motion_prompt=beat["motion_prompt"],
            duration=float(dur),
            keyframe_url=keyframe_url,
            video_url=video_url,
            local_path=local_path,
            keyframe_model=config.KEYFRAME_MODEL,
            animator_model=config.ANIMATOR_MODEL,
        ))

    return clips
