import re
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from urllib.parse import urlparse
from TikTokApi import TikTokApi
from .base import BaseIngestor

logger = logging.getLogger(__name__)

class TikTokApiIngestor(BaseIngestor):
    
    def __init__(self):
        self.api = None
        self._initialized = False
        self._api_available = True 
    
    async def _ensure_initialized(self):
        if not self._initialized and self._api_available:
            try:
                self.api = TikTokApi()
                self._initialized = True
                logger.info("TikTokApi initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize TikTokApi: {e}")
                self._api_available = False
                raise ValueError(f"TikTokApi initialization failed: {str(e)}")
    
    @property
    def platform(self) -> str:
        return "tiktok"
    
    def can_handle(self, url: str) -> bool:
        if not self.validate_url(url):
            return False
        
        try:
            parsed = urlparse(url.lower())
            return (
                'tiktok.com' in parsed.netloc or 
                'vm.tiktok.com' in parsed.netloc or
                't.tiktok.com' in parsed.netloc
            )
        except:
            return False
    
    def extract_video_id(self, url: str) -> Optional[str]:
        try:
            video_pattern = r'tiktok\.com/@[^/]+/video/(\d+)'
            match = re.search(video_pattern, url)
            if match:
                return match.group(1)
            
            short_pattern = r'(vm\.tiktok\.com|t\.tiktok\.com)/([A-Za-z0-9]+)'
            match = re.search(short_pattern, url)
            if match:
                return match.group(2)
            
            return None
        except:
            return None
    
    def extract_username(self, url: str) -> Optional[str]:
        try:
            username_pattern = r'tiktok\.com/@([^/]+)'
            match = re.search(username_pattern, url)
            if match:
                return match.group(1)
            return None
        except:
            return None
    
    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        if not self._api_available:
            raise ValueError("TikTokApi is not available, falling back to Playwright ingestor")
        
        await self._ensure_initialized()
        
        try:
            logger.info(f"Extracting TikTok metadata using TikTokApi from: {url}")
            
            async with self.api as api:
                await api.create_sessions(ms_tokens=[], num_sessions=1, sleep_after=0.1)
                
                video_data = await api.video(url=url).info()
                
                if not video_data:
                    raise ValueError("No video data returned from TikTokApi")
                
                if isinstance(video_data, list):
                    if len(video_data) > 0:
                        video_data = video_data[0]
                    else:
                        raise ValueError("Empty video data list returned from TikTokApi")
                elif not isinstance(video_data, dict):
                    logger.warning(f"Unexpected video_data type: {type(video_data)}, value: {video_data}")
                    raise ValueError(f"Unexpected data type returned from TikTokApi: {type(video_data)}")
                
                logger.info(f"Successfully extracted TikTok metadata for: {video_data.get('desc', 'Unknown')[:50]}...")
                return {
                    'video_data': video_data,
                    'comments': []
                }
                
        except Exception as e:
            logger.error(f"TikTokApi failed for {url}: {e}")
            self._api_available = False
            raise ValueError(f"TikTokApi metadata extraction failed: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            video_data = raw_data.get('video_data', {})
            comments_data = raw_data.get('comments', [])
            
            video_info = video_data.get('video', {})
            author_info = video_data.get('author', {})
            stats = video_data.get('stats', {})
            music_info = video_data.get('music', {})
            
            desc = video_data.get('desc', '') or ''
            hashtags = re.findall(r'#(\w+)', desc)
            
            clean_desc = re.sub(r'#\w+', '', desc).strip()
            
            title = None
            if clean_desc:
                title = clean_desc[:500]
            elif hashtags:
                title = ' '.join([f'#{tag}' for tag in hashtags[:5]])
            
            video_url = None
            thumbnail_url = None
            if video_info:
                video_url = video_info.get('playAddr') or video_info.get('downloadAddr')
                thumbnail_url = video_info.get('cover') or video_info.get('originCover')
            
            processed_comments = []
            for comment in comments_data[:20]:
                comment_text = comment.get('text', '').strip()
                if comment_text:
                    processed_comments.append({
                        'text': comment_text,
                        'author': comment.get('user', {}).get('uniqueId', 'unknown'),
                        'likes': comment.get('diggCount', 0),
                        'created_at': comment.get('createTime')
                    })
            
            published_at = None
            create_time = video_data.get('createTime')
            if create_time:
                try:
                    published_at = datetime.fromtimestamp(int(create_time))
                except:
                    published_at = None
            
            normalized = {
                "title": title,
                "author": author_info.get('uniqueId', '').strip()[:255] or None,
                "thumbnail_url": thumbnail_url,
                "description": desc[:2000] if desc else None,
                "published_at": published_at,
                "platform_specific": {
                    "video_id": video_data.get('id', '').strip(),
                    "author_id": author_info.get('id', '').strip() or None,
                    "author_name": author_info.get('nickname', '').strip() or None,
                    "author_follower_count": author_info.get('followerCount'),
                    "author_verified": author_info.get('verified', False),
                    "duration_seconds": video_info.get('duration'),
                    "view_count": stats.get('playCount'),
                    "like_count": stats.get('diggCount'),
                    "comment_count": stats.get('commentCount'),
                    "share_count": stats.get('shareCount'),
                    "hashtags": hashtags,
                    "video_url": video_url,
                    "video_width": video_info.get('width'),
                    "video_height": video_info.get('height'),
                    "video_ratio": video_info.get('ratio'),
                    "music_title": music_info.get('title', '') if music_info else None,
                    "music_author": music_info.get('authorName', '') if music_info else None,
                    "ai_data": {
                        "clean_caption": clean_desc,
                        "hashtags_for_tagging": hashtags,
                        "comments_sample": processed_comments,
                        "engagement_metrics": {
                            "views": stats.get('playCount', 0),
                            "likes": stats.get('diggCount', 0),
                            "comments": stats.get('commentCount', 0),
                            "shares": stats.get('shareCount', 0),
                            "engagement_rate": self._calculate_engagement_rate(stats)
                        },
                        "author_metadata": {
                            "username": author_info.get('uniqueId'),
                            "display_name": author_info.get('nickname'),
                            "bio": author_info.get('signature', ''),
                            "follower_count": author_info.get('followerCount', 0),
                            "verified": author_info.get('verified', False),
                            "profile_pic_url": author_info.get('avatarLarger')
                        },
                        "music_metadata": {
                            "title": music_info.get('title', '') if music_info else None,
                            "author": music_info.get('authorName', '') if music_info else None,
                            "duration": music_info.get('duration', 0) if music_info else None,
                            "original": music_info.get('original', False) if music_info else None
                        } if music_info else None
                    }
                }
            }
            
            if not normalized["platform_specific"]["video_id"]:
                raise ValueError("No video ID found in metadata")
            
            return normalized
            
        except Exception as e:
            logger.error(f"Error normalizing TikTok metadata: {e}")
            raise ValueError(f"Failed to normalize metadata: {str(e)}")
    
    def _calculate_engagement_rate(self, stats: Dict[str, Any]) -> float:
        try:
            views = stats.get('playCount', 0)
            likes = stats.get('diggCount', 0)
            comments = stats.get('commentCount', 0)
            shares = stats.get('shareCount', 0)
            
            if views == 0:
                return 0.0
            
            total_engagement = likes + comments + shares
            return round((total_engagement / views) * 100, 2)
        except:
            return 0.0
    
    async def __aenter__(self):
        await self._ensure_initialized()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.api and self._initialized:
            try:
                await self.api.close()
            except:
                pass
