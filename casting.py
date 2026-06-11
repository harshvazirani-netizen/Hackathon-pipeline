"""
Character voice casting (a single pass, from scene 1).

For each distinct character (speaker) in the screenplay we build a profile by
looking at BOTH:
  - the screenplay text + that character's lines  -> role, language, accent
  - the storyboard frame they appear in (vision)  -> apparent age, gender

…then lock ONE matching voice per character and reuse it for every scene that
character appears in. Voices never change for a character; recurring characters
are not re-cast (cached in <job>/voices.json).

Pool-agnostic: cast from whatever voices the ElevenLabs account can use (premade
+ own; paid plans add the shared library, incl. native Hindi voices). A picked
library voice is added to My Voices before use.

Precedence per character: job.json "voices" override > voices.json cache >
vision casting > default voice.
"""
from __future__ import annotations

import base64
import json
import os
import re

import requests

import config


def cast(job_dir: str, clips, screenplay: str = "") -> dict:
    """Lock one voice per character. Returns {speaker: voice_id}; persists rich
    profiles (age/gender/accent/language/why) to <job>/voices.json."""
    speakers = sorted({(c.speaker or "VO") for c in clips if c.vo_line})
    if not speakers:
        return {}

    cache_path = os.path.join(job_dir, "voices.json")
    cached = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached = json.load(f)

    manual = _manual_voices(job_dir)
    record: dict = {}                                  # speaker -> rich entry
    for s in speakers:
        if s in manual:
            record[s] = {"voice_id": manual[s], "source": "manual"}
        elif s in cached:
            record[s] = cached[s] if isinstance(cached[s], dict) else {"voice_id": cached[s]}

    todo = [s for s in speakers if s not in record]
    if todo:
        lang_by = {s: _detect_language(_lines_of(s, clips)) for s in todo}
        prepend = []
        # Indian context (₹ / India / Hindi): give Claude native Indian voices with
        # BOTH genders. Hindi-library voices (Bunty/Aakash male, Naina female, ...)
        # speak English fine via the multilingual model, so they fit Indian-English
        # characters too — alongside any English voices tagged with an Indian accent.
        if _indian_context(screenplay, clips):
            prepend = _library_voices("hi") + _library_voices("en", accent="indian")
        elif any(v == "hi" for v in lang_by.values()):
            prepend = _library_voices("hi")
        pool = _dedupe(prepend + _usable_voices())
        frames_by = {s: _speaker_frame(s, clips) for s in todo}

        if pool and os.getenv("ANTHROPIC_API_KEY"):
            for s, a in _claude_cast(todo, clips, screenplay, pool, lang_by, frames_by).items():
                a["voice_id"] = _materialise(a["voice_id"], pool)
                record[s] = a
        for s in todo:                                  # fallback for anything unassigned
            record.setdefault(s, {"voice_id": config.ELEVENLABS_VOICE_ID, "source": "fallback"})

        with open(cache_path, "w") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    for s in speakers:
        e = record[s]
        prof = " ".join(filter(None, [e.get("gender"), e.get("age"),
                                      e.get("accent"), f"[{e.get('language')}]" if e.get("language") else ""]))
        print(f"[cast] {s} -> {e.get('name') or e['voice_id']}  {prof}".rstrip())
    return {s: record[s]["voice_id"] for s in speakers}


# ---- character signals --------------------------------------------------------

def _lines_of(speaker: str, clips) -> str:
    return " ".join(c.vo_line for c in clips if (c.speaker or "VO") == speaker and c.vo_line)


def _speaker_frame(speaker: str, clips) -> str | None:
    """Storyboard frame to SEE the character — first scene they appear in."""
    for c in clips:
        if (c.speaker or "VO") == speaker and c.storyboard_image_path \
                and os.path.exists(c.storyboard_image_path):
            return c.storyboard_image_path
    return None


_HINDI_HINTS = (" hai", " nahi", " kyun", " tum", " kitne", " ho.", " ho ", " main ",
                " mein", " aur ", " kya ", " yeh", " woh", " pyaar", " swaad", " hum ")


def _detect_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    low = f" {text.lower()} "
    return "hi" if sum(h in low for h in _HINDI_HINTS) >= 2 else "en"


def _b64_image(path: str) -> tuple[str, str]:
    mt = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode(), mt


# ---- vision casting -----------------------------------------------------------

def _claude_cast(speakers, clips, screenplay, pool, lang_by, frames_by) -> dict:
    from anthropic import Anthropic

    roster = "\n".join(
        f"- {v['voice_id']} | {v['name']} | {v['gender']} {v['age']} {v['accent']} | {v['style']} | {v['use_case']}"
        for v in pool)

    tool = {
        "name": "submit_casting",
        "description": "Assign one distinct voice per character with the profile you inferred.",
        "input_schema": {"type": "object", "properties": {"assignments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "speaker": {"type": "string", "enum": speakers},
                "age": {"type": "string", "description": "apparent age, e.g. 'teen', '30s', 'middle-aged'"},
                "gender": {"type": "string"},
                "accent": {"type": "string", "description": "e.g. 'Indian', 'neutral', 'US'"},
                "language": {"type": "string", "enum": ["hi", "en"]},
                "voice_id": {"type": "string", "enum": [v["voice_id"] for v in pool]},
                "why": {"type": "string"},
            },
            "required": ["speaker", "age", "gender", "accent", "language", "voice_id", "why"],
        }}}, "required": ["assignments"]},
    }

    content = [{"type": "text", "text": (
        "Cast a voice for each CHARACTER in this vertical video ad. For each character: "
        "look at their storyboard frame to judge apparent AGE and GENDER, and use their "
        "lines + the screenplay to judge ACCENT and LANGUAGE. Then pick the single "
        "best-matching voice from the roster. Use a DIFFERENT voice per character. "
        "Characters speaking Hindi/Hinglish MUST get a native Hindi / Indian-accent voice.\n\n"
        f"Screenplay excerpt:\n{screenplay[:800]}")}]
    for s in speakers:
        content.append({"type": "text", "text":
                        f"\n=== CHARACTER: {s} (detected language: {lang_by.get(s)}) ===\n"
                        f"Lines: {_lines_of(s, clips)[:300]}\nFrame they appear in:"})
        fr = frames_by.get(s)
        if fr:
            b64, mt = _b64_image(fr)
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
        else:
            content.append({"type": "text", "text": "(off-camera narration — no frame; judge from text)"})
    content.append({"type": "text", "text": f"\nVoice roster:\n{roster}"})

    resp = Anthropic().messages.create(
        model=config.VISION_MODEL, max_tokens=1200,
        tools=[tool], tool_choice={"type": "tool", "name": "submit_casting"},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_casting":
            out = {}
            for a in block.input.get("assignments", []):
                name = next((v["name"] for v in pool if v["voice_id"] == a["voice_id"]), "")
                out[a["speaker"]] = {**a, "name": name, "source": "vision"}
                print(f"[cast] {a['speaker']}: {a['gender']} {a['age']} {a['accent']} [{a['language']}] "
                      f"-> {name}: {a['why']}")
            return out
    return {}


# ---- pool helpers (unchanged) -------------------------------------------------

def _materialise(voice_id: str, pool: list[dict]) -> str:
    entry = next((v for v in pool if v["voice_id"] == voice_id), None)
    if not entry or not entry.get("public_owner_id"):
        return voice_id
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


def _dedupe(voices: list[dict]) -> list[dict]:
    seen, out = set(), []
    for v in voices:
        if v["voice_id"] not in seen:
            seen.add(v["voice_id"])
            out.append(v)
    return out


def _indian_context(screenplay: str, clips) -> bool:
    text = (screenplay or "") + " " + " ".join(c.vo_line for c in clips if c.vo_line)
    low = text.lower()
    return ("₹" in text or "india" in low or "indian" in low
            or _detect_language(text) == "hi")


def _library_voices(language: str, accent: str | None = None) -> list[dict]:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return []
    r = requests.get(
        f"https://api.elevenlabs.io/v1/shared-voices?page_size=30&language={language}",
        headers={"xi-api-key": key}, timeout=30)
    if r.status_code != 200:
        print(f"[cast] library lookup failed ({r.status_code}); premades only")
        return []
    out = []
    for v in r.json().get("voices", []):
        if v.get("language") != language:
            continue
        if accent and accent.lower() not in (v.get("accent") or "").lower():
            continue
        out.append({
            "voice_id": v["voice_id"], "name": v.get("name", ""),
            "gender": v.get("gender", ""), "age": v.get("age", ""),
            "accent": v.get("accent", ""), "style": (v.get("description") or "")[:80],
            "use_case": v.get("use_case", ""), "public_owner_id": v.get("public_owner_id"),
        })
    return out


def _usable_voices() -> list[dict]:
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
            "voice_id": v["voice_id"], "name": v.get("name", ""),
            "gender": labels.get("gender", ""), "age": labels.get("age", ""),
            "accent": labels.get("accent", ""),
            "style": labels.get("descriptive", "") or labels.get("description", ""),
            "use_case": labels.get("use_case", ""),
        })
    return out
