import asyncio
import logging
import re
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from datetime import datetime

from TikTokApi import TikTokApi
from .base import BaseIngestor

logger = logging.getLogger(__name__)

class TikTokIngestor(BaseIngestor):
    def __init__(self):
        super().__init__()
        self.api = None

    @property
    def platform(self) -> str:
        return "tiktok"
    
    def can_handle(self, url: str) -> bool:
        url_lower = url.lower()
        return "tiktok.com" in url_lower
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": raw_data.get("title") or "TikTok video",
            "author": raw_data.get("author"),
            "description": raw_data.get("description"),
            "thumbnail_url": raw_data.get("thumbnail_url"),
            "published_at": raw_data.get("published_at"),
            "platform_specific": {
                "content_id": raw_data.get("raw", {}).get("id"),
                "content_type": "video",
                "username": raw_data.get("author"),
                "domain": "tiktok.com",
                "scraping_available": True,
            }
        }
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        try:
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._extract_metadata_async(url))
                    return future.result()
            except RuntimeError:
                return asyncio.run(self._extract_metadata_async(url))
        except Exception as e:
            logger.error(f"Error in TikTok metadata extraction: {e}")
            return self._fallback_extraction(url)
        
    async def _init_api(self):
        if self.api is None:
            try:
                self.api = TikTokApi()
                await self.api.create_sessions(
                    num_sessions=1, 
                    sleep_after=3,
                    headless=True,
                    ms_tokens=[] 
                )
                logger.info("TikTokApi initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize TikTokApi: {e}")
                self.api = None

    async def _extract_metadata_async(self, url: str) -> Dict[str, Any]:
        logger.info(f"Extracting TikTok metadata from: {url}")
        
        try:
            await self._init_api()
            
            if not self.api:
                logger.warning("TikTokApi not available, using fallback")
                return self._fallback_extraction(url)
            
            video_id = self._extract_video_id(url)
            if not video_id:
                logger.error(f"Could not extract video ID from URL: {url}")
                return self._fallback_extraction(url)
            
            try:
                video = self.api.video(id=video_id)
                video_data = await video.info()
                
                hashtags = self._extract_hashtags(video_data.desc or "")
                
                music_info = self._extract_music_info(video_data)
                
                metadata = {
                    "platform": "tiktok",
                    "url": url,
                    "title": self._get_title(video_data),
                    "author": video_data.author.username,
                    "thumbnail_url": self._get_thumbnail_url(video_data),
                    "description": video_data.desc or "",
                    "published_at": self._parse_timestamp(video_data.create_time),
                    "raw": {
                        "id": video_data.id,
                        "hashtags": hashtags,
                        "stats": {
                            "likes": getattr(video_data.stats, 'digg_count', 0),
                            "comments": getattr(video_data.stats, 'comment_count', 0),
                            "shares": getattr(video_data.stats, 'share_count', 0),
                            "plays": getattr(video_data.stats, 'play_count', 0),
                        },
                        "music": music_info,
                        "author": {
                            "username": video_data.author.username,
                            "nickname": getattr(video_data.author, 'nickname', video_data.author.username),
                        }
                    }
                }
                
                logger.info(f"Successfully extracted TikTok metadata for: {metadata.get('title', 'Unknown')[:50]}...")
                return metadata
                    
            except Exception as e:
                logger.error(f"TikTokApi error: {e}, using fallback")
                return self._fallback_extraction(url)
                
        except Exception as e:
            logger.error(f"Error extracting TikTok metadata: {e}")
            return self._fallback_extraction(url)

    def _get_title(self, video_data) -> str:
        if hasattr(video_data, 'desc') and video_data.desc:
            desc = video_data.desc.strip()
            clean_desc = re.sub(r'#\w+|@\w+', '', desc).strip()
            if clean_desc and len(clean_desc) > 10:
                return clean_desc[:100] + "..." if len(clean_desc) > 100 else clean_desc
        
        author = getattr(video_data.author, 'username', 'unknown')
        return f"TikTok video by @{author}"

    def _get_thumbnail_url(self, video_data) -> str:
        thumbnail_sources = [
            getattr(video_data, 'cover_url', None),
            getattr(video_data, 'thumbnail_url', None),
            getattr(video_data, 'cover', None),
        ]
        
        for thumbnail_url in thumbnail_sources:
            if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.startswith('http'):
                return thumbnail_url
        
        if hasattr(video_data, 'id') and video_data.id:
            video_id = video_data.id
            possible_urls = [
                f"https://p16-sign-va.tiktokcdn-us.com/obj/tos-useast2a-p-0068-tx/{video_id}",
                f"https://p16-sign.tiktokcdn-us.com/obj/tos-useast2a-p-0068-tx/{video_id}",
                f"https://p16-sign-va.tiktokcdn.com/obj/tos-useast2a-p-0068-tx/{video_id}",
            ]
            return possible_urls[0]
        
        return ""

    def _extract_hashtags(self, description: str) -> List[str]:
        if not description:
            return []
        
        hashtags = re.findall(r'#\w+', description)
        return hashtags

    def _extract_music_info(self, video_data) -> Dict[str, Any]:
        music_info = {
            "id": "",
            "title": "",
            "author": ""
        }
        
        try:
            if hasattr(video_data, 'music') and video_data.music:
                music = video_data.music
                music_info.update({
                    "id": getattr(music, 'id', ''),
                    "title": getattr(music, 'title', ''),
                    "author": getattr(music, 'author', ''),
                })
        except Exception as e:
            logger.warning(f"Could not extract music info: {e}")
        
        return music_info

    def _extract_video_id(self, url: str) -> Optional[str]:
        try:
            patterns = [
                r'/video/(\d+)',
                r'vm\.tiktok\.com/([A-Za-z0-9]+)',
                r'/v/(\d+)',
                r'tiktok\.com/@[^/]+/video/(\d+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return match.group(1)
                    
        except Exception as e:
            logger.error(f"Error extracting video ID: {e}")
            
        return None

    def _parse_timestamp(self, timestamp: int) -> str:
        if timestamp:
            try:
                if timestamp > 1e10: 
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp).isoformat()
            except Exception as e:
                logger.warning(f"Could not parse timestamp {timestamp}: {e}")
        return ""

    def _fallback_extraction(self, url: str) -> Dict[str, Any]:
        try:
            username_match = re.search(r'@([^/]+)', url)
            username = username_match.group(1) if username_match else 'unknown'
            
            video_id = self._extract_video_id(url)
            thumbnail_url = ''
            if video_id:
                thumbnail_url = f'https://p16-sign-va.tiktokcdn-us.com/obj/tos-useast2a-p-0068-tx/{video_id}'
                logger.info(f'Generated fallback thumbnail URL: {thumbnail_url}')
            
            return {
                'platform': 'tiktok',
                'url': url,
                'title': f"TikTok video by @{username}",
                'author': username,
                'thumbnail_url': thumbnail_url,
                'description': '',
                'published_at': None,
                'raw': {
                    'id': video_id,
                    'hashtags': [],
                    'stats': {
                        'likes': 0,
                        'comments': 0,
                        'shares': 0,
                        'plays': 0,
                    },
                    'music': {
                        'id': '',
                        'title': '',
                        'author': ''
                    },
                    'author': {
                        'username': username,
                        'nickname': username,
                    },
                    'fallback': True
                }
            }
        except Exception as e:
            logger.error(f"Error in fallback extraction: {e}")
            
        return {
            'platform': 'tiktok',
            'url': url,
            'title': 'TikTok video',
            'author': 'unknown',
            'thumbnail_url': '',
            'description': '',
            'published_at': None,
            'raw': {
                'id': '',
                'hashtags': [],
                'stats': {
                    'likes': 0,
                    'comments': 0,
                    'shares': 0,
                    'plays': 0,
                },
                'music': {
                    'id': '',
                    'title': '',
                    'author': ''
                },
                'author': {
                    'username': 'unknown',
                    'nickname': 'unknown',
                },
                'fallback': True
            }
        }

    async def cleanup(self):
        if self.api:
            try:
                await self.api.close()
            except Exception as e:
                logger.warning(f"Error closing TikTokApi: {e}")
            self.api = None
