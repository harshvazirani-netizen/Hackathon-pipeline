"""
The controlled vocabulary + dispatcher (this is what makes it multi-type).

Each ad-type is a RECIPE that carries everything that differs between types:
  - needs_lipsync : True  -> audio-first flow (voice made first, drives the mouth),
                             voiceover is split PER BEAT.
                    False -> visual-first flow (animate the frame, narrate over it).
  - animator_model: the fal model id for this type's generation.
  - director_focus: a hint to the ingest step about what kind of action to extract.
  - vision_rubric : the QA Layer-3 prompt this type is graded against.

To add a 4th type later: add one AdTypeRecipe entry. Nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdTypeRecipe:
    name: str
    needs_lipsync: bool
    animator_model: str          # fal endpoint id (verify on fal.ai/models)
    director_focus: str
    vision_rubric: str
    animator_alt: str = ""       # A/B alternative model id


RECIPES = {
    "ai_human": AdTypeRecipe(
        name="ai_human",
        needs_lipsync=True,
        animator_model="fal-ai/bytedance/omnihuman/v1.5",          # $0.16/s
        animator_alt="fal-ai/kling-video/ai-avatar/v2/pro",        # $0.115/s
        director_focus="A real person delivering dialogue to camera.",
        vision_rubric=(
            "Grading frames of an AI-human video ad. Score 0-10 on: realistic human "
            "face, accurate lip-sync to the spoken words, natural expression, and "
            "match to the intended storyboard frame. Flag uncanny/garbled."
        ),
    ),
    "fruit_object": AdTypeRecipe(
        name="fruit_object",
        needs_lipsync=True,
        animator_model="fal-ai/kling-video/ai-avatar/v2/pro",      # $0.115/s; animates "any character"
        animator_alt="fal-ai/bytedance/omnihuman/v1.5",            # A/B for object lip-sync
        director_focus="An anthropomorphized object/fruit speaking dialogue to camera.",
        vision_rubric=(
            "Grading frames of a talking-object ad. Score 0-10 on: the object has a "
            "believable moving mouth synced to the words, stays on-model (no melting/"
            "morphing), and matches the storyboard frame. Flag garbled/artifacted."
        ),
    ),
    "pixar_animation": AdTypeRecipe(
        name="pixar_animation",
        needs_lipsync=False,
        animator_model="fal-ai/kling-video/v2.6/pro/image-to-video",  # ~$0.07/s
        animator_alt="fal-ai/pixverse/v4.5/image-to-video",
        director_focus="A 3D/Pixar-style animated scene; describe camera and character motion.",
        vision_rubric=(
            "Grading frames of a 3D/Pixar-style animated ad. Score 0-10 on: polished "
            "3D look, coherent motion (no garble/melting), and match to the storyboard "
            "frame. Flag artifacts."
        ),
    ),
}


def get_recipe(ad_type: str) -> AdTypeRecipe:
    if ad_type not in RECIPES:
        raise ValueError(f"unknown ad_type '{ad_type}'. known: {list(RECIPES)}")
    return RECIPES[ad_type]


def generic_recipe(needs_lipsync: bool, label: str = "other") -> AdTypeRecipe:
    """Build a recipe for a storyboard that ISN'T one of the named presets.

    We never reject an unknown type — the only thing that truly changes the flow
    is whether a character speaks on camera (lip-sync) or not, so we route on that
    and pick a GENERAL model that handles arbitrary subjects/styles.
    """
    if needs_lipsync:
        return AdTypeRecipe(
            name=label or "other",
            needs_lipsync=True,
            animator_model="fal-ai/kling-video/ai-avatar/v2/pro",   # animates "any character" + audio
            director_focus="A character speaking dialogue to camera.",
            vision_rubric=(
                "Grading frames of a talking-character ad. Score 0-10: believable "
                "moving mouth synced to the words, subject stays on-model (no melt/"
                "morph), matches the storyboard frame. Flag garbled/artifacted."
            ),
        )
    return AdTypeRecipe(
        name=label or "other",
        needs_lipsync=False,
        animator_model="fal-ai/kling-video/v2.6/pro/image-to-video",  # any-style motion
        director_focus="Describe the camera and subject motion in the scene.",
        vision_rubric=(
            "Grading frames of a video ad. Score 0-10: coherent motion (no garble/"
            "melting) and match to the storyboard frame. Flag artifacts."
        ),
    )
