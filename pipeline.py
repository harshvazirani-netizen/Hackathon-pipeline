"""
End-to-end glue (Step 6): refined ad script -> finished vertical ad.

  direct (Claude) -> generate clips (fal) -> voiceover (ElevenLabs)
  -> assemble (Shotstack) -> QA gate -> ship | retry | dead-letter

V1: synchronous, one ad at a time, no queue. Retry the generation up to
MAX_GENERATION_RETRIES on QA failure or error; then dead-letter with the reason.

Usage:
  python pipeline.py --script examples/sample_script.txt
  python pipeline.py --text "Tired of tangled charging cables? ..."
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import time
import traceback

import config
import script_director
import generate
import voiceover
import assembly
from qa import gate
from schema import AssetBundle, OverlayMetadata


def run(script: str, ad_type: str = config.AD_TYPE) -> AssetBundle | None:
    ad_id = _ad_id(script)
    print(f"\n{'=' * 60}\nAD {ad_id}  ({ad_type})\n{'=' * 60}")

    # Step 1: creative direction (script -> storyboard)
    print("[1/5] directing storyboard ...")
    storyboard = script_director.direct(script)

    bundle = AssetBundle(
        ad_id=ad_id,
        ad_type=ad_type,
        script=script,
        character_bible=storyboard.get("character_bible", ""),
        overlay_metadata=OverlayMetadata(
            hook_text=storyboard.get("hook_text"),
            cta_text=storyboard.get("cta_text"),
        ),
    )

    last_reason = "unknown"
    for attempt in range(1, config.MAX_GENERATION_RETRIES + 2):
        print(f"\n--- attempt {attempt}/{config.MAX_GENERATION_RETRIES + 1} ---")
        try:
            print("[2/5] generating clips ...")
            bundle.clips = generate.generate_clips(storyboard, ad_id)

            print("[3/5] voiceover ...")
            vo_path = os.path.join(config.WORK_DIR, ad_id, "vo.mp3")
            bundle.audio, bundle.captions = voiceover.synthesize(bundle.clips, vo_path)
            bundle.compute_timing()

            print("[4/5] assembling ...")
            out_mp4 = os.path.join(config.WORK_DIR, ad_id, "final.mp4")
            bundle.rendered_path = assembly.render(bundle, out_mp4)

            print("[5/5] QA gate ...")
            bundle.qa = gate.run_qa(bundle, bundle.rendered_path)

            if bundle.qa.passed:
                shipped = _ship(bundle)
                mode = "CALIBRATION (not enforced)" if bundle.qa.calibration else "PASS"
                print(f"\n✅ {mode} — shipped: {shipped}")
                if bundle.qa.failures:
                    print(f"   (would-be QA failures: {bundle.qa.failures})")
                return bundle

            last_reason = "; ".join(bundle.qa.failures) or "QA failed"
            print(f"❌ QA failed: {last_reason}")

        except Exception as e:  # any stage error -> retry
            last_reason = f"{type(e).__name__}: {e}"
            print(f"❌ error: {last_reason}")
            traceback.print_exc()

    path = _dead_letter(bundle, last_reason)
    print(f"\n💀 dead-letter after {config.MAX_GENERATION_RETRIES + 1} attempts: {path}\n   reason: {last_reason}")
    return None


def _ad_id(script: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", script.lower()).strip("-")[:24] or "ad"
    return f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--script", help="Path to a .txt ad script.")
    g.add_argument("--text", help="Inline ad script text.")
    args = ap.parse_args()

    script = open(args.script).read() if args.script else args.text
    run(script)


if __name__ == "__main__":
    main()
