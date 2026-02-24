import json
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, HTTPException
from fastapi.responses import StreamingResponse

from app.models import PaperMeta, PaperSection, ProcessRequest
from app.storage import papers_dir, processed_dir
from app.services.pdf_service import process_pdf
from app.services.llm_service import process_chunks
from app.tasks.processing import task_registry, create_task, update_task, sse_stream

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _load_meta(paper_id: str) -> PaperMeta:
    meta_path = papers_dir() / paper_id / "meta.json"
    if not meta_path.exists():
        raise HTTPException(404, "Paper not found")
    return PaperMeta.model_validate_json(meta_path.read_text())


@router.post("/upload", response_model=PaperMeta)
async def upload_pdf(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    paper_id = uuid.uuid4().hex[:12]
    paper_dir = papers_dir() / paper_id
    paper_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = paper_dir / file.filename
    content = await file.read()
    pdf_path.write_bytes(content)

    sections, num_pages = process_pdf(pdf_path)
    total_chars = sum(len(s.text) for s in sections)

    meta = PaperMeta(
        id=paper_id,
        filename=file.filename,
        num_pages=num_pages,
        sections=sections,
        total_chars=total_chars,
    )
    (paper_dir / "meta.json").write_text(meta.model_dump_json(indent=2))
    return meta


@router.get("/{paper_id}")
async def get_paper(paper_id: str):
    return _load_meta(paper_id)


@router.get("")
async def list_papers():
    results = []
    base = papers_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            meta_path = d / "meta.json"
            if meta_path.exists():
                results.append(json.loads(meta_path.read_text()))
    return results


@router.post("/{paper_id}/process")
async def start_processing(paper_id: str, req: ProcessRequest):
    meta = _load_meta(paper_id)
    task_id = f"llm-{paper_id}-{req.mode}"

    existing = task_registry.get(task_id)
    if existing and existing["status"] == "running":
        return {"task_id": task_id, "status": "running"}

    create_task(task_id, total_chunks=len(meta.sections))

    import asyncio
    asyncio.create_task(
        _run_processing(task_id, paper_id, meta.sections, req.mode)
    )
    return {"task_id": task_id, "status": "started"}


async def _run_processing(
    task_id: str, paper_id: str, sections: list[PaperSection], mode: str
):
    out_dir = processed_dir() / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)

    texts = [s.text for s in sections]
    results: list[str] = []

    try:
        async for i, processed_text in process_chunks(texts, mode):
            results.append(processed_text)
            update_task(task_id, status="running", current_chunk=i + 1,
                        message=f"Processed chunk {i + 1}/{len(texts)}")

        # Save processed sections
        processed_sections = []
        for j, section in enumerate(sections):
            processed_sections.append(
                PaperSection(
                    title=section.title,
                    text=results[j] if j < len(results) else section.text,
                    chunk_index=j,
                ).model_dump()
            )
        (out_dir / f"{mode}.json").write_text(json.dumps(processed_sections, indent=2))
        update_task(task_id, status="completed", message="Done")
    except Exception as e:
        update_task(task_id, status="failed", message=str(e))


@router.get("/{paper_id}/process/stream")
async def stream_processing(paper_id: str, mode: str = "verbatim"):
    task_id = f"llm-{paper_id}-{mode}"
    return StreamingResponse(sse_stream(task_id), media_type="text/event-stream")


@router.get("/{paper_id}/processed/{mode}")
async def get_processed(paper_id: str, mode: str):
    path = processed_dir() / paper_id / f"{mode}.json"
    if not path.exists():
        raise HTTPException(404, "Processed text not found")
    return json.loads(path.read_text())
