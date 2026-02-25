# Paper Reader

Upload academic PDFs, generate video scripts with Claude (parallel scriptwriting + aggregation), convert to voiceover with Qwen3-TTS via MLX on Apple Silicon, render Manim animations per segment, composite into final MP4 with ffmpeg, and play back with segment-level navigation.

## Quick Start

```bash
source .venv/bin/activate
cp .env.example .env   # set ANTHROPIC_API_KEY
python run.py           # http://localhost:8000
```

Requires `brew install ffmpeg` for MP3/MP4 export and `brew install py3cairo pango` for Manim. Python venv already set up in `.venv/`.

## Architecture

**Backend**: FastAPI (Python) with 3 routers, 10 services, and an in-memory task registry.
**Frontend**: Vanilla HTML/CSS/JS served as static files. Three-panel layout (papers, script, player).

### Directory Layout

```
app/
├── main.py              # FastAPI app, mounts routers + static files
├── config.py            # pydantic-settings, reads .env
├── models.py            # Pydantic schemas (Paper*, Script*, Pipeline*, AnimationHint)
├── storage.py           # Path helpers for data/{papers,processed,audio,scripts,exports,animations,videos}/
├── routers/
│   ├── papers.py        # PDF upload, LLM processing, SSE progress
│   ├── tts.py           # TTS generation (legacy, kept for backward compat)
│   └── pipeline.py      # Video pipeline: start, SSE stream, script, audio, animations, video, export
├── services/
│   ├── pdf_service.py   # PyMuPDF text extraction, section detection, sentence-boundary chunking
│   ├── llm_service.py   # Claude API (async streaming, prompt caching, verbatim/narrated modes)
│   ├── tts_service.py   # mlx-audio wrapper (ProcessPoolExecutor, lazy model load)
│   ├── audio_service.py # pydub concat + overlay + MP3 export
│   ├── director_service.py    # Pipeline orchestrator (load → script → voiceover → animation → compositing → save)
│   ├── scriptwriter_service.py # Parallel Claude scriptwriting + aggregation
│   ├── voiceover_service.py   # TTS wrapper with duration measurement per segment
│   ├── animation_service.py   # Manim renderer (ProcessPoolExecutor, hint→Scene mapping)
│   ├── animation_orchestrator.py # Iterates segments, renders animations, patches script
│   └── compositor_service.py  # ffmpeg mux (video+audio per segment) + concat to final MP4
└── tasks/
    └── processing.py    # In-memory task registry + SSE stream generator (supports stages)

static/
├── index.html
├── css/style.css
└── js/
    ├── api.js           # Fetch + EventSource SSE client
    ├── app.js           # Main state + pipeline flow
    ├── ui.js            # DOM rendering (papers, script segments, pipeline stages)
    └── audio-mixer.js   # Web Audio API dual-source mixer with GainNodes

data/                    # Runtime storage, gitignored
├── papers/{id}/         # Uploaded PDFs + meta.json
├── processed/{id}/      # verbatim.json / narrated.json (legacy)
├── audio/{id}/          # chunk_NNNN.wav files
├── scripts/{id}/        # script.json (VideoScript with segments + animation hints)
├── animations/{id}/     # segment_NNNN.mp4 Manim-rendered clips
├── videos/{id}/         # video.mp4 final composited video
└── exports/             # Voiceover MP3 files
```

### Pipeline Data Flow

1. **Upload PDF** → PyMuPDF extracts text → regex detects sections → chunks at sentence boundaries (~2000 chars)
2. **Generate Video** (pipeline) →
   - `director_service.run_pipeline()` orchestrates all phases
   - Phase 1: Load paper meta, group sections by title
   - Phase 2: `scriptwriter_service.write_script()` — parallel Claude calls per section group, then aggregator adds intro/outro/transitions → `VideoScript` with estimated durations
   - Phase 3: `voiceover_service.generate_voiceover()` — sequential TTS per segment via `tts_service.generate_chunk()`, measures actual durations with pydub
   - Phase 4: `animation_orchestrator.generate_animations()` — sequential Manim render per segment via `animation_service.render_segment()`, maps hint types (equation, bullet_list, diagram, highlight, code, graph, image_placeholder) to Manim scenes
   - Phase 5: `compositor_service.composite_video()` — ffmpeg mux (video+audio per segment), then concat into final MP4
   - Script saved to `data/scripts/{id}/script.json` after each phase
3. **Play** → Web Audio API plays speech segments with volume control, segment-level prev/next. Video player shows final MP4.
4. **Export** → pydub concatenates WAVs, exports MP3. ffmpeg serves final MP4 for download.

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/papers/upload` | Upload PDF, extract text |
| GET | `/api/papers` | List all papers |
| GET | `/api/papers/{id}` | Get paper metadata |
| POST | `/api/papers/{id}/process` | Start LLM processing (body: `{mode}`) |
| GET | `/api/papers/{id}/process/stream` | SSE progress (`?mode=verbatim`) |
| GET | `/api/papers/{id}/processed/{mode}` | Get processed sections JSON |
| POST | `/api/pipeline/{id}/start` | Start video pipeline (body: `{voice, speed}`) |
| GET | `/api/pipeline/{id}/stream` | SSE progress with stages |
| GET | `/api/pipeline/{id}/script` | Get VideoScript JSON |
| GET | `/api/pipeline/{id}/audio` | List audio segments |
| GET | `/api/pipeline/{id}/audio/{filename}` | Serve WAV file |
| POST | `/api/pipeline/{id}/export` | Export voiceover MP3 |
| GET | `/api/pipeline/{id}/animations` | List animation segment MP4s |
| GET | `/api/pipeline/{id}/animations/{filename}` | Serve animation MP4 file |
| GET | `/api/pipeline/{id}/video` | Serve final composited MP4 |
| POST | `/api/pipeline/{id}/export-video` | Export video MP4 download |
| POST | `/api/tts/generate` | Start TTS (legacy, body: `{paper_id, voice, speed}`) |
| GET | `/api/tts/{id}/stream` | SSE progress for TTS (legacy) |
| GET | `/api/tts/{id}/chunks` | List generated WAV files (legacy) |
| GET | `/api/tts/{id}/{filename}` | Serve WAV chunk (legacy) |

## Key Patterns

- **Pipeline orchestration**: `director_service` coordinates script → voiceover → animation → compositing phases. Task registry tracks `stage` and `stage_progress` alongside overall progress. Frontend renders a 6-step stage indicator (Loading → Scripting → Voiceover → Animation → Compositing → Done).
- **Parallel scriptwriting**: `scriptwriter_service` fans out one `asyncio.create_task` per section group (e.g. Abstract, Methods, Results each get their own Claude call). Results awaited in order for progress tracking. Aggregator pass adds intro/outro/transitions.
- **JSON parse retry**: Scriptwriter/aggregator Claude calls attempt to parse JSON from the response. On failure, retry once with prefilled assistant response (`[`). On second failure, fall back gracefully (raw segments or single fallback segment).
- **Manim animation rendering**: `animation_service` uses `ProcessPoolExecutor(1)` (mirrors TTS pattern) to render Manim scenes via subprocess. Hint types map to Manim objects: `equation`→`MathTex`+`Write`, `bullet_list`→`VGroup`+`FadeIn(lag_ratio)`, `diagram`→`Rectangle`+`Arrow`+`Create`, `highlight`→`Text`+`Indicate`, `code`→`Code`+`FadeIn`, `graph`→`Axes`+`Create`, `image_placeholder`→`Rectangle`+`Text`. 21+ free-form styles normalize to 5 Manim classes (Write, FadeIn, Create, GrowFromCenter, Indicate). Fallback on any error: plain text title card.
- **Video compositing**: `compositor_service` uses async ffmpeg subprocesses. Per-segment mux (`-c:v copy -c:a aac -shortest`), then concat demuxer for final `data/videos/{id}/video.mp4`.
- **Async background tasks**: Pipeline, LLM, and TTS processing run via `asyncio.create_task()`. Progress tracked in `task_registry` dict, streamed to frontend via SSE (0.5s polling in `sse_stream()`).
- **TTS concurrency**: `ProcessPoolExecutor` with 1 worker (MLX needs its own process). Sync generation runs in `run_in_executor()`.
- **Lazy model loading**: TTS model loaded on first request, not at startup. Uses `mlx_audio.tts.load_model()`.
- **Prompt caching**: Claude system prompts use `cache_control: {"type": "ephemeral"}` for cost reduction across chunks.
- **Progressive playback**: Frontend shows player as soon as first TTS chunk generates; refreshes chunk list during voiceover generation.
- **mlx-audio API**: Uses `generate_audio()` (not `generate_speech`). Key params: `model` (nn.Module), `output_path` (directory), `file_prefix` (stem), `join_audio=True`.

## Known Limitations

- **No persistence**: Task registry is in-memory only (lost on restart). Paper/audio/script data persists on filesystem.
- **No auth**: No authentication or rate limiting.
- **No retry**: Failed pipeline/LLM/TTS tasks stay failed; must re-trigger manually.
- **No cleanup**: Old data in `data/` grows indefinitely.
- **No logging**: Uses default uvicorn logging only.
- **Music/mix routers removed from main.py**: `music.py` and `mix.py` still exist as files but are not mounted. Pipeline export is speech-only.

## Dependencies

- `fastapi` + `uvicorn` — web framework
- `anthropic` — Claude API (async streaming)
- `PyMuPDF` (imported as `fitz`) — PDF text extraction
- `mlx-audio` — on-device TTS via Apple MLX (`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit`)
- `pydub` — audio manipulation (requires ffmpeg system dependency)
- `manim` — programmatic math/science animations (renders MP4 segments)
- `pydantic-settings` — config from `.env`
