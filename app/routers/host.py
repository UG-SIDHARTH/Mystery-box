"""
Host router: admin actions, QR, export.
"""
import csv
import io
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import InviteToken, Participant, Phase, Vote, Upload, InviteMode
from app.services.event_service import (
    broadcast_stats,
    get_leaderboard,
    get_or_create_active_token,
    get_or_create_event,
    get_stats,
    rotate_token,
    set_phase,
    set_invite_mode,
)
from app.services.image_service import UPLOADS_DIR
from app.services.qr_service import make_qr_base64
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)

def verify_host(request: Request):
    if request.cookies.get("host_token") != "authenticated":
        raise HTTPException(status_code=401, detail="Not authenticated")

router = APIRouter(prefix="/host", dependencies=[Depends(verify_host)])



@router.get("/qr")
async def get_current_qr(request: Request, db: AsyncSession = Depends(get_db)):
    token = await get_or_create_active_token(db)
    base_url = str(request.base_url).rstrip("/")
    if base_url.startswith("http://") and "localhost" not in base_url and "127.0.0.1" not in base_url:
        base_url = base_url.replace("http://", "https://", 1)
    join_url = f"{base_url}/join/{token.token}"
    qr_b64   = make_qr_base64(join_url)
    return {"token": token.token, "qr": qr_b64, "join_url": join_url}



@router.post("/qr/next")
async def generate_next_qr(request: Request, db: AsyncSession = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    if base_url.startswith("http://") and "localhost" not in base_url and "127.0.0.1" not in base_url:
        base_url = base_url.replace("http://", "https://", 1)
    token    = await rotate_token(db, base_url)
    join_url = f"{base_url}/join/{token.token}"
    qr_b64   = make_qr_base64(join_url)
    return {"token": token.token, "qr": qr_b64, "join_url": join_url}



@router.get("/stats")
async def host_stats(db: AsyncSession = Depends(get_db)):
    return await get_stats(db)



@router.get("/participants")
async def list_participants(db: AsyncSession = Depends(get_db)):
    from sqlalchemy.orm import selectinload
    result       = await db.execute(
        select(Participant)
        .options(selectinload(Participant.upload), selectinload(Participant.votes))
        .order_by(Participant.display_number)
    )
    participants = result.scalars().all()

    rows = []
    for p in participants:
        # Check votes
        from sqlalchemy import func
        upload_count = (await db.execute(select(func.count()).select_from(Upload))).scalar() or 0
        own_upload   = p.upload is not None
        images_to_vote = upload_count - (1 if own_upload else 0)
        vote_count   = len(p.votes)

        rows.append({
            "id":             p.id,
            "display_number": p.display_number,
            "name":           p.name,
            "topic":          p.topic,
            "joined_at":      p.joined_at.isoformat(),
            "uploaded":       own_upload,
            "upload_filename": p.upload.filename if p.upload else None,
            "vote_count":     vote_count,
            "voting_complete":images_to_vote > 0 and vote_count >= images_to_vote,
            "is_connected":   p.is_connected,
        })
    return rows



class PhasePayload(BaseModel):
    phase: Phase


@router.post("/phase")
async def change_phase(payload: PhasePayload, db: AsyncSession = Depends(get_db)):
    event = await set_phase(db, payload.phase)
    await broadcast_stats(db)
    return {"phase": event.phase.value}


class InviteModePayload(BaseModel):
    invite_mode: InviteMode


@router.post("/invite_mode")
async def change_invite_mode(payload: InviteModePayload, db: AsyncSession = Depends(get_db)):
    event = await set_invite_mode(db, payload.invite_mode)
    return {"invite_mode": event.invite_mode.value}



@router.post("/reveal")
async def reveal_results(db: AsyncSession = Depends(get_db)):
    await set_phase(db, Phase.RESULTS)
    leaderboard = await get_leaderboard(db)
    await manager.emit("results_revealed", {"leaderboard": leaderboard}, audience="all")
    return {"leaderboard": leaderboard}



@router.get("/leaderboard")
async def get_board(db: AsyncSession = Depends(get_db)):
    return await get_leaderboard(db)



@router.delete("/participant/{participant_id}")
async def delete_participant(participant_id: int, db: AsyncSession = Depends(get_db)):
    result      = await db.execute(select(Participant).where(Participant.id == participant_id))
    participant = result.scalar_one_or_none()
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found.")

    # Remove votes cast by this participant
    votes = await db.execute(select(Vote).where(Vote.voter_id == participant_id))
    for v in votes.scalars():
        await db.delete(v)

    # Remove upload
    if participant.upload:
        img_path = UPLOADS_DIR / participant.upload.filename
        if img_path.exists():
            img_path.unlink()
        await db.delete(participant.upload)

    # Unlink token
    token_res = await db.execute(select(InviteToken).where(InviteToken.participant_id == participant_id))
    token     = token_res.scalar_one_or_none()
    if token:
        token.participant_id = None
        token.used           = False

    await db.delete(participant)
    await db.commit()

    await manager.emit("participant_removed", {"participant_id": participant_id}, audience="all")
    await broadcast_stats(db)
    return {"message": "Participant removed."}



@router.post("/restart_voting")
async def restart_voting(db: AsyncSession = Depends(get_db)):
    # Delete all votes
    votes = await db.execute(select(Vote))
    for v in votes.scalars():
        await db.delete(v)
    
    event = await get_or_create_event(db)
    event.phase = Phase.VOTING
    await db.commit()
    
    await manager.emit("phase_changed", {"phase": event.phase.value}, audience="all")
    await broadcast_stats(db)
    
    leaderboard = await get_leaderboard(db)
    await manager.emit("leaderboard_update", {"leaderboard": leaderboard}, audience="host")
    
    logger.info("Voting restarted by host")
    return {"message": "Voting restarted."}



@router.post("/reset")
async def reset_event(request: Request, db: AsyncSession = Depends(get_db)):
    # Delete all votes, uploads, participants, tokens
    for model in (Vote, Upload, Participant, InviteToken):
        rows = await db.execute(select(model))
        for row in rows.scalars():
            await db.delete(row)

    # Clear uploads directory
    if UPLOADS_DIR.exists():
        for f in UPLOADS_DIR.iterdir():
            if f.is_file():
                f.unlink()

    # Reset event phase
    event        = await get_or_create_event(db)
    event.phase  = Phase.WAITING
    await db.commit()

    base_url = str(request.base_url).rstrip("/")
    token    = await get_or_create_active_token(db)
    join_url = f"{base_url}/join/{token.token}"
    from app.services.qr_service import make_qr_base64
    qr_b64   = make_qr_base64(join_url)

    await manager.emit("event_reset", {"phase": "WAITING"}, audience="all")
    await manager.emit("qr_updated", {"token": token.token, "qr": qr_b64, "join_url": join_url}, audience="host")
    logger.info("Event reset by host")
    return {"message": "Event reset."}



@router.get("/export/csv")
async def export_csv(db: AsyncSession = Depends(get_db)):
    votes_res = await db.execute(select(Vote))
    votes     = votes_res.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["vote_id", "voter_participant_id", "upload_id", "score", "voted_at"])
    for v in votes:
        writer.writerow([v.id, v.voter_id, v.upload_id, v.score, v.voted_at.isoformat()])

    buf.seek(0)
    return StreamingResponse(
        io.BytesIO(buf.read().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=votes.csv"},
    )



@router.get("/export/zip")
async def export_zip(db: AsyncSession = Depends(get_db)):
    uploads_res = await db.execute(select(Upload))
    uploads     = uploads_res.scalars().all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload in uploads:
            img_path = UPLOADS_DIR / upload.filename
            if img_path.exists():
                zf.write(img_path, arcname=f"participant_{upload.participant_id}{upload.original_ext}")

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=uploads.zip"},
    )
