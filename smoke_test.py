"""
Step 1 smoke test: get ONE clip out from a single prompt.

Default path is the cartoon-edit pattern (prompt -> keyframe -> animated clip).
Use --t2v for the simplest possible single-call test (no keyframe).
Use --model to A/B a different animator without editing config.py.

Examples:
  python smoke_test.py --prompt "a cheerful cartoon avocado waving hello, flat 2D style"
  python smoke_test.py --prompt "..." --t2v
  python smoke_test.py --prompt "..." --model fal-ai/pixverse/v4.5/image-to-video

Saves the clip + the RAW fal JSON to output/ (the raw JSON seeds Step 2's schema).
"""
import argparse
import datetime as dt
import json
import os

import config
import video_gen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="What to generate (single prompt).")
    ap.add_argument("--motion", default=None,
                    help="Optional separate motion prompt for the I2V stage.")
    ap.add_argument("--duration", type=int, default=config.DEFAULT_CLIP_SECONDS)
    ap.add_argument("--t2v", action="store_true",
                    help="Single-call text-to-video (skip the keyframe stage).")
    ap.add_argument("--model", default=None,
                    help="Override the animator model ID for an A/B test.")
    args = ap.parse_args()

    if args.model:
        config.ANIMATOR_MODEL = args.model
        config.T2V_MODEL = args.model

    # timestamp passed in (Date.now() etc. are fine in normal Python; this is
    # just for unique filenames).
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(config.OUTPUT_DIR, f"{config.AD_TYPE}-{stamp}")

    if args.t2v:
        video_url, raw = video_gen.text_to_video(args.prompt, args.duration)
        bundle = {"prompt": args.prompt, "video_url": video_url,
                  "animator_model": config.ANIMATOR_MODEL, "raw": {"video": raw}}
    else:
        bundle = video_gen.generate_clip(
            args.prompt, motion_prompt=args.motion, duration=args.duration
        )
        video_url = bundle["video_url"]
        if bundle.get("keyframe_url"):
            video_gen.download(bundle["keyframe_url"], base + "-keyframe.png")

    clip_path = video_gen.download(video_url, base + ".mp4")

    # Persist the raw response — this is the input to defining the Step 2 contract.
    with open(base + "-raw.json", "w") as f:
        json.dump(bundle, f, indent=2, default=str)

    est = (config.COST_PER_VIDEO_SECOND_USD * args.duration
           + (0 if args.t2v else config.COST_PER_IMAGE_USD))
    print("\n=== Step 1 result ===")
    print(f"clip:      {clip_path}")
    print(f"raw json:  {base}-raw.json")
    print(f"est. cost: ~${est:.3f}")
    print("Open the clip and judge: on-brand cartoon look? coherent? not garbled?")


if __name__ == "__main__":
    main()
