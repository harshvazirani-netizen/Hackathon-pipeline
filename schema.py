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


class TextCue(BaseModel):
    """One on-screen text cue with its own timing, RELATIVE to the scene start."""
    text: str
    start: float = 0.0          # seconds from the scene's start
    end: float = 0.0
    position: str = "bottom"    # "bottom" | "top" (e.g. location pill)


class VoSegment(BaseModel):
    """One spoken line within a scene, attributed to a character (-> their voice)."""
    speaker: str = ""
    line: str = ""
    line_original: str = ""     # source before localization


class Clip(BaseModel):
    index: int
    vo_line: str = ""                          # (single-speaker convenience) spoken line
    vo_original: str = ""                      # the source line before any translation
    speaker: str = ""                          # (single-speaker convenience) who says vo_line
    vo_segments: list[VoSegment] = Field(default_factory=list)  # ordered (speaker, line) within the scene
    overlay_text: str = ""                     # (legacy) single caption for the whole beat
    text_cues: list[TextCue] = Field(default_factory=list)  # timed text cues within this scene (cue sheet)
    lipsync: bool = False                      # True = a character speaks ON camera this beat
    low_motion: bool = False                   # near-static render to keep on-frame text/logos/packaging legible
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

    def segments(self) -> list[VoSegment]:
        """Ordered VO segments for this scene; falls back to the single vo_line."""
        if self.vo_segments:
            return self.vo_segments
        if self.vo_line:
            return [VoSegment(speaker=self.speaker, line=self.vo_line)]
        return []


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
    add_captions: bool = True              # render text overlays (off when frames carry burned captions)
    vision_rubric: str = ""                # set from the recipe; used by QA layer 3
    script: str = ""
    character_bible: str = ""              # (legacy) hero character desc
    clips: list[Clip] = Field(default_factory=list)
    audio: AudioTrack = Field(default_factory=AudioTrack)
    captions: list[Caption] = Field(default_factory=list)          # per-word TTS captions
    overlay_captions: list[Caption] = Field(default_factory=list)  # screenplay text, timed to the voice
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
