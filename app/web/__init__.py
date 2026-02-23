"""
FastAPI app factory for TgAssistant Web UI.
"""
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Config
from app.db.database import Database
from app.web.services.job_service import JobService
from app.web.services.auth_flow import AuthFlow

logger = logging.getLogger("tgassistant.web")

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def create_app(cfg: Config, db: Database) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="TgAssistant",
        description="Telegram media transcription tool",
        docs_url=None,  # disable Swagger UI in production
        redoc_url=None,
    )

    # Shared services — stored in app.state for route access
    app.state.cfg = cfg
    app.state.db = db
    app.state.job_service = JobService(cfg, db)
    app.state.auth_flow = AuthFlow(cfg)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register routes
    from app.web.routes.jobs import router as jobs_router
    from app.web.routes.exports import router as exports_router
    from app.web.routes.events import router as events_router
    from app.web.routes.auth import router as auth_router
    from app.web.routes.config import router as config_router

    app.include_router(jobs_router)
    app.include_router(exports_router)
    app.include_router(events_router)
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(config_router, prefix="/api/config")

    logger.info("Web UI initialized — templates: %s", TEMPLATES_DIR)
    return app
