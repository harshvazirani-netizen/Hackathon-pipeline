"""
Combine already-generated scenes into one ad and run the DOWNSTREAM funnel
(assemble -> QA gate). No generation, no fal spend — reuses each scene's
fal-hosted clip + per-scene VO.

Usage:  python combine.py --job examples/seekho_tyre --scenes 1,2,3 --out seekho_s1-3
"""
from __future__ import annotations

import argparse
import json
import os

import config
import ingest as ingest_mod
import assembly
from qa import gate
from schema import AssetBundle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--scenes", default="", help="e.g. 1,2,3 (default: all)")
    ap.add_argument("--out", default="combined")
    args = ap.parse_args()

    recipe, all_clips = ingest_mod.ingest(args.job)
    idxs = ([int(x) - 1 for x in args.scenes.split(",")] if args.scenes
            else list(range(len(all_clips))))
    jp = os.path.join(args.job, "job.json")
    burned = set(json.load(open(jp)).get("burned_caption_frames", []) if os.path.exists(jp) else [])
    job_name = os.path.basename(os.path.normpath(args.job))

    clips = []
    for i in idxs:
        c = all_clips[i]
        d = os.path.join(config.WORK_DIR, f"scene-test-{job_name}-{i + 1:02d}")
        rawp = os.path.join(d, "raw.json")
        if not os.path.exists(rawp):
            raise SystemExit(f"scene {i + 1} not generated yet ({rawp} missing).")
        raw = json.load(open(rawp))
        c.video_url = raw["video"]["url"]
        # duration: omnihuman reports it; Kling motion = its gen block (5 or 10s)
        if raw.get("duration"):
            c.duration = float(raw["duration"])
        elif not c.lipsync:
            c.duration = 10.0 if (c.duration or 0) > 5 else 5.0
        # motion beats: lay the per-beat VO; lip-sync beats carry audio in the clip
        if not c.lipsync:
            vo = os.path.join(d, f"vo_{i:02d}.mp3")
            c.audio_path = vo if os.path.exists(vo) else None
        # frames that already have captions burned in -> drop our overlay cue
        if (i + 1) in burned:
            c.text_cues = []
        clips.append(c)

    bundle = AssetBundle(ad_id=args.out, ad_type=recipe.name, add_captions=True,
                         vision_rubric=recipe.vision_rubric, clips=clips)
    bundle.compute_timing()
    out = os.path.join(config.SHIPPED_DIR, f"{args.out}.mp4")
    print(f"[ASM] stitching {len(clips)} scenes -> {bundle.timing.total_duration:.1f}s ...")
    bundle.rendered_path = assembly.render(bundle, out)

    print("[QA] gate ...")
    bundle.qa = gate.run_qa(bundle, bundle.rendered_path)
    bundle.save(os.path.join(config.SHIPPED_DIR, f"{args.out}.bundle.json"))

    print(f"\n✅ combined ad: {bundle.rendered_path}")
    q = bundle.qa
    print(f"[QA] mode={'calibration' if q.calibration else 'enforce'} "
          f"passed={q.passed} layers_run={q.layer_reached}")
    for f in q.failures:
        print(f"   • {f}")


if __name__ == "__main__":
    main()
