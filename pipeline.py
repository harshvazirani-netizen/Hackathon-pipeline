"""
End-to-end glue (Stage 6): a job folder -> finished vertical ad, routed by ad-type.

  ingest -> recipe + per-beat clips (each Clip.lipsync set)
  voiceover per-beat (lip-sync beats get audio that drives the mouth;
                      motion beats with a line get audio for an overlay)
  generate  PER BEAT: lipsync beat -> recipe.lipsync_model (image+audio),
                      motion beat  -> recipe.motion_model (image->video)
  assemble (Shotstack) -> QA gate -> ship | retry | dead-letter.

Usage:
  python pipeline.py --job examples/sample_job
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import time
import traceback

import config
import ingest as ingest_mod
import casting
import generate
import voiceover
import assembly
from qa import gate
from schema import AssetBundle


def run(job_dir: str) -> AssetBundle | None:
    recipe, clips = ingest_mod.ingest(job_dir)
    ad_type = recipe.name
    ad_id = _ad_id(job_dir, ad_type)
    work = os.path.join(config.WORK_DIR, ad_id)
    talk = sum(1 for c in clips if c.lipsync)
    print(f"\n{'=' * 60}\nAD {ad_id}  ({ad_type}; {talk} lip-sync + {len(clips) - talk} motion beats)\n{'=' * 60}")

    bundle = AssetBundle(ad_id=ad_id, ad_type=ad_type,
                         vision_rubric=recipe.vision_rubric,
                         script=f"(job: {job_dir})", clips=clips)

    # Voice casting: each screenplay speaker gets their own ElevenLabs voice
    # (manual job.json override > cached voices.json > Claude picks from the pool).
    print("[CAST] voices per speaker ...")
    screenplay = ""
    sp = os.path.join(job_dir, "screenplay.txt")
    if os.path.exists(sp):
        with open(sp) as f:
            screenplay = f.read()
    voice_map = casting.cast(job_dir, bundle.clips, screenplay)

    last_reason = "unknown"
    for attempt in range(1, config.MAX_GENERATION_RETRIES + 2):
        print(f"\n--- attempt {attempt}/{config.MAX_GENERATION_RETRIES + 1} ---")
        try:
            # Per-beat VO first: lip-sync beats need their audio to drive the mouth;
            # motion beats with a line (e.g. end-card VO) get audio for an overlay.
            print("[VO] per-beat ...")
            bundle.captions = voiceover.synthesize_per_beat(bundle.clips, work, voice_map)

            print("[CAPTIONS] timing screenplay text to the voice ...")
            import captions as captions_mod
            bundle.overlay_captions = captions_mod.align_overlay(bundle.clips, bundle.captions)

            print("[GEN] per-beat (lip-sync vs motion) ...")
            generate.generate_clips(bundle.clips, recipe, ad_id)

            bundle.compute_timing()

            print("[ASM] rendering vertical MP4 ...")
            bundle.rendered_path = assembly.render(bundle, os.path.join(work, "final.mp4"))

            print("[QA] gate ...")
            bundle.qa = gate.run_qa(bundle, bundle.rendered_path)

            if bundle.qa.passed:
                shipped = _ship(bundle)
                mode = "CALIBRATION (not enforced)" if bundle.qa.calibration else "PASS"
                print(f"\n✅ {mode} — shipped: {shipped}")
                if bundle.qa.failures:
                    print(f"   would-be QA failures: {bundle.qa.failures}")
                return bundle

            last_reason = "; ".join(bundle.qa.failures) or "QA failed"
            print(f"❌ QA failed: {last_reason}")

        except Exception as e:
            last_reason = f"{type(e).__name__}: {e}"
            print(f"❌ error: {last_reason}")
            traceback.print_exc()

    path = _dead_letter(bundle, last_reason)
    print(f"\n💀 dead-letter after {config.MAX_GENERATION_RETRIES + 1} attempts: {path}\n   reason: {last_reason}")
    return None


def _ad_id(job_dir: str, ad_type: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", os.path.basename(os.path.normpath(job_dir)).lower()).strip("-") or "ad"
    return f"{ad_type}-{base}-{time.strftime('%Y%m%d-%H%M%S')}"


def _ship(bundle: AssetBundle) -> str:
    dest = os.path.join(config.SHIPPED_DIR, bundle.ad_id)
    os.makedirs(dest, exist_ok=True)
    final = os.path.join(dest, "ad.mp4")
    if bundle.rendered_path and os.path.exists(bundle.rendered_path):
        shutil.copy(bundle.rendered_path, final)
    bundle.save(os.path.join(dest, "bundle.json"))
    return final


def _dead_letter(bundle: AssetBundle, reason: str) -> str:
    dest = os.path.join(config.DEAD_LETTER_DIR, bundle.ad_id)
    os.makedirs(dest, exist_ok=True)
    bundle.save(os.path.join(dest, "bundle.json"))
    with open(os.path.join(dest, "reason.txt"), "w") as f:
        f.write(reason + "\n")
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, help="Path to a job folder (job.json + screenplay + storyboard/).")
    args = ap.parse_args()
    run(args.job)


if __name__ == "__main__":
    main()
