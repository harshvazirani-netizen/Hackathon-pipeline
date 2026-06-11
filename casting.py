"""
Voice casting: each screenplay speaker -> the best-fitting ElevenLabs voice.

Pool-agnostic by design: we cast from WHATEVER voices the connected ElevenLabs
account can actually use (free tier = ~22 premade voices + your own; a paid plan
grows the pool to the shared library automatically — no code change).

Order of precedence per speaker:
  1. manual override in job.json: {"voices": {"EXPERT": "<voice_id>"}}
  2. cached casting from a previous run: <job>/voices.json
  3. Claude casting: reads each speaker's lines + the screenplay, picks from the pool
  4. fallback: config.ELEVENLABS_VOICE_ID

Voice Design (creating NEW voices from a description) is a paid ElevenLabs
feature (403 on free) — when the plan supports it, that becomes another branch
here; the rest of the pipeline won't change.
"""
from __future__ import annotations

import json
import os

import requests

import config


def cast(job_dir: str, clips, screenplay: str = "") -> dict:
    """Return {speaker: voice_id} for every speaker that has lines."""
    speakers = sorted({(c.speaker or "VO") for c in clips if c.vo_line})
    if not speakers:
        return {}

    # 1. manual override
    manual = _manual_voices(job_dir)
    todo = [s for s in speakers if s not in manual]
    voice_map = dict(manual)

    # 2. cache
    cache_path = os.path.join(job_dir, "voices.json")
    if todo and os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)
        for s in list(todo):
            if s in cached:
                voice_map[s] = cached[s]
                todo.remove(s)

    # 3. Claude casting from the usable pool (+ native-language library voices
    #    when the lines are Hindi/Hinglish — needs a paid ElevenLabs plan)
    if todo:
        lang = _detect_language(clips)
        pool = _usable_voices()
        if lang == "hi":
            pool = _library_voices("hi") + pool
        if pool and os.getenv("ANTHROPIC_API_KEY"):
            picked = _claude_cast(todo, clips, screenplay, pool, lang)
            picked = {s: _materialise(v, pool) for s, v in picked.items()}
            voice_map.update(picked)
            todo = [s for s in todo if s not in picked]
        # 4. fallback
        for s in todo:
            voice_map[s] = config.ELEVENLABS_VOICE_ID

        with open(cache_path, "w") as f:
            json.dump(voice_map, f, indent=2)

    for s in speakers:
        print(f"[cast] {s} -> {voice_map[s]}")
    return voice_map


_HINDI_HINTS = (" hai", " nahi", " kyun", " tum", " kitne", " ho.", " ho ", " main ",
                " mein", " aur ", " kya ", " yeh", " woh", " pyaar", " swaad")


def _detect_language(clips) -> str:
    """'hi' for Hindi/Hinglish lines (Devanagari or romanized), else 'en'."""
    import re
    text = " ".join(c.vo_line for c in clips if c.vo_line)
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    low = f" {text.lower()} "
    if sum(h in low for h in _HINDI_HINTS) >= 2:
        return "hi"
    return "en"


def _library_voices(language: str) -> list[dict]:
    """Native-language voices from the shared library (usable on paid plans).
    Marked with public_owner_id so a pick can be added to My Voices first."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return []
    r = requests.get(
        f"https://api.elevenlabs.io/v1/shared-voices?page_size=20&language={language}",
        headers={"xi-api-key": key}, timeout=30)
    if r.status_code != 200:
        print(f"[cast] library lookup failed ({r.status_code}); premades only")
        return []
    out = []
    for v in r.json().get("voices", []):
        if v.get("language") != language:  # API filter is loose; enforce strictly
            continue
        out.append({
            "voice_id": v["voice_id"],
            "name": v.get("name", ""),
            "gender": v.get("gender", ""),
            "age": v.get("age", ""),
            "accent": v.get("accent", ""),
            "style": (v.get("description") or "")[:80],
            "use_case": v.get("use_case", ""),
            "public_owner_id": v.get("public_owner_id"),
        })
    return out


def _materialise(voice_id: str, pool: list[dict]) -> str:
    """If the pick is a shared-library voice, add it to My Voices first (required
    before TTS). Returns the usable voice_id (falls back to default on failure)."""
    entry = next((v for v in pool if v["voice_id"] == voice_id), None)
    if not entry or not entry.get("public_owner_id"):
        return voice_id  # premade/own voice — usable as-is
    key = os.getenv("ELEVENLABS_API_KEY")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/voices/add/{entry['public_owner_id']}/{voice_id}",
        headers={"xi-api-key": key}, json={"new_name": entry["name"]}, timeout=30)
    if r.status_code == 200:
        new_id = r.json().get("voice_id", voice_id)
        print(f"[cast] added library voice '{entry['name']}' -> {new_id}")
        return new_id
    print(f"[cast] ⚠ couldn't add library voice ({r.status_code}: {r.text[:80]}); using default")
    return config.ELEVENLABS_VOICE_ID


def _manual_voices(job_dir: str) -> dict:
    p = os.path.join(job_dir, "job.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f).get("voices", {}) or {}
    return {}


def _usable_voices() -> list[dict]:
    """Voices this account can actually TTS with (premade + own)."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return []
    r = requests.get("https://api.elevenlabs.io/v1/voices",
                     headers={"xi-api-key": key}, timeout=30)
    if r.status_code != 200:
        print(f"[cast] voice list failed ({r.status_code}); using default voice")
        return []
    out = []
    for v in r.json().get("voices", []):
        labels = v.get("labels") or {}
        out.append({
            "voice_id": v["voice_id"],
            "name": v.get("name", ""),
            "gender": labels.get("gender", ""),
            "age": labels.get("age", ""),
            "accent": labels.get("accent", ""),
            "style": labels.get("descriptive", "") or labels.get("description", ""),
            "use_case": labels.get("use_case", ""),
        })
    return out


def _claude_cast(speakers: list[str], clips, screenplay: str, pool: list[dict],
                 lang: str = "en") -> dict:
    from anthropic import Anthropic

    lines = {s: [c.vo_line for c in clips if (c.speaker or "VO") == s and c.vo_line][:3]
             for s in speakers}
    roster = "\n".join(
        f"- {v['voice_id']} | {v['name']} | {v['gender']} {v['age']} {v['accent']} | {v['style']} | {v['use_case']}"
        for v in pool)

    tool = {
        "name": "submit_casting",
        "description": "Assign one voice_id from the roster to each speaker.",
        "input_schema": {
            "type": "object",
            "properties": {"assignments": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": speakers},
                    "voice_id": {"type": "string",
                                 "enum": [v["voice_id"] for v in pool]},
                    "why": {"type": "string"},
                },
                "required": ["speaker", "voice_id", "why"],
            }}},
            "required": ["assignments"],
        },
    }
    client = Anthropic()
    resp = client.messages.create(
        model=config.DIRECTOR_MODEL,
        max_tokens=800,
        tools=[tool],
        tool_choice={"type": "tool", "name": "submit_casting"},
        messages=[{"role": "user", "content": (
            "Cast a voice for each speaker in this vertical video ad. Match gender, age, "
            "tone and role implied by the screenplay and their lines. Use DIFFERENT voices "
            "for different speakers.\n"
            + ("IMPORTANT: the lines are in HINDI/HINGLISH — strongly prefer native "
               "Hindi/Indian-accent voices from the roster over US/UK ones.\n\n"
               if lang == "hi" else "\n")
            + f"Speakers and sample lines:\n{json.dumps(lines, ensure_ascii=False, indent=1)}\n\n"
            f"Screenplay excerpt:\n{screenplay[:800]}\n\nVoice roster:\n{roster}"
        )}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_casting":
            picks = {a["speaker"]: a["voice_id"] for a in block.input.get("assignments", [])}
            for a in block.input.get("assignments", []):
                print(f"[cast] {a['speaker']} -> {a['voice_id']}: {a['why']}")
            return picks
    return {}
