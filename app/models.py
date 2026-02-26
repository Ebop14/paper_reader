from datetime import datetime

from pydantic import BaseModel, Field


class PaperSection(BaseModel):
    title: str
    text: str
    chunk_index: int


class PaperMeta(BaseModel):
    id: str
    filename: str
    num_pages: int
    sections: list[PaperSection]
    total_chars: int


class ProcessRequest(BaseModel):
    mode: str = "verbatim"  # "verbatim" or "narrated"


class TTSRequest(BaseModel):
    paper_id: str
    voice: str = "af_heart"
    speed: float = 1.0


class MusicGenerateRequest(BaseModel):
    prompt: str
    duration: int = 30


class MixExportRequest(BaseModel):
    paper_id: str
    music_id: str | None = None
    speech_volume: float = 1.0
    music_volume: float = 0.3


class TaskStatus(BaseModel):
    task_id: str
    status: str  # "pending", "running", "completed", "failed"
    progress: float = 0.0
    current_chunk: int = 0
    total_chunks: int = 0
    message: str = ""


# --- Video Pipeline Models ---


class ManimObject(BaseModel):
    name: str              # e.g. "eq1", "box_a"
    mobject_type: str       # "Text", "MathTex", "Rectangle", "Axes", "BarChart", etc.
    params: dict = {}      # type-specific params (text, color, width, etc.)
    position: str = ""     # "ORIGIN", "to_edge(UP)", "[-3, 0, 0]", etc.


class AnimationStep(BaseModel):
    action: str            # "create", "write", "fade_in", "fade_out", "indicate", "transform", "wait", etc.
    target: str            # name of object to act on
    params: dict = {}      # action-specific params (run_time, shift, scale_factor, etc.)
    duration: float = 1.0


class AnimationHint(BaseModel):
    # Legacy fields (backward compat, all have defaults)
    type: str = ""  # e.g. "equation", "diagram", "bullet_list", "highlight"
    description: str = ""
    content: str = ""  # Raw content (LaTeX, bullet text, etc.)
    style: str = ""  # e.g. "fade_in", "write", "transform"
    # Rich Manim-aware fields
    objects: list[ManimObject] = Field(default_factory=list)
    steps: list[AnimationStep] = Field(default_factory=list)
    anchor_text: str = ""              # substring of narration_text this hint is tied to
    persistent: bool = False          # objects stay for next hint
    start_fraction: float = 0.0       # timing within segment (0.0-1.0)
    end_fraction: float = 1.0


class ScriptSegment(BaseModel):
    segment_index: int
    section_title: str
    source_chunk_indices: list[int] = Field(default_factory=list)
    narration_text: str
    speaker_notes: str = ""
    visual_strategy: str = ""
    animation_hints: list[AnimationHint] = Field(default_factory=list)
    manim_code: str = ""  # Raw Manim construct() body written by LLM
    tts_chunks: list[str] = Field(default_factory=list)  # Sub-chunks of narration for smoother TTS
    estimated_duration_seconds: float = 0.0
    actual_duration_seconds: float | None = None
    audio_file: str | None = None
    animation_file: str | None = None


class VideoScript(BaseModel):
    paper_id: str
    title: str
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    total_segments: int = 0
    estimated_total_duration_seconds: float = 0.0
    actual_total_duration_seconds: float | None = None
    segments: list[ScriptSegment] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    video_file: str | None = None


class PipelineRequest(BaseModel):
    voice: str = "af_heart"
    speed: float = 1.0
