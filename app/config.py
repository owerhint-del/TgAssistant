"""
Единый загрузчик конфигурации.
Приоритет: CLI аргументы > ENV vars > config.yaml > defaults
"""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # первый запуск до pip install

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


@dataclass
class Config:
    # ─── Telegram ────────────────────────────────────────────
    # Built-in defaults (standard Telethon). Override via ENV if needed.
    tg_api_id: int = 2040
    tg_api_hash: str = "b18441a1ff607e10a989891a5462e627"
    tg_phone: str = ""
    tg_session_path: str = "./sessions/tgassistant"

    # ─── Pipeline ────────────────────────────────────────────
    output_dir: str = str(Path.home() / "Desktop" / "TgAssistant_output")
    temp_dir: str = "./temp"
    max_retries: int = 3
    retry_backoff_sec: float = 30.0
    concurrency: int = 1
    log_level: str = "INFO"
    max_duration_sec: int = 7200   # 2 часа
    max_file_mb: int = 2000

    # ─── ASR ─────────────────────────────────────────────────
    whisper_model: str = "large-v3"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True
    whisper_language: str = "ru"
    whisper_timestamps: bool = True

    # ─── LLM ─────────────────────────────────────────────────
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    llm_max_tokens: int = 8192
    summary_language: str = "ru"

    # ─── PDF ─────────────────────────────────────────────────
    pdf_font_path: str = "./fonts/DejaVuSans.ttf"
    pdf_bold_font_path: str = "./fonts/DejaVuSans-Bold.ttf"
    pdf_page_size: str = "A4"
    pdf_split_mode: str = "two_pdfs"   # two_pdfs | single_pdf

    # ─── yt-dlp ──────────────────────────────────────────────
    ytdlp_cookies_file: str = ""           # path to cookies.txt (Netscape format)
    ytdlp_format: str = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"  # prefer mp4

    # ─── Cleanup ─────────────────────────────────────────────
    cleanup_temp: bool = True
    orphan_retention_hours: int = 24

    # ─── Bot ────────────────────────────────────────────────
    bot_token: str = ""                                    # TG_BOT_TOKEN
    bot_admin_ids: list = field(default_factory=list)      # TG_BOT_ADMIN_IDS (comma-separated)

    # ─── Paths ───────────────────────────────────────────────
    db_path: str = "./data/tasks.db"
    log_dir: str = "./logs"
    fonts_dir: str = "./fonts"


def _yaml_value(data: dict, *keys):
    """Получить вложенное значение из YAML dict по цепочке ключей."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def load_config(
    config_file: Optional[str] = None,
    overrides: Optional[dict] = None,
) -> Config:
    """
    Загружает конфигурацию с приоритетом:
    CLI overrides > ENV > config.yaml > defaults

    Args:
        config_file: путь к config.yaml (None — ищет ./config.yaml)
        overrides: словарь CLI-аргументов (только те, что реально переданы)
    """
    cfg = Config()
    overrides = overrides or {}

    # ── Шаг 1: YAML ──────────────────────────────────────────
    yaml_data: dict = {}
    if _HAS_YAML:
        yaml_path = config_file or "config.yaml"
        if Path(yaml_path).exists():
            with open(yaml_path, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}

    def y(*keys):
        return _yaml_value(yaml_data, *keys)

    # ── Шаг 2: ENV > YAML > default ──────────────────────────
    def get(env_key: str, yaml_val, default):
        """ENV → YAML → default"""
        env = os.getenv(env_key)
        if env is not None:
            return env
        if yaml_val is not None:
            return yaml_val
        return default

    cfg.tg_api_id        = int(get("TG_API_ID",      y("telegram", "api_id"),      cfg.tg_api_id) or 0)
    cfg.tg_api_hash      = get("TG_API_HASH",         y("telegram", "api_hash"),    cfg.tg_api_hash)
    cfg.tg_phone         = get("TG_PHONE",            y("telegram", "phone"),       cfg.tg_phone)
    cfg.tg_session_path  = get("TG_SESSION_PATH",     y("telegram", "session_path"),cfg.tg_session_path)

    cfg.output_dir         = get("OUTPUT_DIR",          y("pipeline", "output_dir"),       cfg.output_dir)
    cfg.temp_dir           = get("TEMP_DIR",            y("pipeline", "temp_dir"),         cfg.temp_dir)
    cfg.max_retries        = int(get("MAX_RETRIES",     y("pipeline", "max_retries"),      cfg.max_retries))
    cfg.retry_backoff_sec  = float(get("RETRY_BACKOFF", y("pipeline", "retry_backoff_sec"),cfg.retry_backoff_sec))
    cfg.concurrency        = int(get("CONCURRENCY",     y("pipeline", "concurrency"),      cfg.concurrency))
    cfg.log_level          = get("LOG_LEVEL",           y("pipeline", "log_level"),        cfg.log_level)
    cfg.max_duration_sec   = int(get("MAX_DURATION_SEC",y("pipeline", "max_duration_sec"), cfg.max_duration_sec))
    cfg.max_file_mb        = int(get("MAX_FILE_MB",     y("pipeline", "max_file_mb"),      cfg.max_file_mb))

    cfg.whisper_model        = get("WHISPER_MODEL",      y("asr", "model_size"),      cfg.whisper_model)
    cfg.whisper_device       = get("WHISPER_DEVICE",     y("asr", "device"),          cfg.whisper_device)
    cfg.whisper_compute_type = get("WHISPER_COMPUTE_TYPE",y("asr","compute_type"),    cfg.whisper_compute_type)
    cfg.whisper_beam_size    = int(get("WHISPER_BEAM",   y("asr", "beam_size"),       cfg.whisper_beam_size))
    _vad = get("WHISPER_VAD", y("asr", "vad_filter"), cfg.whisper_vad_filter)
    cfg.whisper_vad_filter   = _vad not in (False, "false", "False", "0", 0)
    cfg.whisper_language     = get("WHISPER_LANG",       y("asr", "language"),        cfg.whisper_language)
    _ts = get("WHISPER_TIMESTAMPS", y("asr", "timestamps"), cfg.whisper_timestamps)
    cfg.whisper_timestamps   = _ts not in (False, "false", "False", "0", 0)

    cfg.llm_provider      = get("LLM_PROVIDER",      y("llm", "provider"),       cfg.llm_provider)
    cfg.llm_model         = get("LLM_MODEL",          y("llm", "model"),          cfg.llm_model)
    cfg.anthropic_api_key = get("ANTHROPIC_API_KEY",  y("llm", "api_key"),        cfg.anthropic_api_key)
    cfg.llm_max_tokens    = int(get("LLM_MAX_TOKENS", y("llm", "max_tokens"),     cfg.llm_max_tokens))
    cfg.summary_language  = get("SUMMARY_LANGUAGE",   y("llm", "summary_language"),cfg.summary_language)

    cfg.pdf_font_path  = get("PDF_FONT_PATH",   y("pdf", "font_path"),  cfg.pdf_font_path)
    cfg.pdf_page_size  = get("PDF_PAGE_SIZE",   y("pdf", "page_size"),  cfg.pdf_page_size)
    cfg.pdf_split_mode = get("PDF_SPLIT_MODE",  y("pdf", "split_mode"), cfg.pdf_split_mode)

    cfg.ytdlp_cookies_file = get("YTDLP_COOKIES_FILE", y("ytdlp", "cookies_file"), cfg.ytdlp_cookies_file)
    cfg.ytdlp_format       = get("YTDLP_FORMAT",       y("ytdlp", "format"),        cfg.ytdlp_format)

    cfg.bot_token = get("TG_BOT_TOKEN", y("bot", "token"), cfg.bot_token)
    _admin_ids_raw = get("TG_BOT_ADMIN_IDS", y("bot", "admin_ids"), "")
    if isinstance(_admin_ids_raw, list):
        cfg.bot_admin_ids = [int(x) for x in _admin_ids_raw if x]
    elif isinstance(_admin_ids_raw, str) and _admin_ids_raw.strip():
        cfg.bot_admin_ids = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()]
    else:
        cfg.bot_admin_ids = []

    _ct = get("CLEANUP_TEMP", y("cleanup", "cleanup_temp"), cfg.cleanup_temp)
    cfg.cleanup_temp         = _ct not in (False, "false", "False", "0", 0)
    cfg.orphan_retention_hours = int(
        get("ORPHAN_RETENTION_HOURS", y("cleanup", "orphan_retention_hours"), cfg.orphan_retention_hours)
    )

    # ── Шаг 3: CLI overrides (наивысший приоритет) ───────────
    for key, val in overrides.items():
        if val is not None and hasattr(cfg, key):
            setattr(cfg, key, val)

    # ── Нормализация путей ────────────────────────────────────
    cfg.output_dir = str(Path(cfg.output_dir).expanduser())
    cfg.temp_dir   = str(Path(cfg.temp_dir).expanduser())

    return cfg


def validate_config(cfg: Config, require_tg: bool = True) -> list:
    """
    Проверяет конфигурацию. Возвращает список ошибок (пустой = OK).
    """
    errors = []

    if require_tg:
        if not cfg.tg_phone:
            errors.append(
                "TG_PHONE not set. Example: TG_PHONE=+49123456789"
            )

    # ANTHROPIC_API_KEY is optional — only needed if summary features are re-enabled
    # if not cfg.anthropic_api_key:
    #     errors.append(
    #         "ANTHROPIC_API_KEY not set. Get yours at https://console.anthropic.com/keys"
    #     )

    if not Path(cfg.pdf_font_path).exists():
        errors.append(
            f"Font not found: {cfg.pdf_font_path}\n"
            "  Run: python run.py --setup  (font will be downloaded automatically)"
        )

    return errors
