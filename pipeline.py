"""
End-to-end glue (Stage 6): a job folder -> finished vertical ad, routed by ad-type.

  ingest (parse screenplay + pair storyboard frames)  ->  get recipe
  then, by recipe:

    lip-sync (ai_human, fruit_object):   AUDIO-FIRST
       voiceover per-beat -> generate(frame + audio) -> assemble -> QA
       (clips carry their own voice; no separate narration track)

    pixar_animation:                     VISUAL-FIRST
       generate(frame + motion) -> voiceover (narration) -> assemble -> QA

  QA pass -> ship | QA fail -> retry generation | exhausted -> dead-letter.

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
import generate
import voiceover
import assembly
from ad_types import get_recipe
from qa import gate
from schema import AssetBundle, AudioTrack


def run(job_dir: str) -> AssetBundle | None:
    ad_type, clips = ingest_mod.ingest(job_dir)
    recipe = get_recipe(ad_type)
    ad_id = _ad_id(job_dir, ad_type)
    work = os.path.join(config.WORK_DIR, ad_id)
    print(f"\n{'=' * 60}\nAD {ad_id}  ({ad_type}, lip-sync={recipe.needs_lipsync})\n{'=' * 60}")

    bundle = AssetBundle(ad_id=ad_id, ad_type=ad_type,
                         script=f"(job: {job_dir})", clips=clips)

    last_reason = "unknown"
    for attempt in range(1, config.MAX_GENERATION_RETRIES + 2):
        print(f"\n--- attempt {attempt}/{config.MAX_GENERATION_RETRIES + 1} ---")
        try:
            if recipe.needs_lipsync:
                print("[VO] per-beat (drives lip-sync) ...")
                bundle.captions = voiceover.synthesize_per_beat(bundle.clips, work)
                print("[GEN] talking clips (image + audio) ...")
                generate.generate_clips(bundle.clips, recipe, ad_id)
                bundle.audio = AudioTrack()  # clips already contain their voice
            else:
                print("[GEN] animating storyboard frames ...")
                generate.generate_clips(bundle.clips, recipe, ad_id)
                print("[VO] continuous narration ...")
                bundle.audio, bundle.captions = voiceover.synthesize(
                    bundle.clips, os.path.join(work, "vo.mp3"))

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
