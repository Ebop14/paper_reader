from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.routers import papers, tts, music, mix


@asynccontextmanager
async def lifespan(app):
    yield
    # Shut down the TTS process pool cleanly to avoid leaked semaphore warnings
    from app.services.tts_service import _executor
    if _executor is not None:
        _executor.shutdown(wait=False)


app = FastAPI(title="Paper Reader", lifespan=lifespan)

app.include_router(papers.router)
app.include_router(tts.router)
app.include_router(music.router)
app.include_router(mix.router)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_dir / "index.html"))
