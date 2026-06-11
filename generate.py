"""
Generation (Stage 2): storyboard frames -> animated clips, routed by recipe.

  lip-sync types (needs_lipsync=True):
      upload frame + that beat's audio -> OmniHuman / Kling-Avatar
      (image + audio -> talking clip). The clip carries its own voice.
      ==> voiceover MUST have run first (clip.audio_path set).

  pixar (needs_lipsync=False):
      upload frame -> image-to-video with the beat's motion_prompt.
      The clip is silent; narration is added later in assembly.

Each clip's approved storyboard frame is the literal start frame ("match closely").
Downloads every clip locally for QA. Mutates + returns the clips.
"""
from __future__ import annotations

import os

import config
import video_gen
from ad_types import AdTypeRecipe


def generate_clips(clips, recipe: AdTypeRecipe, ad_id: str):
    work = os.path.join(config.WORK_DIR, ad_id)
    os.makedirs(work, exist_ok=True)

    for clip in clips:
        print(f"\n[gen] beat {clip.index + 1}/{len(clips)}  ({recipe.name})")

        # The approved storyboard frame is the visual anchor -> upload for fal.
        clip.start_frame_url = video_gen.upload_file(clip.storyboard_image_path)

        if recipe.needs_lipsync:
            if not clip.audio_path:
                raise RuntimeError(
                    f"beat {clip.index}: lip-sync type needs per-beat audio — "
                    f"run voiceover.synthesize_per_beat() before generation."
                )
            clip.audio_url = video_gen.upload_file(clip.audio_path)
            video_url, _ = video_gen.lipsync_from_image(
                recipe.animator_model, clip.start_frame_url, clip.audio_url,
            )
        else:
            video_url, _ = video_gen.image_to_video(
                clip.start_frame_url, clip.motion_prompt,
                duration=int(clip.duration or config.DEFAULT_CLIP_SECONDS),
                model_id=recipe.animator_model,
            )

        clip.video_url = video_url
        clip.local_path = video_gen.download(
            video_url, os.path.join(work, f"clip_{clip.index:02d}.mp4")
        )
        clip.animator_model = recipe.animator_model
        print(f"[gen] clip -> {clip.local_path}")

    return clips
