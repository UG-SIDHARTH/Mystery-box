# MEMECEPTION App

A lightweight, real-time event voting application built with **FastAPI + SQLite + Vanilla JS + Tailwind CSS**.

## Quick Start

```bash
cd event-voting
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open:
- **Participant**: http://localhost:8000
- **Host Dashboard**: http://localhost:8000/host

## Features

- 🔗 Single-use QR code invite system
- 📸 Image upload (PNG, JPG, WEBP, max 10MB, auto-compressed)
- ⭐ 1–5 star rating for each poster
- 🏆 Live leaderboard with medals
- 🔴 Real-time WebSocket updates - no polling
- 🌑 Dark mode responsive UI
- 📊 CSV export of votes
- 📦 ZIP export of all uploaded images
- 🔄 Full event reset

## Event Flow

| Phase | Participants See | Host Can Do |
|-------|-----------------|-------------|
| WAITING | Waiting screen | Generate QR codes, let people join |
| UPLOAD | Upload form | Monitor uploads, switch to voting when ready |
| VOTING | Star rating cards | See live vote stats |
| RESULTS | Full leaderboard | See final rankings |

## Switching to PostgreSQL

Set the `DATABASE_URL` environment variable before starting:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@localhost/event_voting uvicorn app.main:app
```

## Project Structure

```
event-voting/
├── app/
│   ├── main.py               # FastAPI app, static mounts
│   ├── database.py           # SQLAlchemy async engine
│   ├── models/models.py      # ORM models
│   ├── routers/
│   │   ├── auth.py           # /join/{token}, /me
│   │   ├── upload.py         # /upload, /images
│   │   ├── vote.py           # /vote
│   │   ├── host.py           # /host/* admin endpoints
│   │   └── websocket.py      # /ws/participant, /ws/host
│   ├── services/
│   │   ├── ws_manager.py     # WebSocket connection manager
│   │   ├── qr_service.py     # QR code generation
│   │   ├── image_service.py  # Pillow validation + compression
│   │   └── event_service.py  # Phase + stats management
│   └── static/
│       ├── participant.html  # Participant SPA
│       └── host.html         # Host dashboard SPA
├── requirements.txt
└── README.md
```
