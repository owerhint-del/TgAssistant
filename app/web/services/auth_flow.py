"""
Web-based Telegram authorization flow.
Step-by-step: send_code -> verify_code -> verify_2fa (if needed).
Wraps Telethon's auth for HTTP API usage.
"""
import asyncio
import logging
import os
import stat
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    FloodWaitError,
)

from app.config import Config
from app.auth.session_manager import make_client

logger = logging.getLogger("tgassistant.web.auth")


class AuthFlow:
    """
    Manages a multi-step Telegram auth process for the web UI.
    Keeps the client alive between steps (send_code -> verify).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client: Optional[TelegramClient] = None
        self._phone_code_hash: Optional[str] = None

    async def check_status(self) -> dict:
        """Check if Telegram is already authorized."""
        session_file = Path(self.cfg.tg_session_path + ".session")
        if not session_file.exists():
            return {"authorized": False, "reason": "no_session"}

        client = make_client(self.cfg)
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
            if authorized:
                me = await client.get_me()
                return {
                    "authorized": True,
                    "user": me.first_name if me else "Unknown",
                    "phone": self.cfg.tg_phone,
                }
            return {"authorized": False, "reason": "session_expired"}
        except Exception as e:
            logger.debug("Auth check error: %s", e)
            return {"authorized": False, "reason": str(e)}
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def send_code(self, phone: str) -> dict:
        """Step 1: Send verification code to phone."""
        # Clean up any previous flow
        await self._cleanup_client()

        self._client = make_client(self.cfg)
        await self._client.connect()

        try:
            sent = await self._client.send_code_request(phone)
            self._phone_code_hash = sent.phone_code_hash
            return {"success": True, "message": "Code sent"}
        except FloodWaitError as e:
            await self._cleanup_client()
            return {"success": False, "error": f"Rate limited. Wait {e.seconds} seconds."}
        except Exception as e:
            await self._cleanup_client()
            return {"success": False, "error": str(e)}

    async def verify_code(self, phone: str, code: str) -> dict:
        """Step 2: Verify the code. May return needs_2fa if 2FA is active."""
        if not self._client or not self._phone_code_hash:
            return {"success": False, "error": "No active auth flow. Send code first."}

        try:
            await self._client.sign_in(
                phone, code, phone_code_hash=self._phone_code_hash,
            )
            # Success — secure session file
            self._secure_session()
            me = await self._client.get_me()
            await self._cleanup_client()
            return {
                "success": True,
                "authorized": True,
                "user": me.first_name if me else "Unknown",
            }
        except SessionPasswordNeededError:
            return {"success": True, "needs_2fa": True, "message": "2FA password required"}
        except PhoneCodeInvalidError:
            return {"success": False, "error": "Invalid code. Try again."}
        except PhoneCodeExpiredError:
            await self._cleanup_client()
            return {"success": False, "error": "Code expired. Request a new one."}
        except Exception as e:
            await self._cleanup_client()
            return {"success": False, "error": str(e)}

    async def verify_2fa(self, password: str) -> dict:
        """Step 3: Verify 2FA password (only if verify_code returned needs_2fa)."""
        if not self._client:
            return {"success": False, "error": "No active auth flow."}

        try:
            await self._client.sign_in(password=password)
            self._secure_session()
            me = await self._client.get_me()
            await self._cleanup_client()
            return {
                "success": True,
                "authorized": True,
                "user": me.first_name if me else "Unknown",
            }
        except Exception as e:
            await self._cleanup_client()
            return {"success": False, "error": str(e)}

    def _secure_session(self):
        """Set session file permissions to 600."""
        session_file = self.cfg.tg_session_path + ".session"
        try:
            os.chmod(session_file, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    async def _cleanup_client(self):
        """Disconnect and discard the auth client."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
            self._phone_code_hash = None
