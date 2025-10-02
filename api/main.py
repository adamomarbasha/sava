from fastapi import FastAPI, HTTPException, Depends, status, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import timedelta
from typing import Optional, List, Dict, Any
import logging
import json
import os
import requests
from starlette.responses import StreamingResponse

from .db import get_db, init_db
from .models import User, Bookmark
from .ingestors import add_bookmark, refresh_bookmark
from .email_validation import validate_email_comprehensive
from .transcript_service import get_youtube_transcript, get_available_transcript_languages
from .comment_service import youtube_comment_service
from .rate_limiter import rate_limiter
from .auth import (
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

app = FastAPI(
    title="Sava Bookmark API", 
    version="2.0.0",
    description="A powerful bookmark management API for saving and organizing links from various social media platforms",
    docs_url="/docs",  
    redoc_url="/redoc",  
    openapi_url="/openapi.json"  
)

os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", 
        "http://127.0.0.1:3000", 
        "http://localhost:3001", 
        "http://127.0.0.1:3001", 
        "http://localhost:3002", 
        "http://127.0.0.1:3002",
        "http://localhost:3003", 
        "http://127.0.0.1:3003"
    ],
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

class BookmarkUpdate(BaseModel):
    note: str

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
async def login(user: UserLogin):
    try:
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
            data={"sub": existing_user["email"]}, 
            expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during login"
        )

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
            if b.note and b.note.strip():
                bookmark.note = b.note
                result["note"] = b.note
            
            placeholder_titles = {"", "untitled", "title", "new bookmark", "bookmark", "n/a"}
            provided_title = (b.title or "").strip()
            if provided_title and len(provided_title) >= 3 and provided_title.lower() not in placeholder_titles:
                bookmark.title = provided_title
                result["title"] = provided_title
                logging.info("Title overridden by client input")
            else:
                logging.info("Keeping extracted title; client title omitted or placeholder")
            
            if b.note or (provided_title and len(provided_title) >= 3 and provided_title.lower() not in placeholder_titles):
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

@app.put("/api/bookmarks/{bookmark_id}")
async def update_bookmark(
    bookmark_id: int,
    bookmark_update: BookmarkUpdate,
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
        
        bookmark.note = bookmark_update.note
        
        db.commit()
        db.refresh(bookmark)
        
        return {
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
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/bookmarks/{bookmark_id}/refresh")
async def refresh_bookmark_endpoint(
    bookmark_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    try:
        updated = await refresh_bookmark(bookmark_id, current_user["id"], db)
        return updated
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error refreshing bookmark {bookmark_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh bookmark")

@app.get("/test/instagram-thumbnail")
async def test_instagram_thumbnail(url: str):
    return {
        "url": url,
        "message": "Instagram URL received",
        "success": True,
        "note": "Use POST /bookmarks to actually extract metadata"
    }

@app.get("/api/thumbnail")
async def proxy_thumbnail(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', 'application/octet-stream')
        
        return StreamingResponse(response.iter_content(chunk_size=8192), media_type=content_type)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch image: {e}")


class TranscriptRequest(BaseModel):
    video_url_or_id: str
    languages: Optional[List[str]] = None
    preserve_formatting: Optional[bool] = False

class TranscriptResponse(BaseModel):
    success: bool
    transcript: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    video_id: Optional[str] = None
    language: Optional[str] = None

@app.post("/api/transcript", response_model=TranscriptResponse)
async def get_transcript(request: TranscriptRequest):
    try:
        logger.info(f"Fetching transcript for: {request.video_url_or_id}")
        
        result = get_youtube_transcript(
            video_input=request.video_url_or_id,
            languages=request.languages,
            preserve_formatting=request.preserve_formatting
        )
        
        if result["success"]:
            logger.info(f"Successfully fetched transcript with {len(result['transcript'])} entries")
        else:
            logger.warning(f"Failed to fetch transcript: {result['error']}")
        
        return TranscriptResponse(**result)
        
    except Exception as e:
        logger.error(f"Unexpected error in transcript endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/transcript/languages")
async def get_transcript_languages(video_url_or_id: str):
    try:
        logger.info(f"Getting available languages for: {video_url_or_id}")
        
        result = get_available_transcript_languages(video_url_or_id)
        
        if result["success"]:
            logger.info(f"Found {len(result['languages'])} available languages")
        else:
            logger.warning(f"Failed to get languages: {result['error']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in languages endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/transcript/status")
async def get_transcript_status():
    try:
        status = rate_limiter.get_status()
        return {
            "rate_limiting": status,
            "message": "Rate limiting status for YouTube transcript API"
        }
    except Exception as e:
        logger.error(f"Error getting transcript status: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/transcript/{video_id}")
async def get_transcript_by_id(
    video_id: str,
    languages: Optional[str] = Query(None, description="Comma-separated list of language codes (e.g., 'en,es,fr')"),
    preserve_formatting: bool = Query(False, description="Whether to preserve original formatting")
):
    try:
        lang_list = None
        if languages:
            lang_list = [lang.strip() for lang in languages.split(',')]
        
        logger.info(f"Fetching transcript for video ID: {video_id}")
        
        result = get_youtube_transcript(
            video_input=video_id,
            languages=lang_list,
            preserve_formatting=preserve_formatting
        )
        
        if result["success"]:
            logger.info(f"Successfully fetched transcript with {len(result['transcript'])} entries")
        else:
            logger.warning(f"Failed to fetch transcript: {result['error']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in transcript GET endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )


class CommentRequest(BaseModel):
    video_url_or_id: str
    limit: Optional[int] = 50
    sort_by: Optional[str] = 'popular' 

class CommentResponse(BaseModel):
    success: bool
    comments: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    video_id: Optional[str] = None
    total_fetched: Optional[int] = None

@app.post("/api/comments", response_model=CommentResponse)
async def get_youtube_comments(request: CommentRequest):
    try:
        logger.info(f"Fetching comments for: {request.video_url_or_id}")
        
        result = youtube_comment_service.get_comments(
            video_input=request.video_url_or_id,
            limit=request.limit,
            sort_by=request.sort_by
        )
        
        if result["success"]:
            logger.info(f"Successfully fetched {result['total_fetched']} comments")
        else:
            logger.warning(f"Failed to fetch comments: {result['error']}")
        
        return CommentResponse(**result)
        
    except Exception as e:
        logger.error(f"Unexpected error in comments endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/api/comments/{bookmark_id}")
async def get_bookmark_comments(
    bookmark_id: int,
    limit: Optional[int] = Query(50, description="Maximum number of comments to return"),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"Fetching comments for bookmark: {bookmark_id}")
        
        result = youtube_comment_service.get_comments_from_db(
            bookmark_id=bookmark_id,
            db=db,
            limit=limit
        )
        
        if result["success"]:
            logger.info(f"Successfully retrieved {result['total_count']} comments from database")
        else:
            logger.warning(f"Failed to retrieve comments: {result['error']}")
        
        return result
        
    except Exception as e:
        logger.error(f"Unexpected error in bookmark comments endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

@app.post("/api/comments/save/{bookmark_id}")
async def save_comments_to_bookmark(
    bookmark_id: int,
    video_url_or_id: str,
    limit: Optional[int] = Query(50, description="Maximum number of comments to fetch"),
    sort_by: Optional[str] = Query('popular', description="Sort order: 'popular' or 'recent'"),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"Saving comments for bookmark {bookmark_id}")
        
        fetch_result = youtube_comment_service.get_comments(
            video_input=video_url_or_id,
            limit=limit,
            sort_by=sort_by
        )
        
        if not fetch_result["success"]:
            return {
                "success": False,
                "saved_count": 0,
                "error": f"Failed to fetch comments: {fetch_result['error']}"
            }
        
        save_result = youtube_comment_service.save_comments_to_db(
            bookmark_id=bookmark_id,
            comments=fetch_result["comments"],
            db=db
        )
        
        if save_result["success"]:
            logger.info(f"Successfully saved {save_result['saved_count']} comments for bookmark {bookmark_id}")
        else:
            logger.warning(f"Failed to save comments: {save_result['error']}")
        
        return save_result
        
    except Exception as e:
        logger.error(f"Unexpected error in save comments endpoint: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Internal server error: {str(e)}"
        )

