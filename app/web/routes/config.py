"""
Config routes: read/update settings, setup page.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("tgassistant.web.config")

router = APIRouter()


class ConfigUpdate(BaseModel):
    tg_phone: Optional[str] = None
    output_dir: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None


# ── Pages ──────────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """Settings + auth page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("setup.html", {"request": request})


# ── API ────────────────────────────────────────────────────

@router.get("")
async def get_config(request: Request):
    """Return current config (sensitive values masked)."""
    cfg = request.app.state.cfg
    return JSONResponse({
        "tg_phone": cfg.tg_phone or "",
        "output_dir": cfg.output_dir,
        "whisper_model": cfg.whisper_model,
        "whisper_language": cfg.whisper_language,
    })


@router.put("")
async def update_config(body: ConfigUpdate, request: Request):
    """
    Update config values in memory and persist to config.yaml.
    Only updates provided (non-null) fields.
    """
    cfg = request.app.state.cfg
    updated = []

    for field_name, value in body.model_dump(exclude_none=True).items():
        if hasattr(cfg, field_name):
            setattr(cfg, field_name, value)
            updated.append(field_name)

    if updated:
        _save_config_yaml(cfg)
        logger.info("Config updated: %s", ", ".join(updated))

    return JSONResponse({"updated": updated, "success": True})


def _save_config_yaml(cfg):
    """Persist current config to config.yaml."""
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not available, cannot save config")
        return

    config_path = Path("config.yaml")
    data = {}
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Update relevant sections (secrets stay in .env, not YAML)
    data.setdefault("telegram", {})
    data["telegram"]["phone"] = cfg.tg_phone

    data.setdefault("pipeline", {})
    data["pipeline"]["output_dir"] = cfg.output_dir

    data.setdefault("asr", {})
    data["asr"]["model_size"] = cfg.whisper_model
    data["asr"]["language"] = cfg.whisper_language

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
