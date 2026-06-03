"""
Open Waters - Telegram Verification Backend
"""

import os
import asyncio
import traceback
import re
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
    SessionPasswordNeededError,
)

TEST_API_ID = 2040
TEST_API_HASH = "b18441a1ff607e10a989891a5462e627"
API_ID = int(os.getenv("API_ID", str(TEST_API_ID)))
API_HASH = os.getenv("API_HASH", TEST_API_HASH)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
PORT = int(os.getenv("PORT", "8000"))

_pending_codes: Dict[str, dict] = {}
_rate_limits: Dict[str, list] = {}
_auth_state = {"authorized": False, "phone": None}


def check_rate_limit(ip: str) -> bool:
    now = datetime.utcnow().timestamp()
    window_start = now - 60
    _rate_limits.setdefault(ip, [])
    _rate_limits[ip] = [t for t in _rate_limits[ip] if t > window_start]
    if len(_rate_limits[ip]) >= 3:
        return False
    _rate_limits[ip].append(now)
    return True


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


def handle_telegram_error(e: Exception) -> str:
    err_str = str(e)
    if "all available options" in err_str or "already used" in err_str or "ResendCodeRequest" in err_str:
        return "Слишком большая активность. Попробуйте снова через 5 минут."
    if "FLOOD_WAIT" in err_str or "flood" in err_str.lower():
        match = re.search(r'(\d+)', err_str)
        if match:
            wait_min = max(1, round(int(match.group(1)) / 60))
            return f"Слишком большая активность. Попробуйте снова через {wait_min} мин."
        return "Слишком большая активность. Попробуйте снова через 5 минут."
    return str(e)


app = FastAPI(title="Open Waters - Telegram Verification")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        raise HTTPException(status_code=500, detail=handle_telegram_error(e))

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
        raise HTTPException(status_code=400, detail=handle_telegram_error(e))

@app.post("/api/auth/password")
async def auth_password(data: dict):
    try:
        tg = await get_admin_client()
        await tg.sign_in(password=data.get("password", ""))
        _auth_state["authorized"] = True
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail="Неверный пароль")

@app.post("/api/send-code")
async def send_code(data: SendCodeRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Слишком большая активность. Попробуйте снова через 1 минуту.")
    if not _auth_state["authorized"]:
        raise HTTPException(status_code=503, detail="Admin not authorized")
    try:
        tg = await get_admin_client()
        result = await tg.send_code_request(data.phone)
        _pending_codes[data.phone] = {
            "phone_code_hash": result.phone_code_hash,
            "expires": datetime.utcnow() + timedelta(minutes=5),
        }
        return {"success": True, "phone_code_hash": result.phone_code_hash, "message": "Code sent"}
    except PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail="Неверный номер телефона")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=handle_telegram_error(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=handle_telegram_error(e))

@app.post("/api/verify-code")
async def verify_code(data: VerifyCodeRequest):
    stored = _pending_codes.get(data.phone)
    if not stored or stored["expires"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый.")
    if stored["phone_code_hash"] != data.phone_code_hash:
        raise HTTPException(status_code=400, detail="Неверная сессия.")
    temp_client = TelegramClient(StringSession(), API_ID, API_HASH)
    try:
        await temp_client.connect()
        await temp_client.sign_in(phone=data.phone, code=data.code, phone_code_hash=data.phone_code_hash)
        try:
            await temp_client.log_out()
        except:
            pass
        del _pending_codes[data.phone]
        return {"success": True, "verified": True, "message": "Номер подтверждён"}
    except PhoneCodeInvalidError:
        del _pending_codes[data.phone]
        raise HTTPException(status_code=400, detail="Неверный код. Запросите новый.")
    except PhoneCodeExpiredError:
        del _pending_codes[data.phone]
        raise HTTPException(status_code=400, detail="Код истёк. Запросите новый.")
    except Exception as e:
        traceback.print_exc()
        del _pending_codes[data.phone]
        raise HTTPException(status_code=400, detail="Неверный код. Запросите новый.")
    finally:
        await temp_client.disconnect()

@app.get("/")
async def root():
    return {"message": "Open Waters API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
