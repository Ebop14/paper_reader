# Paper Reader

Upload academic PDFs, process them with Claude (verbatim cleanup or podcast-style narration), convert to speech with Qwen3-TTS via MLX on Apple Silicon, and listen with blended lo-fi background music.

## Quick Start

```bash
source .venv/bin/activate
cp .env.example .env   # set ANTHROPIC_API_KEY
python run.py           # http://localhost:8000
```

Requires `brew install ffmpeg` for MP3 export. Python venv already set up in `.venv/`.

## Architecture

**Backend**: FastAPI (Python) with 4 routers, 4 services, and an in-memory task registry.
**Frontend**: Vanilla HTML/CSS/JS served as static files. Three-panel layout (papers, text, player).

### Directory Layout

```
app/
├── main.py              # FastAPI app, mounts routers + static files
├── config.py            # pydantic-settings, reads .env
├── models.py            # Pydantic schemas (PaperMeta, PaperSection, request models)
├── storage.py           # Path helpers for data/{papers,processed,audio,music,exports}/
├── routers/
│   ├── papers.py        # PDF upload, LLM processing, SSE progress
│   ├── tts.py           # TTS generation, progress, audio serving
│   ├── music.py         # Background music CRUD
│   └── mix.py           # Server-side audio export
├── services/
│   ├── pdf_service.py   # PyMuPDF text extraction, section detection, sentence-boundary chunking
│   ├── llm_service.py   # Claude API (async streaming, prompt caching, verbatim/narrated modes)
│   ├── tts_service.py   # mlx-audio wrapper (lazy model load, asyncio.Lock, run_in_executor)
│   └── audio_service.py # pydub concat + overlay + MP3 export
└── tasks/
    └── processing.py    # In-memory task registry + SSE stream generator

static/
├── index.html
├── css/style.css
└── js/
    ├── api.js           # Fetch + EventSource SSE client
    ├── app.js           # Main state + event wiring
    ├── ui.js            # DOM rendering helpers
    └── audio-mixer.js   # Web Audio API dual-source mixer with GainNodes

data/                    # Runtime storage, gitignored
├── papers/{id}/         # Uploaded PDFs + meta.json
├── processed/{id}/      # verbatim.json / narrated.json
├── audio/{id}/          # chunk_NNNN.wav files
├── music/               # Uploaded music files + metadata JSONs
└── exports/             # Mixed MP3 files
```

### Data Flow

1. **Upload PDF** → PyMuPDF extracts text → regex detects sections (Abstract, Intro, Methods...) → chunks at sentence boundaries (~2000 chars)
2. **Process** → Chunks sent sequentially to Claude (`claude-sonnet-4-20250514`) → SSE progress
3. **Generate TTS** → Processed text fed to Qwen3-TTS via mlx-audio → WAV per chunk → SSE progress
4. **Play** → Web Audio API routes speech + music through separate GainNodes → volume sliders
5. **Export** → pydub concatenates WAVs, overlays looped music, exports MP3

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/papers/upload` | Upload PDF, extract text |
| GET | `/api/papers` | List all papers |
| GET | `/api/papers/{id}` | Get paper metadata |
| POST | `/api/papers/{id}/process` | Start LLM processing (body: `{mode}`) |
| GET | `/api/papers/{id}/process/stream` | SSE progress (`?mode=verbatim`) |
| GET | `/api/papers/{id}/processed/{mode}` | Get processed sections JSON |
| POST | `/api/tts/generate` | Start TTS (body: `{paper_id, voice, speed}`) |
| GET | `/api/tts/{id}/stream` | SSE progress for TTS |
| GET | `/api/tts/{id}/chunks` | List generated WAV files |
| GET | `/api/tts/{id}/{filename}` | Serve WAV chunk |
| POST | `/api/music/upload` | Upload background music |
| GET | `/api/music` | List music |
| GET | `/api/music/{id}` | Serve music file |
| DELETE | `/api/music/{id}` | Delete music |
| POST | `/api/mix/export` | Export mixed MP3 (body: `{paper_id, music_id, speech_volume, music_volume}`) |

## Key Patterns

- **Async background tasks**: LLM and TTS processing run via `asyncio.create_task()`. Progress tracked in `task_registry` dict, streamed to frontend via SSE (0.5s polling in `sse_stream()`).
- **TTS concurrency safety**: `asyncio.Lock` serializes TTS generation (MLX not thread-safe). Sync generation runs in `run_in_executor()`.
- **Lazy model loading**: TTS model (`_load_model()`) loaded on first request, not at startup. Uses `mlx_audio.tts.load_model()`.
- **Prompt caching**: Claude system prompts use `cache_control: {"type": "ephemeral"}` for cost reduction across chunks.
- **Progressive playback**: Frontend shows player as soon as first TTS chunk generates; refreshes chunk list during generation.
- **mlx-audio API**: Uses `generate_audio()` (not `generate_speech`). Key params: `model` (nn.Module), `output_path` (directory), `file_prefix` (stem), `join_audio=True`.

## Known Limitations

- **No persistence**: Task registry is in-memory only (lost on restart). Paper/audio data persists on filesystem.
- **No auth**: No authentication or rate limiting.
- **No retry**: Failed LLM/TTS tasks stay failed; must re-trigger manually.
- **Sequential processing**: LLM chunks processed one-by-one (not parallelized).
- **No cleanup**: Old data in `data/` grows indefinitely.
- **No logging**: Uses default uvicorn logging only.

## Dependencies

- `fastapi` + `uvicorn` — web framework
- `anthropic` — Claude API (async streaming)
- `PyMuPDF` (imported as `fitz`) — PDF text extraction
- `mlx-audio` — on-device TTS via Apple MLX (`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit`)
- `pydub` — audio manipulation (requires ffmpeg system dependency)
- `pydantic-settings` — config from `.env`
