from .base import BaseIngestor
from typing import Dict, Any, Optional, List
import re
import logging
from urllib.parse import urlparse
from datetime import datetime
import json

try:
    from instagrapi import Client
    from instagrapi.exceptions import LoginRequired, ChallengeRequired, SelectContactPointRecoveryForm
    INSTAGRAPI_AVAILABLE = True
except ImportError:
    INSTAGRAPI_AVAILABLE = False
    Client = None

logger = logging.getLogger(__name__)


class InstagramApiIngestor(BaseIngestor):
    
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self._platform = "instagram"
        self.username = username
        self.password = password
        self.client = None
        self._initialize_client()
    
    @property
    def platform(self) -> str:
        return self._platform
    
    def can_handle(self, url: str) -> bool:
        if not INSTAGRAPI_AVAILABLE:
            return False
            
        url_lower = url.lower()
        instagram_patterns = [
            "instagram.com/p/", 
            "instagram.com/reel/",
            "instagram.com/tv/",
            "instagram.com/stories/",
        ]
        return any(pattern in url_lower for pattern in instagram_patterns)
    
    def _initialize_client(self):
        if not INSTAGRAPI_AVAILABLE:
            logger.warning("instagrapi not available. Install with: pip install instagrapi")
            return
            
        try:
            self.client = Client()
        except Exception as e:
            logger.error(f"Failed to initialize Instagram client: {e}")
            self.client = None
    
    def _extract_media_id(self, url: str) -> Optional[str]:
        patterns = [
            r'/p/([^/]+)',
            r'/reel/([^/]+)',
            r'/tv/([^/]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    def _extract_username_from_story(self, url: str) -> Optional[str]:
        story_pattern = r'/stories/([^/]+)/'
        match = re.search(story_pattern, url)
        return match.group(1) if match else None
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        try:
            if not self.client:
                return self._fallback_extraction(url)
            
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid URL format: {url}")
            
            media_id = self._extract_media_id(url)
            username = self._extract_username_from_story(url)
            
            metadata = {
                "url": url,
                "platform": self.platform,
                "domain": parsed.netloc,
                "path": parsed.path,
                "extracted_at": datetime.now().isoformat(),
                "media_id": media_id,
                "username": username,
            }
            
            if "/p/" in url:
                metadata["content_type"] = "post"
            elif "/reel/" in url:
                metadata["content_type"] = "reel"
            elif "/tv/" in url:
                metadata["content_type"] = "igtv"
            elif "/stories/" in url:
                metadata["content_type"] = "story"
            else:
                metadata["content_type"] = "unknown"
            
            if self.username and self.password and media_id:
                try:
                    detailed_metadata = self._get_detailed_metadata(media_id, url)
                    metadata.update(detailed_metadata)
                except Exception as e:
                    logger.warning(f"Could not get detailed metadata: {e}")
                    metadata["detailed_extraction_failed"] = str(e)
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error extracting metadata from Instagram URL {url}: {e}")
            return self._fallback_extraction(url)
    
    def _get_detailed_metadata(self, media_id: str, url: str) -> Dict[str, Any]:
        try:
            if not self.client.user_id:
                self.client.login(self.username, self.password)
            
            media_info = self.client.media_info(media_id)
            
            detailed_metadata = {
                "title": media_info.caption_text or f"Instagram {media_info.media_type}",
                "author": f"@{media_info.user.username}",
                "description": media_info.caption_text,
                "thumbnail_url": media_info.thumbnail_url or media_info.video_url,
                "published_at": media_info.taken_at.isoformat() if media_info.taken_at else None,
                
                "ai_metadata": {
                    "media_type": media_info.media_type,
                    "like_count": media_info.like_count,
                    "comment_count": media_info.comment_count,
                    "view_count": getattr(media_info, 'view_count', None),
                    "play_count": getattr(media_info, 'play_count', None),
                    "hashtags": self._extract_hashtags(media_info.caption_text or ""),
                    "mentions": self._extract_mentions(media_info.caption_text or ""),
                    "is_video": media_info.media_type in ['video', 'reel', 'igtv'],
                    "video_duration": getattr(media_info, 'video_duration', None),
                    "media_urls": {
                        "image_url": media_info.thumbnail_url,
                        "video_url": getattr(media_info, 'video_url', None),
                    },
                    "user_info": {
                        "username": media_info.user.username,
                        "full_name": media_info.user.full_name,
                        "follower_count": media_info.user.follower_count,
                        "following_count": media_info.user.following_count,
                        "is_verified": media_info.user.is_verified,
                        "is_private": media_info.user.is_private,
                    },
                    "location": {
                        "name": getattr(media_info, 'location', {}).get('name') if hasattr(media_info, 'location') else None,
                        "address": getattr(media_info, 'location', {}).get('address') if hasattr(media_info, 'location') else None,
                    } if hasattr(media_info, 'location') else None,
                }
            }
            
            return detailed_metadata
            
        except (LoginRequired, ChallengeRequired, SelectContactPointRecoveryForm) as e:
            logger.warning(f"Instagram login required or challenge needed: {e}")
            return {"login_required": True, "error": str(e)}
        except Exception as e:
            logger.error(f"Error getting detailed Instagram metadata: {e}")
            return {"detailed_extraction_error": str(e)}
    
    def _extract_hashtags(self, text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r'#\w+', text)
    
    def _extract_mentions(self, text: str) -> List[str]:
        if not text:
            return []
        return re.findall(r'@\w+', text)
    
    def _fallback_extraction(self, url: str) -> Dict[str, Any]:
        parsed = urlparse(url)
        media_id = self._extract_media_id(url)
        username = self._extract_username_from_story(url)
        
        return {
            "url": url,
            "platform": self.platform,
            "domain": parsed.netloc,
            "path": parsed.path,
            "extracted_at": datetime.now().isoformat(),
            "media_id": media_id,
            "username": username,
            "title": f"Instagram {self._get_content_type_from_url(url)}",
            "author": f"@{username}" if username else "Instagram User",
            "description": f"Instagram {self._get_content_type_from_url(url)} content",
            "scraping_available": False,
            "fallback_extraction": True,
        }
    
    def _get_content_type_from_url(self, url: str) -> str:
        if "/p/" in url:
            return "post"
        elif "/reel/" in url:
            return "reel"
        elif "/tv/" in url:
            return "igtv"
        elif "/stories/" in url:
            return "story"
        return "content"
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": raw_data.get("title") or f"Instagram {raw_data.get('content_type', 'content')}",
            "author": raw_data.get("author"),
            "description": raw_data.get("description"),
            "thumbnail_url": raw_data.get("thumbnail_url"),
            "published_at": raw_data.get("published_at"),
            "platform_specific": {
                "media_id": raw_data.get("media_id"),
                "content_type": raw_data.get("content_type"),
                "username": raw_data.get("username"),
                "domain": raw_data.get("domain"),
                "ai_metadata": raw_data.get("ai_metadata", {}),
                "scraping_available": not raw_data.get("fallback_extraction", False),
                "login_required": raw_data.get("login_required", False),
            }
        }
    
    def get_media_for_ai(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            if not self.client or not self.username or not self.password:
                logger.warning("Instagram credentials not provided for AI media extraction")
                return None
            
            media_id = self._extract_media_id(url)
            if not media_id:
                return None
            
            if not self.client.user_id:
                self.client.login(self.username, self.password)
            
            media_info = self.client.media_info(media_id)
            
            ai_data = {
                "media_id": media_id,
                "url": url,
                "content_type": media_info.media_type,
                "caption": media_info.caption_text,
                "hashtags": self._extract_hashtags(media_info.caption_text or ""),
                "mentions": self._extract_mentions(media_info.caption_text or ""),
                "engagement": {
                    "likes": media_info.like_count,
                    "comments": media_info.comment_count,
                    "views": getattr(media_info, 'view_count', None),
                },
                "user": {
                    "username": media_info.user.username,
                    "full_name": media_info.user.full_name,
                    "followers": media_info.user.follower_count,
                    "verified": media_info.user.is_verified,
                },
                "media_urls": {
                    "thumbnail": media_info.thumbnail_url,
                    "video": getattr(media_info, 'video_url', None),
                },
                "timestamp": media_info.taken_at.isoformat() if media_info.taken_at else None,
            }
            
            return ai_data
            
        except Exception as e:
            logger.error(f"Error getting AI media data: {e}")
            return None
