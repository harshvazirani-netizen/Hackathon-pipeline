"""
Generation (Stage 2): storyboard frames -> animated clips, routed PER BEAT.

  beat.lipsync == True  (a character speaks on camera):
      upload frame + that beat's audio -> recipe.lipsync_model (OmniHuman /
      Kling-Avatar) -> talking clip that carries its own voice.
      ==> voiceover MUST have run first (clip.audio_path set).

  beat.lipsync == False (silent action / SFX / end card):
      upload frame -> recipe.motion_model (image -> video). Silent clip;
      any narration for this beat is laid over it in assembly.

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

    # Upload each storyboard frame once (reused as a start frame AND as the previous
    # scene's end frame for continuity chaining).
    _url_cache: dict[str, str] = {}

    def frame_url(path: str) -> str:
        if path not in _url_cache:
            _url_cache[path] = video_gen.upload_file(path)
        return _url_cache[path]

    for i, clip in enumerate(clips):
        kind = "lipsync" if clip.lipsync else "motion"
        print(f"\n[gen] beat {clip.index + 1}/{len(clips)}  ({kind}, {recipe.name})")

        # The approved storyboard frame is the visual anchor -> upload for fal.
        clip.start_frame_url = frame_url(clip.storyboard_image_path)

        if clip.lipsync:
            if not clip.audio_path:
                raise RuntimeError(
                    f"beat {clip.index}: lip-sync beat needs audio — "
                    f"run voiceover.synthesize_per_beat() before generation."
                )
            clip.audio_url = video_gen.upload_file(clip.audio_path)
            clip.animator_model = recipe.lipsync_model
            video_url, _ = video_gen.lipsync_from_image(
                recipe.lipsync_model, clip.start_frame_url, clip.audio_url,
            )
        else:
            # Continuity: end this motion scene on the NEXT scene's first frame so
            # the cut flows straight into it (no jump, no black). Skip if the next
            # scene has no frame.
            nxt = clips[i + 1] if i + 1 < len(clips) else None
            end_url = frame_url(nxt.storyboard_image_path) if (nxt and nxt.storyboard_image_path) else None
            clip.animator_model = recipe.motion_model
            video_url, _ = video_gen.image_to_video(
                clip.start_frame_url, clip.motion_prompt,
                duration=clip.duration or config.DEFAULT_CLIP_SECONDS,
                model_id=recipe.motion_model,
                low_motion=clip.low_motion,
                negatives=clip.negatives,
                end_image_url=end_url,
            )

        clip.video_url = video_url
        clip.local_path = video_gen.download(
            video_url, os.path.join(work, f"clip_{clip.index:02d}.mp4")
        )
        print(f"[gen] clip -> {clip.local_path}")

    return clips
