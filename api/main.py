from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import timedelta
from typing import Optional, List
import logging
import json

from db import get_db, init_db
from models import User, Bookmark
from ingestors import add_bookmark
from email_validation import validate_email_comprehensive
from auth import (
    authenticate_user, 
    create_access_token, 
    get_current_user, 
    get_password_hash,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    get_user_by_email,
    verify_password,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sava Bookmark API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001", "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BookmarkIn(BaseModel):
    url: HttpUrl
    title: str | None = None
    note: str | None = None

class YouTubeBookmarkIn(BaseModel):
    url: HttpUrl

class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Sava API started successfully")

def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u: return "tiktok"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u: return "instagram"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    if "linkedin.com" in u: return "linkedin"
    if "reddit.com" in u: return "reddit"
    if "pinterest.com" in u or "pin.it" in u: return "pinterest"
    if "snapchat.com" in u: return "snapchat"
    if "facebook.com" in u or "fb.com" in u: return "facebook"
    return "other"

@app.get("/")
def health():
    return {"message": "Sava API is running 🚀", "version": "2.0.0"}

@app.post("/auth/register", response_model=dict)
def register(user: UserRegister, db: Session = Depends(get_db)):
    normalized_email = user.email.strip().lower()
    
    is_valid, error_message = validate_email_comprehensive(user.email)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )
    
    existing = db.query(User).filter(User.email == normalized_email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    hashed_password = get_password_hash(user.password)
    new_user = User(email=normalized_email, password_hash=hashed_password)
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {"id": new_user.id, "email": new_user.email, "message": "User created successfully"}

@app.post("/auth/login", response_model=Token)
def login(user: UserLogin):
    existing_user = get_user_by_email(user.email)
    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not found"
        )
    if not verify_password(user.password, existing_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": existing_user["email"]}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "created_at": current_user["created_at"]
    }

@app.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [{"id": u.id, "email": u.email, "created_at": u.created_at} for u in users]

@app.post("/api/bookmarks/youtube")
async def create_youtube_bookmark(
    bookmark_data: YouTubeBookmarkIn, 
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        url = str(bookmark_data.url)
        
        if not ("youtube.com" in url.lower() or "youtu.be" in url.lower()):
            raise HTTPException(
                status_code=422,
                detail="URL must be a valid YouTube URL"
            )
        
        result = await add_bookmark(url, current_user["id"], db)
        
        logger.info(f"Successfully created YouTube bookmark: {result.get('title', 'Unknown')}")
        return result
        
    except ValueError as e:
        logger.error(f"Validation error creating YouTube bookmark: {e}")
        error_msg = str(e)
        if "already" in error_msg.lower() and "bookmarked" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        else:
            raise HTTPException(status_code=422, detail=error_msg)
    except Exception as e:
        logger.error(f"Unexpected error creating YouTube bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/bookmarks")
async def create_bookmark(
    b: BookmarkIn, 
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        url = str(b.url)
        result = await add_bookmark(url, current_user["id"], db)
        
        bookmark = db.query(Bookmark).filter(Bookmark.id == result["id"]).first()
        if bookmark:
            if b.title:
                bookmark.title = b.title
                result["title"] = b.title
            if b.note:
                bookmark.note = b.note
                result["note"] = b.note
            if b.title or b.note:
                db.commit()
        
        return result
        
    except ValueError as e:
        error_msg = str(e)
        if "already" in error_msg.lower() and "bookmarked" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        else:
            raise HTTPException(status_code=422, detail=error_msg)
    except Exception as e:
        logger.error(f"Error creating bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/bookmarks")
def list_bookmarks(
    platform: Optional[str] = Query(None, description="Filter by platform (youtube, tiktok, instagram, twitter, linkedin, reddit, pinterest, snapchat, facebook, other)"),
    q: Optional[str] = Query(None, description="Search query for title, author, or description"),
    limit: int = Query(100, ge=1, le=500, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        query = db.query(Bookmark).filter(Bookmark.user_id == current_user["id"])
        
        if platform:
            platform_lower = platform.lower()
            if platform_lower not in ['youtube', 'tiktok', 'instagram', 'twitter', 'linkedin', 'reddit', 'pinterest', 'snapchat', 'facebook', 'other']:
                raise HTTPException(status_code=422, detail="Invalid platform")
            query = query.filter(Bookmark.platform == platform_lower)
        
        if q:
            search_term = f"%{q}%"
            query = query.filter(
                (Bookmark.title.ilike(search_term)) |
                (Bookmark.author.ilike(search_term)) |
                (Bookmark.description.ilike(search_term)) |
                (Bookmark.note.ilike(search_term))
            )
        
        bookmarks = query.order_by(Bookmark.created_at.desc()).offset(offset).limit(limit).all()
        
        results = []
        for bookmark in bookmarks:
            response = {
                "id": bookmark.id,
                "platform": bookmark.platform,
                "url": bookmark.url,
                "title": bookmark.title,
                "author": bookmark.author,
                "thumbnail_url": bookmark.thumbnail_url,
                "note": bookmark.note,
                "published_at": bookmark.published_at.isoformat() if bookmark.published_at else None,
                "created_at": bookmark.created_at.isoformat(),
                "meta": {}
            }
            
            if bookmark.platform == "youtube" and bookmark.youtube_details:
                yt = bookmark.youtube_details[0]
                response["meta"] = {
                    "video_id": yt.video_id,
                    "channel_id": yt.channel_id,
                    "duration_seconds": yt.duration_seconds,
                    "view_count": yt.view_count,
                    "like_count": yt.like_count,
                    "tags": json.loads(yt.tags) if yt.tags else []
                }
            
            results.append(response)
        
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing bookmarks: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/bookmarks")
def list_bookmarks_legacy(
    query: str | None = None, 
    platform: str | None = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return list_bookmarks(platform=platform, q=query, current_user=current_user, db=db)

@app.delete("/api/bookmarks/{bookmark_id}")
def delete_bookmark(
    bookmark_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        bookmark = db.query(Bookmark).filter(
            Bookmark.id == bookmark_id,
            Bookmark.user_id == current_user["id"]
        ).first()
        
        if not bookmark:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        
        db.delete(bookmark)
        db.commit()
        
        logger.info(f"Deleted bookmark {bookmark_id}")
        return {"message": "Bookmark deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/test/instagram-thumbnail")
async def test_instagram_thumbnail(url: str):
    return {
        "url": url,
        "message": "Instagram URL received",
        "success": True,
        "note": "Use POST /bookmarks to actually extract metadata"
    }
