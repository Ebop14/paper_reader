from pydantic import BaseModel


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
    voice: str = "serena"
    speed: float = 1.0


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
