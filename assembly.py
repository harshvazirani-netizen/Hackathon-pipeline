"""
Assembly backbone (Step 4): AssetBundle -> rendered vertical MP4 via Shotstack.

Shotstack is JSON-driven and async: POST an Edit, poll the render, download the
result. It pulls assets from URLs, so:
  - clip videos already have fal-hosted URLs (used directly),
  - the local VO mp3 + any local logo are uploaded via the Ingest API first.

Layout (Shotstack renders earlier tracks ON TOP):
  track 0: captions (+ optional logo)   <- on top
  track 1: VO narration (audio)
  track 2: video clips, laid end to end
  soundtrack: optional background music

The Ingest upload + the exact caption styling are the two spots most likely to
need a tweak on first live run.
"""
from __future__ import annotations

import os
import time

import requests

import config

_TIMEOUT = 600  # seconds to wait for a render


def _base(kind: str) -> str:
    return f"https://api.shotstack.io/{kind}/{config.SHOTSTACK_ENV}"


def _headers() -> dict:
    key = os.getenv("SHOTSTACK_API_KEY")
    if not key:
        raise SystemExit("SHOTSTACK_API_KEY not set. Add it to .env.")
    return {"x-api-key": key, "Content-Type": "application/json"}


def render(bundle, out_path: str) -> str:
    """Render the bundle to a local MP4 and return its path."""
    # Host any local VO so Shotstack can fetch it.
    if bundle.audio.vo_path and not bundle.audio.vo_url:
        bundle.audio.vo_url = _ingest_upload(bundle.audio.vo_path)
    # Motion beats with a line (e.g. end-card VO) carry per-beat audio -> host it.
    # (Lip-sync beats already have their voice baked into the generated clip.)
    for clip in bundle.clips:
        if (not clip.lipsync) and clip.audio_path and not clip.audio_url:
            clip.audio_url = _ingest_upload(clip.audio_path)

    edit = _build_edit(bundle)
    r = requests.post(_base("edit") + "/render", json=edit, headers=_headers(), timeout=60)
    r.raise_for_status()
    render_id = r.json()["response"]["id"]
    print(f"[shotstack] render queued: {render_id}")

    url = _poll_render(render_id)
    return _download(url, out_path)


def _build_edit(bundle) -> dict:
    bundle.compute_timing()

    # Video track: clips end to end.
    video_clips = []
    for ct in bundle.timing.clips:
        clip = next(c for c in bundle.clips if c.index == ct.index)
        video_clips.append({
            "asset": {"type": "video", "src": clip.video_url},
            "start": round(ct.start, 3),
            "length": round(ct.end - ct.start, 3),
            "fit": "cover",
        })

    tracks = []

    # Captions track (on top). Chunk word captions into short readable lines.
    caption_clips = []
    for line in _chunk_captions(bundle.captions):
        caption_clips.append({
            "asset": {
                "type": "title",
                "text": line["text"],
                "style": "subtitle",
                "size": "medium",
                "position": "bottom",
            },
            "start": round(line["start"], 3),
            "length": round(line["end"] - line["start"], 3),
        })
    if caption_clips:
        tracks.append({"clips": caption_clips})

    # Optional logo overlay.
    logo_url = bundle.overlay_metadata.brand_logo_url or config.BRAND_LOGO_URL
    if logo_url:
        tracks.append({"clips": [{
            "asset": {"type": "image", "src": logo_url},
            "start": 0,
            "length": bundle.timing.total_duration,
            "position": "topRight",
            "scale": 0.15,
        }]})

    # Optional continuous VO narration track (only if a continuous track was made).
    if bundle.audio.vo_url:
        tracks.append({"clips": [{
            "asset": {"type": "audio", "src": bundle.audio.vo_url},
            "start": 0,
            "length": bundle.timing.total_duration,
        }]})

    # Per-beat VO for MOTION beats (lip-sync beats carry audio inside their clip).
    beat_vo = []
    for ct in bundle.timing.clips:
        clip = next(c for c in bundle.clips if c.index == ct.index)
        if (not clip.lipsync) and clip.audio_url:
            beat_vo.append({
                "asset": {"type": "audio", "src": clip.audio_url},
                "start": round(ct.start, 3),
                "length": round(ct.end - ct.start, 3),
            })
    if beat_vo:
        tracks.append({"clips": beat_vo})

    # Video track last (renders at the bottom).
    tracks.append({"clips": video_clips})

    timeline = {"background": "#000000", "tracks": tracks}
    music_url = bundle.audio.music_url or config.MUSIC_URL
    if music_url:
        timeline["soundtrack"] = {"src": music_url, "effect": "fadeOut"}

    return {
        "timeline": timeline,
        "output": {
            "format": "mp4",
            "size": {"width": config.WIDTH, "height": config.HEIGHT},
            "fps": 30,
        },
    }


def _chunk_captions(captions, max_words: int = 4, max_dur: float = 2.0) -> list[dict]:
    """Group word captions into short lines for readability."""
    lines, cur = [], []
    for cap in captions:
        cur.append(cap)
        span = cur[-1].end - cur[0].start
        if len(cur) >= max_words or span >= max_dur:
            lines.append({"text": " ".join(c.text for c in cur),
                          "start": cur[0].start, "end": cur[-1].end})
            cur = []
    if cur:
        lines.append({"text": " ".join(c.text for c in cur),
                      "start": cur[0].start, "end": cur[-1].end})
    return lines


def _ingest_upload(local_path: str) -> str:
    """Upload a local file via Shotstack Ingest API; return a fetchable source URL."""
    # 1) request a signed upload URL
    r = requests.post(_base("ingest") + "/upload", headers=_headers(), timeout=60)
    r.raise_for_status()
    data = r.json()["data"]
    put_url = data["attributes"]["url"]
    source_id = data["attributes"]["id"]

    # 2) PUT the bytes
    with open(local_path, "rb") as f:
        put = requests.put(put_url, data=f, timeout=300)
        put.raise_for_status()

    # 3) poll the source until it's ready, then return its URL
    for _ in range(60):
        s = requests.get(_base("ingest") + f"/sources/{source_id}", headers=_headers(), timeout=60)
        s.raise_for_status()
        attrs = s.json()["data"]["attributes"]
        if attrs.get("status") == "ready":
            return attrs["source"]
        if attrs.get("status") == "failed":
            raise RuntimeError(f"Shotstack ingest failed for {local_path}")
        time.sleep(2)
    raise TimeoutError(f"Shotstack ingest timed out for {local_path}")


def _poll_render(render_id: str) -> str:
    deadline = time.time() + _TIMEOUT
    while time.time() < deadline:
        r = requests.get(_base("edit") + f"/render/{render_id}", headers=_headers(), timeout=60)
        r.raise_for_status()
        resp = r.json()["response"]
        status = resp["status"]
        print(f"[shotstack] {status}")
        if status == "done":
            return resp["url"]
        if status == "failed":
            raise RuntimeError(f"Shotstack render failed: {resp.get('error')}")
        time.sleep(5)
    raise TimeoutError("Shotstack render timed out")


def _download(url: str, dest: str) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return dest
