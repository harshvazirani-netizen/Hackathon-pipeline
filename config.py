"""
Central config for the vertical-ad pipeline (V1).

V1 scope: ONE hardcoded ad-type ("cartoon_edit"), no dispatcher, no queue.
When we add ad-type #2, this is where the dispatcher will route on AD_TYPE.

Model/service IDs are the ONLY things you change to swap a vendor — that's the
point of routing generation through fal and keeping assembly behind one module.

  VERIFY model IDs against live catalogs (fal.ai/models, ElevenLabs voices,
  Shotstack docs). Vendors rev versions; the code fails loudly with a hint when
  an ID is stale.
"""
import os

# --- Ad-type (hardcoded for V1) ------------------------------------------------
AD_TYPE = "cartoon_edit"

# --- Vertical video target -----------------------------------------------------
ASPECT_RATIO = "9:16"
WIDTH, HEIGHT = 1080, 1920
RESOLUTION = f"{WIDTH}x{HEIGHT}"
DEFAULT_CLIP_SECONDS = 5
MIN_BEATS, MAX_BEATS = 3, 6          # clips per ad
TARGET_AD_SECONDS = 25               # rough total length target

# --- fal.ai generation models --------------------------------------------------
# Cartoon pattern = keyframe image -> image-to-video. Two stages.
#
# Format facts (drive the choices below):
#   - Style: 3D / Pixar-ish  -> keyframe model quality matters a lot here.
#   - Characters: fresh per script -> NO recurring-mascot DB / cross-ad locking.
#     Consistency only needs to hold WITHIN one ad's clips; generate.py gets that
#     by making the hero keyframe once (beat 0) and passing it as a reference to
#     later beats.

# Stage A: keyframe / character image. Sets the 3D look + the per-ad character.
KEYFRAME_MODEL = "fal-ai/bytedance/seedream/v4/text-to-image"   # $0.03/img, confirmed shape
# A/B alternatives (swap the string above) — for 3D/Pixar, try Nano Banana FIRST:
#   "fal-ai/nano-banana"        -> Nano Banana, cleanest Pixar-style 3D renders, ~$0.04/img
#   "fal-ai/flux-pro/kontext"   -> FLUX Kontext Pro, strong if we ever need ref locking, $0.04/img

# Stage B: image-to-video animator. Animates the keyframe.
ANIMATOR_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"   # $0.07/s, best motion, most mature
# A/B alternatives:
#   "fal-ai/pixverse/v4.5/image-to-video"  -> best pure-cartoon/anime look, fast
#   "fal-ai/vidu/q1/reference-to-video"    -> cheapest, multi-ref consistency

# Direct text-to-video (no keyframe) — only used by the --t2v smoke-test fallback.
T2V_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"

# --- Claude models (Anthropic API) ---------------------------------------------
DIRECTOR_MODEL = "claude-opus-4-8"   # script -> beats (downgrade to a sonnet id for cost)
VISION_MODEL = "claude-opus-4-8"     # QA layer 3: keyframe scoring vs rubric

# --- ElevenLabs (TTS) ----------------------------------------------------------
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"   # default "Rachel"; pick yours in the dashboard
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# --- Assembly (Shotstack default; Creatomate swappable behind assembly.py) -----
ASSEMBLY_BACKEND = "shotstack"
SHOTSTACK_ENV = os.getenv("SHOTSTACK_ENV", "stage")  # 'stage' = free sandbox, 'v1' = production
MUSIC_URL = None        # optional background music (public URL); None = no music
BRAND_LOGO_URL = None   # optional logo overlay (public URL); None = no branding

# --- Auto-QA: thresholds + calibration -----------------------------------------
# WEEK 1: leave QA_CALIBRATION=True -> run ALL layers, LOG every score, never
# reject. After a week of logs, set thresholds from the data and flip to False.
QA_CALIBRATION = True
QA_DURATION_TOLERANCE_S = 1.5        # rendered vs intended duration
QA_TRANSCRIPT_MIN_SIMILARITY = 0.75  # Whisper transcript vs intended script (0..1)
QA_VISION_MIN_SCORE = 7              # Claude rubric score, out of 10
QA_AUDIO_MAX_DBFS = -1.0             # clipping if measured max volume above this
QA_VISION_SAMPLE_FRAMES = 5          # keyframes sampled for layer 3
MAX_GENERATION_RETRIES = 2           # retry whole generation 2x (3 attempts total)

# --- Rough cost model (for logging; update from invoices) ----------------------
COST_PER_IMAGE_USD = 0.03
COST_PER_VIDEO_SECOND_USD = 0.07

# --- Paths (created on import for convenience) ---------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")
WORK_DIR = os.path.join(OUTPUT_DIR, "work")        # intermediate clips/audio per ad
SHIPPED_DIR = os.path.join(OUTPUT_DIR, "shipped")  # passed QA
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")
DEAD_LETTER_DIR = os.path.join(LOGS_DIR, "dead_letter")
QA_LOG = os.path.join(LOGS_DIR, "qa_scores.jsonl")

for _d in (OUTPUT_DIR, WORK_DIR, SHIPPED_DIR, LOGS_DIR, DEAD_LETTER_DIR):
    os.makedirs(_d, exist_ok=True)
