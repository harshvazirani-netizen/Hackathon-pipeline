"""
Voiceover-only trial: cast voices + TTS every scene. NO fal, NO video.

Produces one mp3 per scene and a single stitched full-ad voiceover track
(scenes placed at their screenplay timecodes via Shotstack — free, audio-only).

Usage:  python voiceover_only.py --job examples/gold_reel
"""
from __future__ import annotations

import argparse
import os

import config
import ingest as ingest_mod
import casting
import voiceover


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    args = ap.parse_args()

    recipe, clips = ingest_mod.ingest(args.job)
    sp = os.path.join(args.job, "screenplay.txt")
    screenplay = open(sp).read() if os.path.exists(sp) else ""

    print(f"\n[LOCALIZE] dialogue -> {config.VOICE_LANGUAGE} ...")
    import localize as localize_mod
    localize_mod.localize(clips)

    print("\n[CAST] locking per-character voices ...")
    vmap = casting.cast(args.job, clips, screenplay)

    work = os.path.join(config.WORK_DIR, "vo-only-" + os.path.basename(os.path.normpath(args.job)))
    print("\n[VO] synthesizing every scene (ElevenLabs only) ...")
    voiceover.synthesize_per_beat(clips, work, vmap)

    print("\n=== per-scene voiceover ===")
    for c in clips:
        if c.audio_path:
            print(f"scene {c.index+1:>2} | {c.speaker:<8} | {c.duration:>4.1f}s | {os.path.basename(c.audio_path)} | {c.vo_line[:44]}")

    merged = _stitch(clips, os.path.join(work, "full_voiceover.mp3"))
    if merged:
        print(f"\n✅ full-ad voiceover: {merged}")
    print(f"   per-scene mp3s in: {work}")


def _stitch(clips, dest: str) -> str | None:
    """Lay each scene's VO at its screenplay timecode -> one mp3 (Shotstack, free)."""
    import assembly
    import requests
    clipped, t = [], 0.0
    for c in clips:
        if c.audio_path:
            url = assembly._ingest_upload(c.audio_path)
            clipped.append({"asset": {"type": "audio", "src": url},
                            "start": round(t, 3), "length": round(c.duration, 3)})
        t += c.duration
    if not clipped:
        return None
    edit = {"timeline": {"tracks": [{"clips": clipped}]},
            "output": {"format": "mp3"}}
    r = requests.post(assembly._base("edit") + "/render", json=edit,
                      headers=assembly._headers(), timeout=60)
    r.raise_for_status()
    url = assembly._poll_render(r.json()["response"]["id"])
    path = assembly._download(url, dest)
    return _normalize(path)


def _normalize(path: str) -> str:
    """Re-encode the stitched track to a clean, loudness-normalized mp3 (libmp3lame
    44.1kHz, -16 LUFS) so it's loud + plays in any player. Fails soft without ffmpeg."""
    import shutil
    import subprocess
    if not path or not shutil.which("ffmpeg"):
        return path
    tmp = path[:-4] + ".norm.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path,
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", tmp],
            check=True,
        )
        os.replace(tmp, path)
    except Exception as e:                               # noqa: BLE001
        print(f"[VO] normalize skipped ({e})")
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


if __name__ == "__main__":
    main()
