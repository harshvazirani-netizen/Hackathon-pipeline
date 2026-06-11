# Vertical Ad Pipeline

Input: a **screenplay (with timing) + a storyboard (one approved frame per beat)**.
Output: a finished **9:16 vertical video ad**. The storyboard frames ARE the look —
we animate from them, we don't invent visuals.

## Ad-types — read FROM the storyboard
The type is **auto-detected from the first storyboard frame** (Claude vision). An
explicit `job.json` `ad_type` is only an optional override.

| `ad_type` | What it is | Lip-sync? | Flow | Model (fal) |
|---|---|---|---|---|
| `ai_human` | real person to camera | ✅ | audio-first | OmniHuman v1.5 |
| `fruit_object` | talking object/fruit | ✅ | audio-first | Kling AI-Avatar v2 |
| `pixar_animation` | 3D animated scene | ❌ | visual-first | Kling 2.6 Pro I2V |
| **anything else** | any other storyboard | auto | by lip-sync | **generic recipe** (Kling AI-Avatar if it talks, else Kling I2V) |

The 3 are optimized presets; a storyboard outside them is **never rejected** — it
gets a generic recipe routed by the one thing that matters (does a character speak
on camera?). Everything routes through **fal.ai** (one key, one bill, swap a model by editing one
string in `ad_types.py`). Voice = **ElevenLabs**, brains/QA = **Claude**, stitching =
**Shotstack**, cheap QA = **ffmpeg + Whisper** (local).

## The flow (routing is PER BEAT)
A single ad mixes talking and silent beats (a microdrama has dialogue beats *and*
action/SFX/end-card beats), so the recipe carries **two** models and each beat is
routed on its own `lipsync` flag:
```
job folder ──► ingest ──► recipe + per-beat clips ──► voiceover (per beat) ──► generate ──► assemble ──► QA ──► ship
               (parse + pair                          lip-sync beats get
                each beat to its frame)               audio that drives the mouth

  per beat:  speaks on camera?  ── yes ─► recipe.lipsync_model  (image + audio → talking, voice baked in)
                                └─ no ──► recipe.motion_model   (image → motion; any VO laid over in assembly)
```
QA pass → `output/shipped/` · fail → retry generation ×2 · still failing → `logs/dead_letter/`.

## Input contract (a "job folder")
```
my_job/
├── job.json                 # OPTIONAL — type is read from the storyboard; this only overrides it
├── screenplay.txt           # .txt / .fountain / .md  (with timing)
├── beats.json               # OPTIONAL — a pre-parsed manifest (e.g. from html_adapter) skips Claude
└── storyboard/
    ├── beat_01.png          # approved frame for beat 1
    ├── beat_02.png          # …sorted filename order == beat order
    └── ...
```
See [examples/sample_job](examples/sample_job), or [examples/the_affair](examples/the_affair)
(built from an HTML storyboard via `python html_adapter.py <file.html>`).

## Setup
```bash
cd ~/ad-pipeline && source .venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg                 # QA Layer 1 + frame sampling
pip install faster-whisper          # QA Layer 2
cp .env.example .env                # FAL + ANTHROPIC + ELEVENLABS + SHOTSTACK keys
```

## Run
```bash
python pipeline.py --job examples/sample_job
```

## Files
- `ad_types.py` — **the dispatcher + 3 recipes** (models, lip-sync flag, QA rubric). Add a type here.
- `schema.py` — the AssetBundle contract (storyboard frame + per-beat audio + outputs)
- `ingest.py` — job folder → beats paired with storyboard frames (Claude parses, doesn't invent)
- `video_gen.py` — fal primitives: `upload_file`, `lipsync_from_image`, `image_to_video`
- `generate.py` — beats → clips, routed by recipe (lip-sync vs motion)
- `voiceover.py` — `synthesize_per_beat` (lip-sync) / `synthesize` (continuous narration)
- `assembly.py` — bundle → Shotstack render → MP4
- `qa/` — `layer1_technical` (ffprobe), `layer2_transcript` (whisper), `layer3_vision` (Claude, per-type rubric), `gate`
- `pipeline.py` — end-to-end glue, per-type ordering, retry + dead-letter

## Known first-run fix points (untested against live APIs)
1. **Screenplay parsing** — `ingest._parse_beats` assumes beat order == storyboard order; calibrate to your real screenplay format (PDF/.fdx extractors are a TODO).
2. **fal model arg names** — `video_gen.lipsync_from_image` uses `image_url`+`audio_url`; confirm per model on fal.ai/models.
3. **ElevenLabs timestamp fields** — `voiceover._get` handles the common variants.
4. **Shotstack ingest** — `assembly._ingest_upload` upload/source step.
5. **fruit_object lip-sync** — A/B OmniHuman vs Kling-Avatar on a real frame; it's the least-certain model choice.

## QA calibration
`config.QA_CALIBRATION = True` (week 1): all 3 layers run, scores log to
`logs/qa_scores.jsonl`, nothing is rejected. Set thresholds from the logs, then flip to `False`.
