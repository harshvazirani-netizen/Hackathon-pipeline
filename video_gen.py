"""
Generation module (Step 1): prompt -> video clip via fal.ai.

Exposes three primitives the rest of the pipeline will build on:
  - generate_keyframe(prompt, ...)      -> image URL   (Stage A)
  - image_to_video(image_url, ...)      -> video URL   (Stage B)
  - text_to_video(prompt, ...)          -> video URL   (single-call fallback)

Plus generate_clip(), the cartoon-edit path: prompt -> keyframe -> clip.

Everything returns the RAW fal response alongside the URL, because Step 2
defines the asset-bundle schema around these real output shapes. Don't throw
the raw responses away — smoke_test.py saves them to output/.
"""
from __future__ import annotations

import os
import time
import requests
import fal_client
from dotenv import load_dotenv

import config

load_dotenv()


def _require_key():
    """Checked at call-time (not import) so the whole pipeline imports without keys."""
    if not os.getenv("FAL_KEY"):
        raise SystemExit(
            "FAL_KEY not set. Copy .env.example to .env and add your key "
            "(https://fal.ai/dashboard/keys)."
        )


def _on_update(update):
    """Stream queue progress to the console so long renders aren't silent."""
    if isinstance(update, fal_client.InProgress):
        for log in update.logs or []:
            print(f"  [fal] {log.get('message', '')}")


def _run(model_id: str, arguments: dict) -> dict:
    """Submit to fal's queue and block until the result is ready.

    Uses subscribe() (queue + auto-poll) — the synchronous path that's right
    for V1. When we scale, the same call becomes fal_client.submit(..., webhook_url=...)
    and the assembly backbone gets pinged on completion.
    """
    _require_key()
    print(f"[fal] calling {model_id} ...")
    t0 = time.time()
    try:
        result = fal_client.subscribe(
            model_id,
            arguments=arguments,
            with_logs=True,
            on_queue_update=_on_update,
        )
    except Exception as e:
        raise RuntimeError(
            f"fal call to '{model_id}' failed: {e}\n"
            f"If this is a 404/'not found', the model ID is stale — check "
            f"https://fal.ai/models and update it in config.py."
        ) from e
    print(f"[fal] done in {time.time() - t0:.1f}s")
    return result


def generate_keyframe(prompt: str, reference_image_url: str | None = None) -> tuple[str, dict]:
    """Stage A: text (+ optional reference) -> a single keyframe image URL."""
    args: dict = {"prompt": prompt, "image_size": {"width": 1080, "height": 1920}}
    if reference_image_url:
        # Reference-edit models (FLUX Kontext / Nano Banana) take image_url(s)
        # to lock a recurring character. Seedream uses image_urls for ref too.
        args["image_urls"] = [reference_image_url]
    result = _run(config.KEYFRAME_MODEL, args)
    url = _first_media_url(result, kind="image")
    return url, result


def image_to_video(image_url: str, motion_prompt: str,
                   duration: int = config.DEFAULT_CLIP_SECONDS) -> tuple[str, dict]:
    """Stage B: animate a keyframe into a clip. Returns video URL + raw response."""
    args = {
        "image_url": image_url,
        "prompt": motion_prompt,
        "duration": str(duration),
        "aspect_ratio": config.ASPECT_RATIO,
    }
    result = _run(config.ANIMATOR_MODEL, args)
    url = _first_media_url(result, kind="video")
    return url, result


def text_to_video(prompt: str,
                  duration: int = config.DEFAULT_CLIP_SECONDS) -> tuple[str, dict]:
    """Single-call fallback (no keyframe). Used by smoke_test.py --t2v."""
    args = {
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": config.ASPECT_RATIO,
    }
    result = _run(config.T2V_MODEL, args)
    url = _first_media_url(result, kind="video")
    return url, result


def generate_clip(prompt: str, motion_prompt: str | None = None,
                  duration: int = config.DEFAULT_CLIP_SECONDS,
                  reference_image_url: str | None = None) -> dict:
    """The cartoon-edit path: prompt -> keyframe -> animated clip.

    Returns a dict with both stage outputs + the local video path. This shape
    is the seed of the asset-bundle 'clip' contract we define in Step 2.
    """
    keyframe_url, keyframe_raw = generate_keyframe(prompt, reference_image_url)
    print(f"[gen] keyframe: {keyframe_url}")
    video_url, video_raw = image_to_video(
        keyframe_url, motion_prompt or prompt, duration
    )
    print(f"[gen] clip: {video_url}")
    return {
        "ad_type": config.AD_TYPE,
        "prompt": prompt,
        "motion_prompt": motion_prompt or prompt,
        "duration": duration,
        "keyframe_url": keyframe_url,
        "video_url": video_url,
        "keyframe_model": config.KEYFRAME_MODEL,
        "animator_model": config.ANIMATOR_MODEL,
        "raw": {"keyframe": keyframe_raw, "video": video_raw},
    }


def download(url: str, dest_path: str) -> str:
    """Download a fal result URL to a local file."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest_path


def _first_media_url(result: dict, kind: str) -> str:
    """fal responses vary by model. Most return {'images':[{'url':...}]} or
    {'video':{'url':...}}. Probe the common shapes and fail loudly otherwise so
    we learn the real shape (which we'll codify in the Step 2 schema)."""
    if kind == "image":
        if result.get("images"):
            return result["images"][0]["url"]
        if result.get("image", {}).get("url"):
            return result["image"]["url"]
    if kind == "video":
        if result.get("video", {}).get("url"):
            return result["video"]["url"]
        if result.get("videos"):
            return result["videos"][0]["url"]
    raise RuntimeError(
        f"Couldn't find a {kind} URL in the fal response. Raw keys: "
        f"{list(result.keys())}. Inspect the saved raw JSON and adjust "
        f"_first_media_url() — this tells us the real output shape for Step 2."
    )
