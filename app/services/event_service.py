"""
Event state management service.

Handles phase transitions, stats aggregation, and token/QR lifecycle.
"""
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Event, InviteToken, Participant, Phase, Upload, Vote
from app.services.qr_service import generate_token
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)



async def get_or_create_event(db: AsyncSession) -> Event:
    result = await db.execute(select(Event).limit(1))
    event = result.scalar_one_or_none()
    if event is None:
        event = Event(phase=Phase.WAITING)
        db.add(event)
        await db.commit()
        await db.refresh(event)
    return event


async def set_phase(db: AsyncSession, phase: Phase) -> Event:
    event = await get_or_create_event(db)
    event.phase = phase
    await db.commit()
    await db.refresh(event)
    await manager.emit("phase_changed", {"phase": phase.value}, audience="all")
    logger.info("Phase changed → %s", phase.value)
    return event



async def get_or_create_active_token(db: AsyncSession) -> InviteToken:
    """Return the current unused token, creating one if needed."""
    result = await db.execute(
        select(InviteToken).where(InviteToken.used == False).order_by(InviteToken.id).limit(1)
    )
    token = result.scalar_one_or_none()
    if token is None:
        token = InviteToken(token=generate_token())
        db.add(token)
        await db.commit()
        await db.refresh(token)
    return token


async def rotate_token(db: AsyncSession, base_url: str) -> InviteToken:
    """Mark old token used (if any) and generate a fresh one, then broadcast."""
    new_token = InviteToken(token=generate_token())
    db.add(new_token)
    await db.commit()
    await db.refresh(new_token)

    from app.services.qr_service import make_qr_base64
    join_url = f"{base_url}/join/{new_token.token}"
    qr_b64   = make_qr_base64(join_url)
    await manager.emit("qr_updated", {"token": new_token.token, "qr": qr_b64, "join_url": join_url}, audience="host")
    return new_token



async def get_stats(db: AsyncSession) -> dict[str, Any]:
    total_participants = (await db.execute(select(func.count()).select_from(Participant))).scalar() or 0
    total_uploads      = (await db.execute(select(func.count()).select_from(Upload))).scalar() or 0

    # Participants who have voted on ALL images except their own
    # (i.e., vote count == total_uploads - 1 if they have an upload, else total_uploads)
    # Simplified: participants whose vote count matches total images they should rate
    participants_result = await db.execute(select(Participant))
    participants        = participants_result.scalars().all()

    voting_complete = 0
    for p in participants:
        # Images they should vote on = all uploads except their own
        own_upload_res   = await db.execute(select(Upload).where(Upload.participant_id == p.id))
        own_upload       = own_upload_res.scalar_one_or_none()
        images_to_vote   = total_uploads - (1 if own_upload else 0)
        if images_to_vote <= 0:
            voting_complete += 1
            continue
        vote_count_res   = await db.execute(
            select(func.count()).select_from(Vote).where(Vote.voter_id == p.id)
        )
        vote_count = vote_count_res.scalar() or 0
        if vote_count >= images_to_vote:
            voting_complete += 1

    connected = sum(1 for p in participants if p.is_connected)

    return {
        "total_participants": total_participants,
        "total_uploads":      total_uploads,
        "remaining_uploads":  total_participants - total_uploads,
        "voting_complete":    voting_complete,
        "connected":          connected,
    }


async def get_leaderboard(db: AsyncSession) -> list[dict]:
    """Return sorted leaderboard of uploads with avg score and vote count."""
    from sqlalchemy.orm import selectinload
    uploads_res = await db.execute(select(Upload).options(selectinload(Upload.participant)))
    uploads     = uploads_res.scalars().all()

    rows = []
    for upload in uploads:
        votes_res  = await db.execute(select(Vote).where(Vote.upload_id == upload.id))
        votes      = votes_res.scalars().all()
        if votes:
            avg = sum(v.score for v in votes) / len(votes)
        else:
            avg = 0.0
        rows.append({
            "upload_id":    upload.id,
            "filename":     upload.filename,
            "participant":  upload.participant.display_number if upload.participant else upload.participant_id,
            "name":         upload.participant.name if upload.participant else "Unknown",
            "topic":        upload.participant.topic if upload.participant else "Unknown",
            "avg_score":    round(avg, 2),
            "vote_count":   len(votes),
        })

    rows.sort(key=lambda r: (r["avg_score"], r["vote_count"]), reverse=True)
    for i, r in enumerate(rows):
        if i > 0 and r["avg_score"] == rows[i - 1]["avg_score"] and r["vote_count"] == rows[i - 1]["vote_count"]:
            r["rank"] = rows[i - 1]["rank"]
        else:
            r["rank"] = i + 1
    return rows


async def broadcast_stats(db: AsyncSession) -> None:
    stats = await get_stats(db)
    await manager.emit("stats_update", {"stats": stats}, audience="host")
