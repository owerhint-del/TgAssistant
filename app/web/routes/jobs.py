"""
Job routes: submit link, list jobs, retry, job detail, main page.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()


class SubmitRequest(BaseModel):
    url: str
    from_start: bool = False


class RetryRequest(BaseModel):
    from_start: bool = False


# ── Pages ──────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    """Main page — link input + job list."""
    templates = request.app.state.templates
    return templates.TemplateResponse("index.html", {"request": request})


# ── API ────────────────────────────────────────────────────

@router.post("/api/jobs")
async def submit_job(body: SubmitRequest, request: Request):
    """Submit a Telegram link for processing."""
    svc = request.app.state.job_service
    try:
        result = await svc.submit(body.url, from_start=body.from_start)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/jobs")
async def list_jobs(request: Request, status: str = None):
    """List all jobs, optionally filtered by status."""
    svc = request.app.state.job_service
    jobs = svc.list_jobs(status_filter=status)
    return JSONResponse({"jobs": jobs})


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Get a single job with exports."""
    svc = request.app.state.job_service
    job = svc.get_job_detail(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse({"job": job})


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request, body: RetryRequest = RetryRequest()):
    """Retry a failed job."""
    svc = request.app.state.job_service
    try:
        result = await svc.retry(job_id, from_start=body.from_start)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
