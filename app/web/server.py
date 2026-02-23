"""
Uvicorn server startup for TgAssistant Web UI.
"""
import logging
import threading
import time
import webbrowser

import uvicorn

from app.config import Config
from app.db.database import Database
from app.web import create_app

logger = logging.getLogger("tgassistant.web.server")


def start_server(cfg: Config, db: Database, host: str = "127.0.0.1", port: int = 8000):
    """Start the web server. Blocks until shutdown."""
    app = create_app(cfg, db)

    # Auto-open browser after a short delay
    def _open_browser():
        time.sleep(1.5)
        url = f"http://localhost:{port}"
        logger.info("Opening browser: %s", url)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()

    logger.info("Starting web server on %s:%d", host, port)
    print(f"\n  TgAssistant Web UI: http://localhost:{port}")
    print("  Press Ctrl+C to stop\n")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="warning",  # suppress uvicorn's own access logs
    )
