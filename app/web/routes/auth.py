"""
Telegram auth routes: multi-step authorization via HTTP.
POST /api/auth/status      — check if authorized
POST /api/auth/send-code   — send verification code
POST /api/auth/verify-code — verify SMS/app code
POST /api/auth/verify-2fa  — verify 2FA password
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class SendCodeRequest(BaseModel):
    phone: str


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str


class Verify2FARequest(BaseModel):
    password: str


@router.get("/status")
async def auth_status(request: Request):
    """Check current Telegram authorization status."""
    auth_flow = request.app.state.auth_flow
    result = await auth_flow.check_status()
    return JSONResponse(result)


@router.post("/send-code")
async def send_code(body: SendCodeRequest, request: Request):
    """Step 1: Send verification code to phone number."""
    auth_flow = request.app.state.auth_flow
    result = await auth_flow.send_code(body.phone)
    status = 200 if result.get("success") else 400
    return JSONResponse(result, status_code=status)


@router.post("/verify-code")
async def verify_code(body: VerifyCodeRequest, request: Request):
    """Step 2: Verify the code received via SMS/Telegram."""
    auth_flow = request.app.state.auth_flow
    result = await auth_flow.verify_code(body.phone, body.code)
    status = 200 if result.get("success") else 400
    return JSONResponse(result, status_code=status)


@router.post("/verify-2fa")
async def verify_2fa(body: Verify2FARequest, request: Request):
    """Step 3: Verify 2FA password (if required after step 2)."""
    auth_flow = request.app.state.auth_flow
    result = await auth_flow.verify_2fa(body.password)
    status = 200 if result.get("success") else 400
    return JSONResponse(result, status_code=status)
