"""
Script -> keyframe generation.

When the storyboard frames are off-script (e.g. screenshots from another reel),
generate a fresh keyframe per scene FROM the script's visual description, so the
animated clip actually depicts the scene. Optional character reference keeps the
person/look consistent across scenes.

Enabled per job via job.json {"generate_keyframes": true, "character_bible": "..."}.
Costs a fal image gen per scene (~$0.03-0.04); animation then runs on the keyframe.
"""
from __future__ import annotations

import config
import video_gen


def keyframe_for(clip, recipe, character_bible: str = "", ref_url: str | None = None) -> tuple[str, dict]:
    """Generate the scene's start frame from its script visual + character bible."""
    bits = [character_bible.strip(), (clip.motion_prompt or "").strip(),
            "Vertical 9:16 composition, photorealistic, cinematic, natural lighting, "
            "no on-screen text or captions."]
    prompt = " ".join(b for b in bits if b)
    return video_gen.generate_image(prompt, recipe.keyframe_model, ref_url)
