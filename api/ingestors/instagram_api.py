import logging
import re
import json
import hashlib
import tempfile
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from urllib.parse import urlparse
import asyncio

try:
    import instaloader
except ImportError:
    instaloader = None

from .base import BaseIngestor

logger = logging.getLogger(__name__)

_metadata_cache = {}

class InstagramApiIngestor:
    def __init__(self, username=None, password=None, session_id=None):
        self.loader = instaloader.Instaloader()
        self.platform = "instagram"
        
        if username and password:
            try:
                self.loader.login(username, password)
                logger.info(f"Successfully logged into Instagram as {username}")
            except Exception as e:
                logger.warning(f"Failed to login to Instagram: {e}")
        elif session_id:
            self.loader.context._session.cookies.set('sessionid', session_id)
    
    def can_handle(self, url):
        return 'instagram.com' in url.lower()
    
    def normalize_metadata(self, metadata):
        from datetime import datetime
        
        published_at = None
        if metadata.get('published_at'):
            try:
                published_at = datetime.fromisoformat(metadata['published_at'].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                published_at = None
        
        return {
            'title': metadata.get('title', ''),
            'description': metadata.get('description', ''),
            'thumbnail_url': metadata.get('thumbnail_url', ''),
            'author': metadata.get('author', ''),
            'published_at': published_at,  
            'platform': 'instagram'
        }
    
    def extract_shortcode(self, url):
        patterns = [
            r'instagram\.com/p/([^/?]+)',
            r'instagram\.com/reel/([^/?]+)',
            r'instagram\.com/tv/([^/?]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    
    async def extract_metadata(self, url):
        try:
            shortcode = self.extract_shortcode(url)
            if not shortcode:
                return {"error": "Invalid Instagram URL"}
            
            logger.info(f"Extracting Instagram metadata using Instaloader from: {url}")
            
            post = instaloader.Post.from_shortcode(self.loader.context, shortcode)
            
            thumbnail_url = post.url 
            
            metadata = {
                "title": post.caption[:200] if post.caption else "Instagram Post",
                "thumbnail_url": thumbnail_url,
                "platform": "instagram",
                "author": post.owner_username,
                "published_at": post.date_utc.isoformat() if post.date_utc else None,
                "meta": {
                    "like_count": post.likes,
                    "comment_count": post.comments,
                    "is_video": post.is_video,
                    "shortcode": shortcode
                }
            }
            
            logger.info(f"Successfully extracted Instagram metadata for: {shortcode}")
            return metadata
            
        except Exception as e:
            logger.error(f"Instaloader failed for {url}: {str(e)}")
            return {
                "title": "Instagram Post",
                "platform": "instagram",
                "error": str(e)
            }
