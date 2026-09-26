"""
FastAPI application entry point.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import os
from fastapi import Form, Response, status
from fastapi.responses import RedirectResponse

from app.database import engine
from app.models.models import Base
from app.routers import auth, host, upload, vote, websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
HOST_PASSWORD = "admin"

limiter = Limiter(key_func=get_remote_address)

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "uploads"

# Create directories at import time so StaticFiles mount works on first run
STATIC_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure an initial event and token exist
    from app.database import AsyncSessionLocal
    from app.services.event_service import get_or_create_active_token, get_or_create_event
    async with AsyncSessionLocal() as db:
        await get_or_create_event(db)
        await get_or_create_active_token(db)

    logger.info("✅  MEMECEPTION App started. Visit http://localhost:8000")
    yield
    await engine.dispose()


app = FastAPI(title="MEMECEPTION", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/static",  StaticFiles(directory=str(STATIC_DIR)),  name="static")

app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(vote.router)
app.include_router(host.router)
app.include_router(websocket.router)


@app.get("/", response_class=HTMLResponse)
async def participant_page():
    return FileResponse(STATIC_DIR / "participant.html")


@app.get("/join/{token}", response_class=HTMLResponse)
async def join_page(token: str):
    """Serve the participant page; JS will POST /join/{token} automatically."""
    return FileResponse(STATIC_DIR / "participant.html")


@app.get("/host/login", response_class=HTMLResponse)
async def host_login_page():
    return FileResponse(STATIC_DIR / "host_login.html")


@app.post("/host/login")
async def host_login(password: str = Form(...)):
    if password != HOST_PASSWORD:
        return RedirectResponse(url="/host/login?error=1", status_code=status.HTTP_303_SEE_OTHER)
    
    response = RedirectResponse(url="/host", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="host_token", value="authenticated", httponly=True, max_age=86400 * 30)
    return response


@app.get("/host", response_class=HTMLResponse)
async def host_page(request: Request):
    if request.cookies.get("host_token") != "authenticated":
        return RedirectResponse(url="/host/login")
    return FileResponse(STATIC_DIR / "host.html")
