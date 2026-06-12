"""
Central config for the vertical-ad pipeline.

Per-ad-type choices (generation model, lip-sync flow, QA rubric) live in
ad_types.py recipes — NOT here. This file holds only the cross-type, cross-vendor
settings: video target, Claude/ElevenLabs/Shotstack ids, QA thresholds, paths.

  VERIFY service ids against live catalogs (fal.ai/models, ElevenLabs voices,
  Shotstack docs). The code fails loudly with a hint when an id is stale.
"""
import os

# --- Vertical video target -----------------------------------------------------
ASPECT_RATIO = "9:16"
WIDTH, HEIGHT = 1080, 1920
RESOLUTION = f"{WIDTH}x{HEIGHT}"
DEFAULT_CLIP_SECONDS = 5              # fallback only; real durations come from the screenplay

# --- Claude models (Anthropic API) ---------------------------------------------
DIRECTOR_MODEL = "claude-opus-4-8"   # ingest: parse screenplay -> beats
VISION_MODEL = "claude-opus-4-8"     # QA layer 3: score frames vs the recipe's rubric

# --- ElevenLabs (TTS) ----------------------------------------------------------
ELEVENLABS_VOICE_ID = "HI0kneBmwaZBJsViQ5rD"   # user's own voice (free-tier usable)
ELEVENLABS_MODEL = "eleven_multilingual_v2"
MAX_VO_SPEED = 1.5   # max time-compression to fit a line in its slot (ffmpeg atempo, pitch-preserved)
PREFER_INDIAN_VOICES = True   # brand default: cast native Indian/Hindi-accent voices for ALL characters
VOICE_LANGUAGE = "hi"         # spoken language for VO; "hi" = translate every line to Hindi, "en" = as written

# --- Assembly (Shotstack; Creatomate swappable behind assembly.py) -------------
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
QA_VISION_SAMPLE_FRAMES = 5          # frames sampled for layer 3
MAX_GENERATION_RETRIES = 2           # retry whole generation 2x (3 attempts total)

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

# Make the bundled static ffmpeg/ffprobe (bin/) discoverable to subprocess +
# shutil.which everywhere — no Homebrew needed. Unblocks VO time-stretch + QA.
_BIN = os.path.join(PROJECT_DIR, "bin")
if os.path.isdir(_BIN) and _BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _BIN + os.pathsep + os.environ.get("PATH", "")

# Load .env from the project dir (cwd-independent) so EVERY entrypoint that
# imports config has the API keys available, regardless of import order.
from dotenv import load_dotenv as _load_dotenv  # noqa: E402
_load_dotenv(os.path.join(PROJECT_DIR, ".env"))
