# api/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlalchemy import text
from .db import engine, init_db

app = FastAPI()

# allow Next.js dev server calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BookmarkIn(BaseModel):
    url: HttpUrl
    title: str | None = None
    note: str | None = None

@app.on_event("startup")
def on_startup():
    init_db()

def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u: return "tiktok"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u: return "instagram"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    return "web"

@app.get("/")
def health():
    return {"message": "Sava API is running 🚀"}

@app.post("/bookmarks")
def create_bookmark(b: BookmarkIn):
    platform = detect_platform(str(b.url))
    try:
        with engine.begin() as conn:
            # Insert the bookmark
            result = conn.execute(
                text("""
                    INSERT INTO bookmarks (url, title, note, platform)
                    VALUES (:url, :title, :note, :platform)
                """),
                {"url": str(b.url), "title": b.title, "note": b.note, "platform": platform}
            )
            # Get the inserted bookmark
            bookmark_id = result.lastrowid
            row = conn.execute(
                text("SELECT id, url, title, note, platform, created_at FROM bookmarks WHERE id = :id"),
                {"id": bookmark_id}
            ).mappings().one()
            return dict(row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bookmarks")
def list_bookmarks(query: str | None = None, platform: str | None = None):
    sql = "SELECT id, url, title, note, platform, created_at FROM bookmarks WHERE 1=1"
    params: dict = {}
    if query:
        sql += " AND (url LIKE :q OR COALESCE(title,'') LIKE :q OR COALESCE(note,'') LIKE :q)"
        params["q"] = f"%{query}%"
    if platform:
        sql += " AND platform = :p"
        params["p"] = platform
    sql += " ORDER BY created_at DESC LIMIT 200"
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
