"""
fal.ai primitives — the generation layer every recipe calls through.

  upload_file(path)                          -> hosted URL (storyboard frame / audio)
  image_to_video(img_url, motion, model_id)  -> clip URL  (pixar: visual-first)
  lipsync_from_image(model_id, img, audio)   -> clip URL  (ai_human/fruit_object: audio-first)
  download(url, dest)                         -> local path

Keys are checked at call-time (not import) so the whole pipeline imports without
a FAL_KEY. Each model's exact arg names should be confirmed on its fal.ai page.
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
    if not os.getenv("FAL_KEY"):
        raise SystemExit(
            "FAL_KEY not set. Copy .env.example to .env and add your key "
            "(https://fal.ai/dashboard/keys)."
        )


def _on_update(update):
    """Stream queue progress so long renders aren't silent."""
    if isinstance(update, fal_client.InProgress):
        for log in update.logs or []:
            print(f"  [fal] {log.get('message', '')}")


def _run(model_id: str, arguments: dict) -> dict:
    """Submit to fal's queue and block until the result is ready (subscribe = queue
    + auto-poll). At scale this becomes submit(..., webhook_url=...)."""
    _require_key()
    print(f"[fal] calling {model_id} ...")
    t0 = time.time()
    try:
        result = fal_client.subscribe(
            model_id, arguments=arguments, with_logs=True, on_queue_update=_on_update,
        )
    except Exception as e:
        raise RuntimeError(
            f"fal call to '{model_id}' failed: {e}\n"
            f"If this is a 404/'not found', the model id is stale — check "
            f"https://fal.ai/models and update it in ad_types.py."
        ) from e
    print(f"[fal] done in {time.time() - t0:.1f}s")
    return result


def upload_file(local_path: str) -> str:
    """Upload a local file (storyboard frame, VO clip) to fal; return a fetchable URL.
    Images smaller than the video models' 300x300 minimum are upscaled first."""
    _require_key()
    local_path = _ensure_min_size(local_path)
    return fal_client.upload_file(local_path)


def generate_image(prompt: str, model_id: str, ref_url: str | None = None) -> tuple[str, dict]:
    """Text -> a 9:16 keyframe image (fal image model). Optional reference image for
    character/style consistency. Verify arg names per model on fal.ai/models."""
    args: dict = {"prompt": prompt, "image_size": {"width": 1080, "height": 1920}}
    if ref_url:
        args["image_urls"] = [ref_url]   # reference-conditioned models (Seedream/FLUX/Nano-Banana)
    result = _run(model_id, args)
    return _first_media_url(result, kind="image"), result


def _ensure_min_size(path: str, min_side: int = 512) -> str:
    """Kling/OmniHuman reject images under 300x300 (e.g. contact-sheet slices).
    Upscale small images to a temp copy; non-images pass through untouched."""
    if not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return path
    try:
        from PIL import Image
    except ImportError:
        return path
    im = Image.open(path)
    if min(im.size) >= min_side:
        return path
    scale = min_side / min(im.size)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    out = path.rsplit(".", 1)[0] + ".upscaled.jpg"
    im.convert("RGB").save(out, quality=92)
    print(f"[gen] frame under {min_side}px -> upscaled to {im.size}")
    return out


def image_to_video(image_url: str, motion_prompt: str,
                   duration: int = config.DEFAULT_CLIP_SECONDS,
                   model_id: str | None = None,
                   low_motion: bool = False,
                   negatives: str = "") -> tuple[str, dict]:
    """Visual-first animation (pixar): animate a still into a clip. model_id is the
    recipe's animator and is required."""
    if not model_id:
        raise ValueError("image_to_video requires model_id (from the recipe).")
    # Kling 2.6 I2V schema (verified on fal): start_image_url + prompt (required),
    # duration enum "5"/"10" only, NO aspect_ratio, generate_audio default true.
    # Motion clips are silent here (SFX/VO added in assembly), so audio is off; the
    # clip is trimmed to the beat's timeline length during assembly.
    prompt = motion_prompt or "subtle, natural motion; keep the framing steady"
    args = {
        "start_image_url": image_url,
        "prompt": prompt,
        "duration": "10" if float(duration) > 5 else "5",   # block must cover the scene
        "generate_audio": False,
    }
    negs = []
    if negatives:                       # per-scene NEGATIVES from the screenplay
        negs.append(negatives)
    if low_motion:
        # Text/product/graphic scenes: near-static so Kling doesn't melt the lettering.
        args["prompt"] = (prompt + " Hold the camera nearly static with only very "
                          "subtle movement; keep all text, logos and packaging perfectly "
                          "sharp, stable and legible — do not warp, morph, or animate any letters.")
        negs.append("warped text, distorted text, morphing letters, gibberish text, "
                    "changing logo, melting, flicker, blur, distort, low quality")
    if negs:
        args["negative_prompt"] = ", ".join(negs)
    result = _run(model_id, args)
    return _first_media_url(result, kind="video"), result


def lipsync_from_image(model_id: str, image_url: str, audio_url: str,
                       prompt: str | None = None) -> tuple[str, dict]:
    """Audio-first talking video (ai_human / fruit_object): drive a still image with
    an audio track so the subject lip-syncs (OmniHuman / Kling AI-Avatar). Clip
    length = audio length.

    NOTE: confirm arg names per model on its fal page (image_url + audio_url is the
    common shape; some take 'audio'/'driven_audio'). One-spot fix if needed."""
    args = {"image_url": image_url, "audio_url": audio_url}
    if prompt:
        args["prompt"] = prompt
    result = _run(model_id, args)
    return _first_media_url(result, kind="video"), result


def download(url: str, dest_path: str) -> str:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest_path


def _first_media_url(result: dict, kind: str) -> str:
    """fal responses vary by model. Probe the common shapes; fail loudly otherwise
    so the first live run tells us the real shape (the *-raw payload)."""
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
        f"{list(result.keys())}. Adjust _first_media_url() to this model's shape."
    )
