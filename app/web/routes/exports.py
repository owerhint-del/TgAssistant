"""
Export routes: download PDF files.
"""
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


@router.get("/api/jobs/{job_id}/exports")
async def list_exports(job_id: str, request: Request):
    """List exports for a job."""
    db = request.app.state.db
    exports = db.get_exports(job_id)
    return JSONResponse({"exports": exports})


@router.get("/api/exports/{export_id}/download")
async def download_export(export_id: str, request: Request):
    """Download a PDF export file."""
    db = request.app.state.db

    # Find the export by ID
    row = db.conn.execute(
        "SELECT * FROM exports WHERE id = ?", (export_id,)
    ).fetchone()

    if not row:
        return JSONResponse({"error": "Export not found"}, status_code=404)

    export = dict(row)
    file_path = Path(export["file_path"])

    if not file_path.exists():
        return JSONResponse({"error": "File not found on disk"}, status_code=404)

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
    )
