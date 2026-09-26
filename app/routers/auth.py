"""
Auth router: participant join flow and session management.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import InviteToken, Participant, Phase
from app.services.event_service import (
    broadcast_stats,
    get_or_create_event,
    rotate_token,
    get_leaderboard,
)
from app.services.qr_service import generate_token
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_COOKIE = "session_id"

class JoinRequest(BaseModel):
    name: str
    topic: str


async def get_current_participant(
    request: Request,
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Cookie(default=None),
) -> Participant:
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Participant)
        .options(selectinload(Participant.upload))
        .where(Participant.session_id == session_id)
    )
    participant = result.scalar_one_or_none()
    if not participant:
        raise HTTPException(status_code=401, detail="Session not found")
    return participant



@router.post("/join/{token}")
async def join_event(
    token: str,
    join_data: JoinRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    session_id: str | None = Cookie(default=None),
):
    """
    Claim an invite token and create a participant session.
    If the caller already has a valid session, return their existing info.
    """
    # Already have a session?
    if session_id:
        existing = await db.execute(select(Participant).where(Participant.session_id == session_id))
        participant = existing.scalar_one_or_none()
        if participant:
            event = await get_or_create_event(db)
            return {
                "participant_id": participant.id,
                "display_number": participant.display_number,
                "phase": event.phase.value,
                "already_joined": True,
            }

    # Validate token
    result = await db.execute(select(InviteToken).where(InviteToken.token == token))
    invite = result.scalar_one_or_none()
    if not invite or invite.used:
        raise HTTPException(status_code=400, detail="Invalid or already used invite link.")

    # Get max display_number
    from sqlalchemy import func
    max_num = (await db.execute(select(func.max(Participant.display_number)))).scalar() or 0

    # Create participant
    new_session = generate_token()
    participant  = Participant(
        session_id     = new_session,
        display_number = max_num + 1,
        is_connected   = False,
        name           = join_data.name,
        topic          = join_data.topic,
    )
    db.add(participant)
    await db.flush()

    # Mark token used
    invite.used           = True
    invite.participant_id = participant.id
    await db.commit()
    await db.refresh(participant)

    # Set HttpOnly cookie
    response.set_cookie(
        key=SESSION_COOKIE,
        value=new_session,
        path="/",
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )

    # Notify host
    event = await get_or_create_event(db)
    await manager.emit("participant_joined", {
        "participant": {
            "id":             participant.id,
            "display_number": participant.display_number,
            "joined_at":      participant.joined_at.isoformat(),
            "uploaded":       False,
            "voting_complete":False,
            "is_connected":   False,
        }
    }, audience="host")
    await broadcast_stats(db)

    # Rotate QR on host
    base_url = str(request.base_url).rstrip("/")
    await rotate_token(db, base_url)

    logger.info("Participant #%d joined", participant.display_number)

    return {
        "participant_id": participant.id,
        "display_number": participant.display_number,
        "phase":          event.phase.value,
        "already_joined": False,
    }



@router.get("/me")
async def get_me(
    participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db),
):
    event = await get_or_create_event(db)
    resp = {
        "participant_id": participant.id,
        "display_number": participant.display_number,
        "name":           participant.name,
        "topic":          participant.topic,
        "phase":          event.phase.value,
        "has_upload":     participant.upload is not None,
    }
    if event.phase == Phase.RESULTS:
        resp["leaderboard"] = await get_leaderboard(db)
    return resp
