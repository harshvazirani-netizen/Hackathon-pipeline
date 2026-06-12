"""
Controlled vocabulary + dispatcher.

A recipe carries the per-type choices. Crucially each recipe has TWO generation
models, because lip-sync is a PER-BEAT property, not per-ad (an ad like a
microdrama mixes talking beats and silent action beats):

  - lipsync_model : talk beats (a character speaks on camera) — image + audio -> talking
  - motion_model  : silent beats (action / SFX / end-card) — image -> video

The per-beat router (generate.py) picks one per beat based on Clip.lipsync.

To add a 4th type: add one AdTypeRecipe. Anything not matched gets generic_recipe().
"""
from __future__ import annotations

from dataclasses import dataclass

# Shared default for silent / motion beats across all types (any visual style).
_MOTION = "fal-ai/kling-video/v2.6/pro/image-to-video"   # ~$0.07/s


@dataclass(frozen=True)
class AdTypeRecipe:
    name: str
    lipsync_model: str           # talk beats (verify ids on fal.ai/models)
    motion_model: str            # silent beats
    director_focus: str
    vision_rubric: str
    keyframe_model: str = "fal-ai/bytedance/seedream/v4/text-to-image"  # script -> on-script keyframe


RECIPES = {
    "ai_human": AdTypeRecipe(
        name="ai_human",
        lipsync_model="fal-ai/bytedance/omnihuman/v1.5",   # $0.16/s, realistic human + audio
        motion_model=_MOTION,
        director_focus="Realistic human characters; some beats are dialogue to camera, some are action.",
        vision_rubric=(
            "Grading frames of an AI-human video ad. Score 0-10: realistic human face, "
            "accurate lip-sync where a character speaks, natural expression, match to the "
            "storyboard frame. Flag uncanny/garbled."
        ),
    ),
    "fruit_object": AdTypeRecipe(
        name="fruit_object",
        lipsync_model="fal-ai/kling-video/ai-avatar/v2/pro",  # $0.115/s, animates "any character" + audio
        motion_model=_MOTION,
        director_focus="An anthropomorphized object/fruit character; some beats talk, some are action.",
        vision_rubric=(
            "Grading frames of a talking-object ad. Score 0-10: believable moving mouth "
            "synced to the words on talk beats, object stays on-model (no melt/morph), "
            "matches the storyboard frame. Flag garbled."
        ),
    ),
    "pixar_animation": AdTypeRecipe(
        name="pixar_animation",
        lipsync_model="fal-ai/kling-video/ai-avatar/v2/pro",  # if a 3D character talks on camera
        motion_model=_MOTION,
        director_focus="A 3D/Pixar-style animated scene; describe camera and character motion.",
        vision_rubric=(
            "Grading frames of a 3D/Pixar-style animated ad. Score 0-10: polished 3D look, "
            "coherent motion (no garble/melt), match to the storyboard frame. Flag artifacts."
        ),
    ),
}


def get_recipe(ad_type: str) -> AdTypeRecipe:
    if ad_type not in RECIPES:
        raise ValueError(f"unknown ad_type '{ad_type}'. known: {list(RECIPES)}")
    return RECIPES[ad_type]


def generic_recipe(label: str = "other") -> AdTypeRecipe:
    """A recipe for storyboards that aren't one of the named presets. Uses general
    models that handle arbitrary subjects/styles; per-beat routing still applies."""
    return AdTypeRecipe(
        name=label or "other",
        lipsync_model="fal-ai/kling-video/ai-avatar/v2/pro",  # animates "any character" + audio
        motion_model=_MOTION,
        director_focus="Describe each beat's action and camera; some beats may have dialogue.",
        vision_rubric=(
            "Grading frames of a video ad. Score 0-10: coherent motion (no garble/melt), "
            "believable lip-sync on any talk beats, match to the storyboard frame. Flag artifacts."
        ),
    )
