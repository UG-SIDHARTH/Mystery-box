"""
Upload router: POST /upload, GET /images.
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import Participant, Phase, Upload
from app.routers.auth import get_current_participant
from app.services.event_service import broadcast_stats, get_or_create_event
from app.services.image_service import validate_and_save
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db),
):
    # Phase check
    event = await get_or_create_event(db)
    if event.phase != Phase.UPLOAD:
        raise HTTPException(status_code=400, detail="Uploads are not open right now.")

    # Duplicate upload check
    if participant.upload is not None:
        raise HTTPException(status_code=400, detail="You have already uploaded an image.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        stored_name, ext = validate_and_save(raw, file.filename or "image.jpg")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    upload = Upload(participant_id=participant.id, filename=stored_name, original_ext=ext)
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    logger.info("Participant #%d uploaded %s", participant.display_number, stored_name)

    # Notify host
    await manager.emit("upload_completed", {
        "participant_id": participant.id,
        "display_number": participant.display_number,
        "filename":       stored_name,
    }, audience="host")
    await broadcast_stats(db)

    return {"filename": stored_name, "message": "Upload successful."}


@router.delete("/upload")
async def delete_upload(
    participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db),
):
    event = await get_or_create_event(db)
    if event.phase != Phase.UPLOAD:
        raise HTTPException(status_code=400, detail="Uploads are not open right now.")

    if participant.upload is None:
        raise HTTPException(status_code=400, detail="You have not uploaded an image.")

    from app.services.image_service import UPLOADS_DIR
    img_path = UPLOADS_DIR / participant.upload.filename
    if img_path.exists():
        img_path.unlink()

    await db.delete(participant.upload)
    await db.commit()

    logger.info("Participant #%d removed their upload", participant.display_number)

    await manager.emit("upload_completed", {
        "participant_id": participant.id,
        "display_number": participant.display_number,
    }, audience="host")
    await broadcast_stats(db)

    return {"message": "Upload removed."}

@router.get("/images")
async def get_images_to_vote(
    participant: Participant = Depends(get_current_participant),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all uploads except the participant's own, in a randomised order
    seeded by the participant's ID (consistent across requests).
    """
    import random
    event = await get_or_create_event(db)
    if event.phase not in (Phase.VOTING, Phase.RESULTS):
        raise HTTPException(status_code=400, detail="Voting has not started yet.")

    from sqlalchemy.orm import selectinload
    # All uploads except participant's own
    result = await db.execute(
        select(Upload)
        .options(selectinload(Upload.participant))
        .where(Upload.participant_id != participant.id)
    )
    uploads = result.scalars().all()

    rng = random.Random(participant.id)
    rng.shuffle(uploads)

    # Which ones has this participant already voted on?
    voted_res = await db.execute(
        select(Upload.id)
        .join(Upload.votes)
        .where(Upload.votes.any(voter_id=participant.id))
    )
    # simpler approach:
    from app.models.models import Vote
    votes_res = await db.execute(
        select(Vote.upload_id).where(Vote.voter_id == participant.id)
    )
    voted_ids = {r for r, in votes_res.fetchall()}

    return [
        {
            "upload_id": u.id,
            "filename":  u.filename,
            "url":       f"/uploads/{u.filename}",
            "topic":     u.participant.topic if u.participant else "",
            "voted":     u.id in voted_ids,
        }
        for u in uploads
    ]
