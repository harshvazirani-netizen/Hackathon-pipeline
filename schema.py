"""
THE CONTRACT (Step 2): the normalized asset bundle every component reads/writes.

  generation  -> fills clips[]
  voiceover   -> fills audio + captions[]
  (compute)   -> fills timing
  director    -> fills overlay_metadata (hook/cta)
  assembly    -> reads all of it, sets rendered_path
  qa          -> sets qa

Field names mirror the expected fal/ElevenLabs outputs. After the first LIVE run
we may rename a field or two to match the real payloads (esp. the fal video shape
captured in *-raw.json) — that's expected and contained to this file.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class Clip(BaseModel):
    index: int
    vo_line: str = ""                          # dialogue/narration for this beat
    lipsync: bool = False                      # True = a character speaks ON camera this beat
    motion_prompt: str = ""                    # action for this beat (from screenplay)
    duration: float = 0.0                      # screenplay timing; lip-sync beats overwrite w/ audio length

    # storyboard-driven inputs (the approved frame IS the look):
    storyboard_image_path: Optional[str] = None   # local approved frame
    start_frame_url: Optional[str] = None          # uploaded frame URL (fal input)

    # per-beat voiceover (lip-sync types drive each frame with its own line):
    audio_path: Optional[str] = None
    audio_url: Optional[str] = None

    # outputs:
    video_url: Optional[str] = None            # fal-hosted clip (fed to assembly)
    local_path: Optional[str] = None           # downloaded copy for QA
    animator_model: Optional[str] = None

    # legacy text-prompt flow (unused in storyboard mode):
    keyframe_prompt: Optional[str] = None
    keyframe_url: Optional[str] = None
    keyframe_model: Optional[str] = None


class Caption(BaseModel):
    text: str
    start: float                           # seconds on the final timeline
    end: float


class AudioTrack(BaseModel):
    vo_path: Optional[str] = None          # local VO mp3
    vo_url: Optional[str] = None           # hosted VO (assembly needs a URL)
    music_url: Optional[str] = None
    duration: float = 0.0


class ClipTiming(BaseModel):
    index: int
    start: float
    end: float


class Timing(BaseModel):
    total_duration: float = 0.0
    clips: list[ClipTiming] = Field(default_factory=list)


class OverlayMetadata(BaseModel):
    hook_text: Optional[str] = None        # on-screen opener
    cta_text: Optional[str] = None         # call to action
    brand_logo_url: Optional[str] = None


class QAResult(BaseModel):
    passed: bool = False
    layer_reached: int = 0                 # 1, 2, or 3
    scores: dict = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    calibration: bool = False              # True = scores logged, not enforced


class AssetBundle(BaseModel):
    ad_id: str
    ad_type: str                           # recipe name (preset or auto-detected label)
    vision_rubric: str = ""                # set from the recipe; used by QA layer 3
    script: str = ""
    character_bible: str = ""              # (legacy) hero character desc
    clips: list[Clip] = Field(default_factory=list)
    audio: AudioTrack = Field(default_factory=AudioTrack)
    captions: list[Caption] = Field(default_factory=list)
    timing: Timing = Field(default_factory=Timing)
    overlay_metadata: OverlayMetadata = Field(default_factory=OverlayMetadata)
    rendered_path: Optional[str] = None    # final vertical MP4
    qa: Optional[QAResult] = None

    def save(self, path: str) -> str:
        with open(path, "w") as f:
            f.write(self.model_dump_json(indent=2))
        return path

    @classmethod
    def load(cls, path: str) -> "AssetBundle":
        with open(path) as f:
            return cls.model_validate_json(f.read())

    def compute_timing(self) -> "Timing":
        """Lay clips end-to-end; total duration = sum of clip durations."""
        t = 0.0
        rows = []
        for c in self.clips:
            rows.append(ClipTiming(index=c.index, start=t, end=t + c.duration))
            t += c.duration
        self.timing = Timing(total_duration=t, clips=rows)
        return self.timing
