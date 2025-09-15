import logging
import json
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .base import BaseIngestor
from .youtube import YouTubeIngestor
from .tiktok_api import TikTokApiIngestor
from .tiktok import TikTokIngestor 
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
import asyncio

logger = logging.getLogger(__name__)

INGESTORS = [
    YouTubeIngestor(),
    TikTokApiIngestor(), 
    TikTokIngestor(),    
    InstagramIngestor(), 
    TwitterIngestor(),
    LinkedInIngestor(),
    RedditIngestor(),
    PinterestIngestor(),
    SnapchatIngestor(),
    FacebookIngestor(),
]

def get_ingestor(url: str) -> Optional[BaseIngestor]:
    for ingestor in INGESTORS:
        if ingestor.can_handle(url):
            return ingestor
    return None

def get_tiktok_ingestors(url: str) -> list:
    tiktok_ingestors = []
    for ingestor in INGESTORS:
        if ingestor.platform == "tiktok" and ingestor.can_handle(url):
            tiktok_ingestors.append(ingestor)
    return tiktok_ingestors

async def add_bookmark(url: str, user_id: int, db: Session = None) -> Dict[str, Any]:
    should_close_db = False
    if db is None:
        db = SessionLocal()
        should_close_db = True
    
    try:
        if "tiktok.com" in url.lower():
            tiktok_ingestors = get_tiktok_ingestors(url)
            if tiktok_ingestors:
                for ingestor in tiktok_ingestors:
                    try:
                        logger.info(f"Trying {type(ingestor).__name__} for TikTok URL: {url}")
                        return await _create_new_bookmark(ingestor, url, user_id, db)
                    except Exception as e:
                        logger.warning(f"{type(ingestor).__name__} failed: {e}")
                        continue
                raise ValueError("All TikTok ingestors failed")
        
        ingestor = get_ingestor(url)
        
        if not ingestor:
            logger.info(f"No ingestor found for URL: {url}, creating basic bookmark")
            return _create_basic_bookmark(url, user_id, db)
        
        logger.info(f"Using {ingestor.platform} ingestor for URL: {url}")
        
        existing = db.query(Bookmark).filter(Bookmark.url == url, Bookmark.user_id == user_id).first()
        
        if existing:
            logger.info(f"Bookmark already exists for URL: {url}")
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        else:
            return await _create_new_bookmark(ingestor, url, user_id, db)
            
    except ValueError as e:
        logger.error(f"Validation error adding bookmark: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error adding bookmark: {e}")
        raise RuntimeError(f"Failed to add bookmark: {str(e)}")
    finally:
        if should_close_db:
            db.close()

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
        
        return _format_bookmark_response(bookmark)
        
    except IntegrityError as e:
        db.rollback()
        if "unique constraint" in str(e).lower():
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        raise RuntimeError(f"Database error: {str(e)}")

async def _create_new_bookmark(ingestor: BaseIngestor, url: str, user_id: int, db: Session) -> Dict[str, Any]:
    try:
        if asyncio.iscoroutinefunction(ingestor.extract_metadata):
            raw_data = await ingestor.extract_metadata(url)
        else:
            raw_data = ingestor.extract_metadata(url)
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
            raw=json.dumps(raw_data)
        )
        
        db.add(bookmark)
        db.flush()
        
        if ingestor.platform == "youtube":
            _create_youtube_details(bookmark.id, normalized["platform_specific"], db)
        
        db.commit()
        db.refresh(bookmark)
        
        logger.info(f"Created new {ingestor.platform} bookmark: {bookmark.title}")
        return _format_bookmark_response(bookmark)
        
    except IntegrityError as e:
        db.rollback()
        if "unique constraint" in str(e).lower():
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        raise RuntimeError(f"Database error: {str(e)}")

async def _update_existing_bookmark(bookmark: Bookmark, ingestor: BaseIngestor, url: str, db: Session) -> Dict[str, Any]:
    try:
        raw_data = await ingestor.extract_metadata(url)
        normalized = ingestor.normalize_metadata(raw_data)
        
        bookmark.title = normalized.get("title") or bookmark.title
        bookmark.author = normalized.get("author") or bookmark.author
        bookmark.thumbnail_url = normalized.get("thumbnail_url") or bookmark.thumbnail_url
        bookmark.description = normalized.get("description") or bookmark.description
        bookmark.published_at = normalized.get("published_at") or bookmark.published_at
        bookmark.raw = json.dumps(raw_data)
        
        if ingestor.platform == "youtube" and bookmark.youtube_details:
            _update_youtube_details(bookmark.youtube_details[0], normalized["platform_specific"])
        
        db.commit()
        db.refresh(bookmark)
        
        logger.info(f"Updated existing {ingestor.platform} bookmark: {bookmark.title}")
        return _format_bookmark_response(bookmark)
        
    except Exception as e:
        db.rollback()
        raise RuntimeError(f"Failed to update bookmark: {str(e)}")

def _create_youtube_details(bookmark_id: int, platform_data: Dict[str, Any], db: Session):
    details = YouTubeDetails(
        bookmark_id=bookmark_id,
        video_id=platform_data["video_id"],
        channel_id=platform_data.get("channel_id"),
        duration_seconds=platform_data.get("duration_seconds"),
        view_count=platform_data.get("view_count"),
        like_count=platform_data.get("like_count"),
        tags=json.dumps(platform_data.get("tags", [])),
        extra=json.dumps(platform_data.get("extra", {}))
    )
    db.add(details)

def _update_youtube_details(details: YouTubeDetails, platform_data: Dict[str, Any]):
    details.channel_id = platform_data.get("channel_id") or details.channel_id
    details.duration_seconds = platform_data.get("duration_seconds") or details.duration_seconds
    details.view_count = platform_data.get("view_count") or details.view_count
    details.like_count = platform_data.get("like_count") or details.like_count
    details.tags = json.dumps(platform_data.get("tags", [])) if platform_data.get("tags") else details.tags
    details.extra = json.dumps(platform_data.get("extra", {})) if platform_data.get("extra") else details.extra

def _format_bookmark_response(bookmark: Bookmark) -> Dict[str, Any]:
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
