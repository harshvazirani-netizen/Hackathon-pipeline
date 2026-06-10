# Vertical Ad Pipeline

Automated pipeline: refined ad script + ad-type → finished vertical (9:16) video ad.

**V1 scope:** one hardcoded ad-type (`cartoon_edit`, 3D/Pixar style, fresh characters
per script), no dispatcher, no queue. ~10 ads/day. Add a dispatcher at ad-type #2;
add a queue past ~50–100/day.

## Flow (all six components built)
```
script ──► script_director ──► generate ──► voiceover ──► assembly ──► QA gate ──► ship
           (Claude:beats)      (fal:clips)  (11Labs:VO)   (Shotstack)  (1▸2▸3)      │
                                                                                     ├─ pass  → output/shipped/
                                                                                     ├─ fail  → retry gen ×2
                                                                                     └─ still → logs/dead_letter/
```
Everything reads/writes one **AssetBundle** (`schema.py`) — the contract:
`{ clips, audio, captions, timing, overlay_metadata, qa }`.

## Why this generation design
"Cartoon edit" lives or dies on **art-style + character consistency across clips**,
so generation is **keyframe image → image-to-video**: lock the character/style as an
image, then animate it. Format here is 3D/Pixar with **fresh characters per script**,
so consistency only needs to hold *within* one ad — `generate.py` makes the hero
keyframe once (beat 0) and reuses it as a reference for later beats.

## Vendor choices (all swappable)
| Stage | Default | Cost | Swap |
|---|---|---|---|
| Direction | Claude (tool-use) | — | model id in `config.DIRECTOR_MODEL` |
| Keyframe image | Seedream V4 (fal) | $0.03/img | Nano Banana, FLUX Kontext |
| Image→video | Kling 2.5 Turbo Pro (fal) | $0.07/s | PixVerse, Vidu |
| Voiceover | ElevenLabs | cheap | voice id in `config` |
| Assembly | Shotstack | render-based | Creatomate (behind `assembly.py`) |
| QA transcription | faster-whisper (local) | free | hosted Whisper API |
| QA vision | Claude vision | per-call | — |

≈ **under $2 per finished ad** at these defaults.

## Setup
```bash
cd ~/ad-pipeline && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg                 # QA Layer 1 + frame sampling
pip install faster-whisper          # QA Layer 2
cp .env.example .env                # fill in all 4 keys
```

## Run end-to-end
```bash
python pipeline.py --script examples/sample_script.txt
# or
python pipeline.py --text "Tired of tangled cables? ..."
```

## Test a single component in isolation (recommended first runs)
```bash
python smoke_test.py --prompt "a 3D Pixar-style avocado waving hello"   # just fal generation
```

## Auto-QA calibration (important)
`config.QA_CALIBRATION = True` for week 1: QA runs all 3 layers and **logs every
score to `logs/qa_scores.jsonl` but never rejects**. After a week, read the logs,
set real thresholds in `config.py`, and flip `QA_CALIBRATION = False` to enforce.

## Files
- `config.py` — ad-type, model/vendor ids, QA thresholds, paths (the control panel)
- `schema.py` — the AssetBundle contract (pydantic)
- `script_director.py` — script → storyboard beats (Claude tool-use)
- `video_gen.py` / `generate.py` — fal primitives / beats → clips
- `voiceover.py` — ElevenLabs TTS → VO + word captions
- `assembly.py` — bundle → Shotstack render → MP4
- `qa/` — `layer1_technical` (ffprobe), `layer2_transcript` (whisper), `layer3_vision` (Claude), `gate`
- `pipeline.py` — end-to-end glue + retry + dead-letter
- `smoke_test.py` — Step-1 single-clip tester

## Known first-run fix points (untested against live APIs yet)
1. **fal video output shape** — `video_gen._first_media_url` probes common shapes; confirm against the saved `*-raw.json`.
2. **ElevenLabs timestamp fields** — `voiceover._get` handles `audio_base64`/`audio_base_64`; confirm against your SDK version.
3. **Shotstack Ingest** — `assembly._ingest_upload` follows the documented flow; the upload/source step is the likeliest tweak.
4. **Model IDs** — verify on each vendor's live catalog; code fails loudly with a hint if stale.
