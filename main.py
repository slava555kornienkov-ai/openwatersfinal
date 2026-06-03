"""
Open Waters - Telegram Verification Backend
Real code verification via Telegram API
"""

import os
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberBannedError,
    SessionPasswordNeededError,
)

# ============ Config ============
TEST_API_ID = 2040
TEST_API_HASH = "b18441a1ff607e10a989891a5462e627"

API_ID = int(os.getenv("API_ID", str(TEST_API_ID)))
API_HASH = os.getenv("API_HASH", TEST_API_HASH)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
PORT = int(os.getenv("PORT", "8000"))

# ============ In-memory storage ============
_pending_codes: Dict[str, dict] = {}
_rate_limits: Dict[str, list] = {}
_auth_state = {"authorized": False, "phone": None}

# ============ Rate Limit ============
def check_rate_limit(ip: str) -> bool:
    now = datetime.utcnow().timestamp()
    window_start = now - 60
    _rate_limits.setdefault(ip, [])
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > window_start]
    if len(_rate_limits[ip]) >= 3:
        return False
    _rate_limits[ip].append(now)
    return True


# ============ Telegram Admin Client ============
admin_client: Optional[TelegramClient] = None
admin_lock = asyncio.Lock()


async def get_admin_client() -> TelegramClient:
    global admin_client
    if admin_client is None:
        async with admin_lock:
            if admin_client is None:
                admin_client = TelegramClient("/tmp/admin_session", API_ID, API_HASH)
    if not admin_client.is_connected():
        await admin_client.connect()
    return admin_client


# ============ REAL code verification ============
async def verify_code_telegram(phone: str, code: str, phone_code_hash: str) -> bool:
    """
    Creates a temporary Telegram client to verify the code.
    Returns True if code is correct, False otherwise.
    """
    temp_client = TelegramClient(StringSession(""), API_ID, API_HASH)
    try:
        await temp_client.connect()
        await temp_client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        await temp_client.log_out()
        return True
    except PhoneCodeInvalidError:
        return False
    except PhoneCodeExpiredError:
        return False
    except Exception as e:
        print(f"[Verify] Error: {e}")
        return False
    finally:
        await temp_client.disconnect()


# ============ FastAPI ============
app = FastAPI(title="Open Waters - Telegram Verification")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ Models ============
class SendCodeRequest(BaseModel):
    phone: str = Field(..., pattern=r"^\d{10,15}$")

class VerifyCodeRequest(BaseModel):
    phone: str = Field(..., pattern=r"^\d{10,15}$")
    code: str = Field(..., pattern=r"^\d{4,6}$")
    phone_code_hash: str

class AuthRequest(BaseModel):
    phone: str = Field(..., pattern=r"^\d{10,15}$")

class AuthCodeRequest(BaseModel):
    code: str = Field(..., min_length=1)

class AuthPasswordRequest(BaseModel):
    password: str = Field(..., min_length=1)


# ============ Endpoints ============

@app.get("/api/health")
async def health():
    return {"status": "ok", "authorized": _auth_state["authorized"]}

@app.post("/api/auth/send-code")
async def auth_send_code(data: AuthRequest):
    try:
        tg = await get_admin_client()
        result = await tg.send_code_request(data.phone)
        _auth_state["phone"] = data.phone
        _auth_state["phone_code_hash"] = result.phone_code_hash
        return {"success": True, "message": "Code sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-code")
async def auth_verify_code(data: AuthCodeRequest):
    try:
        tg = await get_admin_client()
        await tg.sign_in(phone=_auth_state["phone"], code=data.code, phone_code_hash=_auth_state.get("phone_code_hash"))
        _auth_state["authorized"] = True
        return {"success": True, "message": "Admin authorized"}
    except SessionPasswordNeededError:
        return {"success": False, "needs_password": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/password")
async def auth_password(data: AuthPasswordRequest):
    try:
        tg = await get_admin_client()
        await tg.sign_in(password=data.password)
        _auth_state["authorized"] = True
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/send-code")
async def send_code(data: SendCodeRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests. Wait 1 minute.")
    if not _auth_state["authorized"]:
        raise HTTPException(status_code=503, detail="Admin not authorized")
    try:
        tg = await get_admin_client()
        result = await tg.send_code_request(data.phone)
        _pending_codes[data.phone] = {
            "phone_code_hash": result.phone_code_hash,
            "expires": datetime.utcnow() + timedelta(minutes=5),
            "attempts": 0,
        }
        return {"success": True, "phone_code_hash": result.phone_code_hash, "message": "Code sent via Telegram"}
    except PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Wait {e.seconds}s")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/verify-code")
async def verify_code(data: VerifyCodeRequest):
    """
    REAL verification: checks code via Telegram API.
    Only returns verified=True if the code is correct.
    """
    stored = _pending_codes.get(data.phone)
    if not stored or stored["expires"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Code expired. Request new code.")
    if stored["phone_code_hash"] != data.phone_code_hash:
        raise HTTPException(status_code=400, detail="Invalid session.")
    
    stored["attempts"] = (stored.get("attempts", 0) + 1)
    if stored["attempts"] > 3:
        del _pending_codes[data.phone]
        raise HTTPException(status_code=400, detail="Too many attempts.")
    
    # REAL verification via Telegram API
    is_valid = await verify_code_telegram(data.phone, data.code, data.phone_code_hash)
    
    if is_valid:
        del _pending_codes[data.phone]
        return {"success": True, "verified": True, "message": "Phone verified successfully."}
    else:
        remaining = 3 - stored["attempts"]
        raise HTTPException(status_code=400, detail=f"Invalid code. {remaining} attempts left.")

@app.get("/")
async def root():
    return {"message": "Open Waters Telegram Verification API", "docs": "/docs"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)

