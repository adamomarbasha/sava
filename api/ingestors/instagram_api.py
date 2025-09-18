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
import requests

try:
    import instaloader
except ImportError:
    instaloader = None

from .base import BaseIngestor

logger = logging.getLogger(__name__)

_metadata_cache = {}

class InstagramApiIngestor:
    def __init__(self, username=None, password=None, session_id=None):
        self.loader = instaloader.Instaloader(
            "Sava-Bookmark-App",
            compress_json=False,
            save_metadata=False,
            download_comments=False,
            download_geotags=False,
            download_videos=False,
            post_metadata_txt_pattern=""
        )
        self.platform = "instagram"
        
        if username:
            try:
                logger.info(f"Attempting to load Instagram session for {username}")
                self.loader.load_session_from_file(username)
                logger.info(f"Successfully loaded session for {username}.")
            except FileNotFoundError:
                logger.info(f"No session file found for {username}. Attempting to login and create a new session.")
                if password:
                    try:
                        self.loader.login(username, password)
                        self.loader.save_session_to_file()
                        logger.info(f"Successfully logged in and saved session for {username}.")
                    except Exception as e:
                        logger.error(f"Failed to login to Instagram for user {username}: {e}")
                else:
                    logger.warning(f"Instagram username '{username}' provided without a password and no existing session file.")
            except Exception as e:
                logger.error(f"An unexpected error occurred during Instagram session handling for {username}: {e}")
        else:
            logger.warning("Instagram ingestor is running without authentication. May be rate-limited or fail for private content.")

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
    
    def _get_best_thumbnail(self, post):
        if post.is_video:
            return post.url
        
        return post.url

    async def extract_metadata(self, url):
        try:
            shortcode = self.extract_shortcode(url)
            if not shortcode:
                return {"error": "Invalid Instagram URL"}
            
            logger.info(f"Extracting Instagram metadata using Instaloader from: {url}")
            
            post = instaloader.Post.from_shortcode(self.loader.context, shortcode)
            
            media_url = self._get_best_thumbnail(post)
            thumbnail_url = None

            if media_url:
                try:
                    thumbnail_dir = "static/thumbnails"
                    os.makedirs(thumbnail_dir, exist_ok=True)
                    
                    thumbnail_filename = f"instagram_{shortcode}.jpg"
                    thumbnail_filepath = os.path.join(thumbnail_dir, thumbnail_filename)

                    if not os.path.exists(thumbnail_filepath):
                        logger.info(f"Downloading Instagram thumbnail from {media_url} to {thumbnail_filepath}")
                        response = self.loader.context._session.get(media_url, stream=True)
                        response.raise_for_status()
                        with open(thumbnail_filepath, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        logger.info(f"Saved Instagram thumbnail to {thumbnail_filepath}")
                    else:
                        logger.info(f"Instagram thumbnail already exists at {thumbnail_filepath}")

                    thumbnail_url = f"/static/thumbnails/{thumbnail_filename}"

                except Exception as e:
                    logger.error(f"Failed to download Instagram thumbnail for {shortcode} from {media_url}: {e}")
                    thumbnail_url = post.url 
            else:
                logger.warning(f"Could not find a media URL for Instagram post {shortcode}")
            
            title = "Instagram Post"
            if post.caption:
                clean_caption = re.sub(r'#\w+', '', post.caption).strip()
                if (clean_caption):
                    title = clean_caption[:100] + ("..." if len(clean_caption) > 100 else "")
                else:
                    title = "Instagram Post"
            
            metadata = {
                "title": title,
                "thumbnail_url": thumbnail_url,
                "platform": "instagram",
                "author": post.owner_username,
                "description": post.caption or "",
                "published_at": post.date_utc.isoformat() if post.date_utc else None,
                "meta": {
                    "like_count": post.likes,
                    "comment_count": post.comments,
                    "is_video": post.is_video,
                    "shortcode": shortcode,
                    "media_type": "video" if post.is_video else "image"
                }
            }
            
            logger.info(f"Successfully extracted Instagram metadata for: {shortcode}, thumbnail: {thumbnail_url}")
            return metadata
            
        except Exception as e:
            logger.error(f"Instaloader failed for {url}: {str(e)}")
            return {
                "title": "Instagram Post",
                "platform": "instagram",
                "thumbnail_url": None,
                "error": str(e)
            }
