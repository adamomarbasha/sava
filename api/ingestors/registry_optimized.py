import logging
import asyncio
import json
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from concurrent.futures import ThreadPoolExecutor
from .base import BaseIngestor
from .youtube_optimized import YouTubeOptimizedIngestor
from .tiktok_optimized import TikTokOptimizedIngestor
from .social import (
    InstagramIngestor, 
    TwitterIngestor,
    LinkedInIngestor,
    RedditIngestor,
    PinterestIngestor,
    SnapchatIngestor,
    FacebookIngestor
)
from models import Bookmark, YouTubeDetails, User
from db import SessionLocal

logger = logging.getLogger(__name__)

OPTIMIZED_INGESTORS = [
    YouTubeOptimizedIngestor(),
    TikTokOptimizedIngestor(),
    InstagramIngestor(), 
    TwitterIngestor(),
    LinkedInIngestor(),
    RedditIngestor(),
    PinterestIngestor(),
    SnapchatIngestor(),
    FacebookIngestor(),
]

PROCESSING_POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="video_processor")

def get_ingestor(url: str) -> Optional[BaseIngestor]:
    for ingestor in OPTIMIZED_INGESTORS:
        if ingestor.can_handle(url):
            return ingestor
    return None

async def add_bookmark_ultra_fast(url: str, user_id: int, db: Session = None) -> Dict[str, Any]:
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True
    
    try:
        start_time = asyncio.get_event_loop().time()
        
        ingestor = get_ingestor(url)
        
        if not ingestor:
            logger.info(f"No optimized ingestor found for URL: {url}, creating basic bookmark")
            return _create_basic_bookmark(url, user_id, db)
        
        logger.info(f"🚀 Using OPTIMIZED {ingestor.platform} ingestor for URL: {url}")
        
        existing = db.query(Bookmark).filter(Bookmark.url == url, Bookmark.user_id == user_id).first()
        
        if existing:
            logger.info(f"Bookmark already exists for URL: {url}")
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        
        result = await _create_new_bookmark_optimized(ingestor, url, user_id, db)
        
        elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(f"⚡ TOTAL bookmark processing completed in {elapsed:.2f}s")
        
        return result
            
    except ValueError as e:
        logger.error(f"Validation error adding bookmark: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error adding optimized bookmark: {e}")
        raise RuntimeError(f"Failed to add bookmark: {str(e)}")
    finally:
        if should_close_db:
            db.close()

async def _create_new_bookmark_optimized(ingestor: BaseIngestor, url: str, user_id: int, db: Session) -> Dict[str, Any]:
    try:
        raw_data = await ingestor.extract_metadata(url)
        normalized = ingestor.normalize_metadata(raw_data)
        
        bookmark = Bookmark(
            platform=ingestor.platform,
            url=url,
            title=normalized.get("title"),
            author=normalized.get("author"),
            thumbnail_url=normalized.get("thumbnail_url"),
            description=normalized.get("description"),
            published_at=normalized.get("published_at"),
            user_id=user_id,
            raw=json.dumps(raw_data, default=str) 
        )
        
        db.add(bookmark)
        db.flush()
        
        if ingestor.platform == "youtube":
            _create_youtube_details_optimized(bookmark.id, normalized["platform_specific"], db)
        
        db.commit()
        db.refresh(bookmark)
        
        logger.info(f"✅ Created OPTIMIZED {ingestor.platform} bookmark: {bookmark.title}")
        return _format_bookmark_response_optimized(bookmark)
        
    except IntegrityError as e:
        db.rollback()
        if "unique constraint" in str(e).lower():
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        raise RuntimeError(f"Database error: {str(e)}")

def _create_youtube_details_optimized(bookmark_id: int, platform_data: Dict[str, Any], db: Session):
    details = YouTubeDetails(
        bookmark_id=bookmark_id,
        video_id=platform_data["video_id"],
        channel_id=platform_data.get("channel_id"),
        duration_seconds=platform_data.get("duration_seconds"),
        view_count=platform_data.get("view_count"),
        like_count=platform_data.get("like_count"),
        tags=json.dumps(platform_data.get("tags", [])),
        extra=json.dumps({}) 
    )
    db.add(details)

def _format_bookmark_response_optimized(bookmark: Bookmark) -> Dict[str, Any]:
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
    
    return response

def _create_basic_bookmark(url: str, user_id: int, db: Session) -> Dict[str, Any]:
    try:
        platform = _detect_platform(url)
        
        bookmark = Bookmark(
            platform=platform,
            url=url,
            user_id=user_id,
            raw='{}'
        )
        
        db.add(bookmark)
        db.commit()
        db.refresh(bookmark)
        
        return _format_bookmark_response_optimized(bookmark)
        
    except IntegrityError as e:
        db.rollback()
        if "unique constraint" in str(e).lower():
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        raise RuntimeError(f"Database error: {str(e)}")

def _detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    elif "tiktok.com" in url_lower:
        return "tiktok"
    elif "instagram.com" in url_lower:
        return "instagram"
    elif "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    elif "linkedin.com" in url_lower:
        return "linkedin"
    elif "reddit.com" in url_lower:
        return "reddit"
    elif "pinterest.com" in url_lower or "pin.it" in url_lower:
        return "pinterest"
    elif "snapchat.com" in url_lower:
        return "snapchat"
    elif "facebook.com" in url_lower or "fb.com" in url_lower:
        return "facebook"
    else:
        return "other"

async def process_bookmark_background(url: str, user_id: int) -> Dict[str, Any]:
    try:
        return await add_bookmark_ultra_fast(url, user_id)
    except Exception as e:
        logger.error(f"Background processing failed for {url}: {e}")
        raise 