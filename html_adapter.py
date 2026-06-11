"""
HTML storyboard -> job folder adapter.

Some storyboards arrive as a single self-contained HTML (screenplay text + inline
SVG frames), not our job-folder layout. This parses that HTML deterministically
(no Claude needed — the markup already labels speakers/timecodes) and writes:

  <out>/job.json            {"ad_type": "ai_human"}   (override; ingest can still re-detect)
  <out>/screenplay.txt      the screenplay, tags stripped
  <out>/storyboard/beat_NN.svg   each frame  (+ .png if a rasteriser is available)
  <out>/beats.json          the per-beat manifest (the Clip list ingest would build)

Speaker in the storyboard's audio cue decides per-beat routing:
  named character (MEERA/RAJ/…) speaking  -> lipsync beat
  SFX / MUSIC / off-screen VO / silent     -> motion beat

Usage:  python html_adapter.py "<file.html>" [--out examples/the_affair] [--ad-type ai_human]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess

_NON_SPEAKERS = {"SFX", "MUSIC", "VO", "SUPER"}


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&amp;", "&").replace("&middot;", "·").replace("&#10084;", "♥")
           .replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", s).strip()


def _seconds(tc: str) -> float:
    """'0:07–0:10' -> 3.0 (handles en-dash or hyphen)."""
    a, b = re.split(r"[–-]", tc.strip())[:2]

    def to_s(x):
        m, s = x.strip().split(":")
        return int(m) * 60 + int(s)
    return float(to_s(b) - to_s(a))


def parse(html: str) -> list[dict]:
    board = html.split('class="board"', 1)[1].split("<!-- ===", 1)[0]
    cells = board.split('class="cell"')[1:]
    beats = []
    for i, cell in enumerate(cells):
        tc = re.search(r'class="tc-tag">([^<]+)<', cell)
        svg = re.search(r"(<svg.*?</svg>)", cell, re.S)
        shot = re.search(r'class="shot-type">([^<]+)<', cell)
        act = re.search(r'class="act">(.*?)</p>', cell, re.S)
        aud = re.search(r'class="aud">(.*?)</p>', cell, re.S)
        if not (tc and svg):
            continue

        speaker, line = None, ""
        if aud:
            sp = re.search(r"<b>([^<]+)</b>\s*(.*)", aud.group(1), re.S)
            if sp:
                speaker = sp.group(1).strip().upper()
                q = re.search(r'"([^"]+)"', _strip_tags(sp.group(2)))
                line = q.group(1) if q else ""

        on_camera = bool(speaker) and speaker not in _NON_SPEAKERS and bool(line)
        beats.append({
            "index": i,
            "timecode": tc.group(1).strip(),
            "duration": _seconds(tc.group(1)),
            "shot_type": _strip_tags(shot.group(1)) if shot else "",
            "speaker": speaker,
            "vo_line": line,
            "motion_prompt": _strip_tags(act.group(1)) if act else "",
            "on_camera_speech": on_camera,      # -> lipsync beat
            "_svg": svg.group(1),
        })
    return beats


def _screenplay_text(html: str) -> str:
    block = re.search(r'class="script">(.*?)</div>', html, re.S)
    raw = block.group(1) if block else ""
    # one line per paragraph
    paras = re.findall(r"<p[^>]*>(.*?)</p>", raw, re.S)
    return "\n".join(_strip_tags(p) for p in paras if _strip_tags(p))


def _rasterise(svg_path: str) -> str | None:
    """Best-effort SVG->PNG. Tries rsvg-convert, then macOS qlmanage. Returns png path or None."""
    png = svg_path[:-4] + ".png"
    if shutil.which("rsvg-convert"):
        if subprocess.run(["rsvg-convert", "-w", "1080", svg_path, "-o", png],
                          capture_output=True).returncode == 0:
            return png
    if shutil.which("qlmanage"):  # macOS Quick Look
        d = os.path.dirname(svg_path)
        subprocess.run(["qlmanage", "-t", "-s", "1080", "-o", d, svg_path],
                       capture_output=True)
        ql = svg_path + ".png"
        if os.path.exists(ql):
            os.replace(ql, png)
            return png
    return None


def adapt(html_path: str, out_dir: str, ad_type: str | None) -> dict:
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    beats = parse(html)
    sb = os.path.join(out_dir, "storyboard")
    os.makedirs(sb, exist_ok=True)

    rasterised = 0
    for b in beats:
        svg_path = os.path.join(sb, f"beat_{b['index'] + 1:02d}.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(b.pop("_svg"))
        png = _rasterise(svg_path)
        b["storyboard_image_path"] = png or svg_path
        if png:
            rasterised += 1

    with open(os.path.join(out_dir, "screenplay.txt"), "w", encoding="utf-8") as f:
        f.write(_screenplay_text(html) + "\n")
    if ad_type:
        with open(os.path.join(out_dir, "job.json"), "w") as f:
            json.dump({"ad_type": ad_type}, f, indent=2)
    with open(os.path.join(out_dir, "beats.json"), "w", encoding="utf-8") as f:
        json.dump(beats, f, indent=2, ensure_ascii=False)

    return {"beats": beats, "rasterised": rasterised, "out": out_dir}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out", default="examples/the_affair")
    ap.add_argument("--ad-type", default="ai_human")
    args = ap.parse_args()

    from ad_types import get_recipe
    r = get_recipe(args.ad_type)
    res = adapt(args.html, args.out, args.ad_type)

    total = sum(b["duration"] for b in res["beats"])
    print(f"\nAdapted -> {res['out']}  ({len(res['beats'])} beats, {total:.0f}s, "
          f"{res['rasterised']}/{len(res['beats'])} frames rasterised to PNG)\n")
    print(f"{'#':>2} {'time':<11}{'route':<8}{'model':<42}{'line'}")
    for b in res["beats"]:
        route = "lipsync" if b["on_camera_speech"] else "motion"
        model = (r.lipsync_model if b["on_camera_speech"] else r.motion_model).split("/")[-1]
        line = (b["vo_line"][:34] + "…") if len(b["vo_line"]) > 35 else b["vo_line"]
        print(f"{b['index']+1:>2} {b['timecode']:<11}{route:<8}{model:<42}{line or '—'}")


if __name__ == "__main__":
    main()
