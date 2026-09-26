import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, String, Float,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Phase(str, enum.Enum):
    WAITING = "WAITING"
    UPLOAD  = "UPLOAD"
    VOTING  = "VOTING"
    RESULTS = "RESULTS"


class InviteMode(str, enum.Enum):
    INVITE_ONLY = "INVITE_ONLY"
    OPEN = "OPEN"


class Event(Base):
    __tablename__ = "events"

    id:         Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    phase:      Mapped[Phase]    = mapped_column(Enum(Phase), default=Phase.WAITING, nullable=False)
    invite_mode: Mapped[InviteMode] = mapped_column(Enum(InviteMode), default=InviteMode.OPEN, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id:             Mapped[int]           = mapped_column(Integer, primary_key=True, index=True)
    token:          Mapped[str]           = mapped_column(String(64), unique=True, nullable=False, index=True)
    used:           Mapped[bool]          = mapped_column(Boolean, default=False, nullable=False)
    created_at:     Mapped[datetime]      = mapped_column(DateTime, server_default=func.now())
    participant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("participants.id"), nullable=True)

    participant: Mapped[Optional["Participant"]] = relationship("Participant", back_populates="token")


class Participant(Base):
    __tablename__ = "participants"

    id:             Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    session_id:     Mapped[str]      = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_number: Mapped[int]      = mapped_column(Integer, nullable=False)
    joined_at:      Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_connected:   Mapped[bool]     = mapped_column(Boolean, default=False, nullable=False)
    name:           Mapped[str]      = mapped_column(String(128), nullable=False, default="")
    topic:          Mapped[str]      = mapped_column(String(256), nullable=False, default="")

    token:   Mapped[Optional["InviteToken"]] = relationship("InviteToken", back_populates="participant")
    upload:  Mapped[Optional["Upload"]]      = relationship("Upload", back_populates="participant", uselist=False)
    votes:   Mapped[list["Vote"]]            = relationship("Vote", back_populates="voter", foreign_keys="Vote.voter_id")


class Upload(Base):
    __tablename__ = "uploads"

    id:             Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    participant_id: Mapped[int]      = mapped_column(ForeignKey("participants.id"), unique=True, nullable=False)
    filename:       Mapped[str]      = mapped_column(String(128), nullable=False)
    original_ext:   Mapped[str]      = mapped_column(String(8), nullable=False)
    uploaded_at:    Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    participant: Mapped["Participant"] = relationship("Participant", back_populates="upload")
    votes:       Mapped[list["Vote"]]  = relationship("Vote", back_populates="upload")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("voter_id", "upload_id", name="uq_voter_upload"),)

    id:        Mapped[int]      = mapped_column(Integer, primary_key=True, index=True)
    voter_id:  Mapped[int]      = mapped_column(ForeignKey("participants.id"), nullable=False)
    upload_id: Mapped[int]      = mapped_column(ForeignKey("uploads.id"), nullable=False)
    score:     Mapped[float]      = mapped_column(Float, nullable=False)
    voted_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    voter:  Mapped["Participant"] = relationship("Participant", back_populates="votes", foreign_keys=[voter_id])
    upload: Mapped["Upload"]      = relationship("Upload", back_populates="votes")
