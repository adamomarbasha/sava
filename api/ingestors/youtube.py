import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import yt_dlp
from .base import BaseIngestor

logger = logging.getLogger(__name__)

class YouTubeIngestor(BaseIngestor):
    
    @property
    def platform(self) -> str:
        return "youtube"
    
    def can_handle(self, url: str) -> bool:
        if not self.validate_url(url):
            return False
        
        try:
            parsed = urlparse(url.lower())
            return (
                'youtube.com' in parsed.netloc or 
                'youtu.be' in parsed.netloc or
                'm.youtube.com' in parsed.netloc
            )
        except:
            return False
    
    def extract_video_id(self, url: str) -> Optional[str]:
        try:
            parsed = urlparse(url)
            
            if 'youtu.be' in parsed.netloc:
                return parsed.path.lstrip('/')
            
            if 'youtube.com' in parsed.netloc:
                if parsed.path == '/watch':
                    return parse_qs(parsed.query).get('v', [None])[0]
                elif parsed.path.startswith('/embed/'):
                    return parsed.path.split('/embed/')[1].split('?')[0]
                elif parsed.path.startswith('/v/'):
                    return parsed.path.split('/v/')[1].split('?')[0]
            
            return None
        except:
            return None
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        if not self.can_handle(url):
            raise ValueError(f"Cannot handle URL: {url}")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'skip_download': True,
            'no_check_formats': True,
            'no_check_certificate': True,
            'socket_timeout': 10,
            'retries': 0,
            'fragment_retries': 0,
            'ignoreerrors': False,
            'no_color': True,
            'extractaudio': False,
            'writeautomaticsub': False,
            'writesubtitles': False,
            'writethumbnail': False,
            'writeinfojson': False,
            'writedescription': False,
            'youtube_include_dash_manifest': False,
            'youtube_include_hls_manifest': False,
            'format': 'worst',
            'noplaylist': True,
            'playlistend': 1,
            'geo_bypass': False,
            'call_home': False,
            'check_formats': False,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Extracting metadata for YouTube URL: {url}")
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    raise ValueError("No metadata extracted - video may be private or deleted")
                
                logger.info(f"Successfully extracted metadata for video: {info.get('title', 'Unknown')}")
                return info
                
        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            logger.error(f"yt-dlp download error for {url}: {e}")
            
            if "private" in error_msg.lower():
                raise ValueError("This video is private and cannot be accessed")
            elif "unavailable" in error_msg.lower():
                raise ValueError("This video is unavailable or has been removed")
            elif "blocked" in error_msg.lower():
                raise ValueError("This video is blocked in your region")
            elif "timeout" in error_msg.lower():
                raise ValueError("Request timed out - please try again")
            else:
                raise ValueError(f"Could not access video: {error_msg}")
        except Exception as e:
            logger.error(f"Unexpected error extracting metadata for {url}: {e}")
            raise ValueError(f"Metadata extraction failed: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            published_at = None
            if raw_data.get('upload_date'):
                try:
                    upload_date_str = str(raw_data['upload_date'])
                    published_at = datetime.strptime(upload_date_str, '%Y%m%d')
                except (ValueError, TypeError):
                    pass
            
            thumbnail_url = None
            if raw_data.get('thumbnails'):
                thumbnails = raw_data['thumbnails']
                if thumbnails:
                    thumbnail_url = thumbnails[-1].get('url')
            
            video_id = raw_data.get('id', '').strip()
            if not thumbnail_url and video_id:
                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            
            description = raw_data.get('description', '')
            if description:
                description = description.strip()
                if len(description) > 2000:
                    description = description[:2000] + '...'
            else:
                description = None
            
            tags = raw_data.get('tags', [])
            if isinstance(tags, list) and tags:
                tags = [str(tag)[:50] for tag in tags[:15]]
            else:
                tags = []
            
            normalized = {
                "title": (raw_data.get('title', '').strip()[:500]) or None,
                "author": (raw_data.get('uploader', '').strip()[:255] or 
                          raw_data.get('channel', '').strip()[:255]) or None,
                "thumbnail_url": thumbnail_url,
                "description": description,
                "published_at": published_at,
                "platform_specific": {
                    "video_id": raw_data.get('id', '').strip(),
                    "channel_id": raw_data.get('channel_id', '').strip() or None,
                    "duration_seconds": raw_data.get('duration'),
                    "view_count": raw_data.get('view_count'),
                    "like_count": raw_data.get('like_count'),
                    "tags": tags,
                }
            }
            
            if not normalized["platform_specific"]["video_id"]:
                raise ValueError("No video ID found in metadata")
            
            return normalized
            
        except Exception as e:
            logger.error(f"Error normalizing YouTube metadata: {e}")
            raise ValueError(f"Failed to normalize metadata: {str(e)}")

class TikTokIngestor(BaseIngestor):
    @property
    def platform(self) -> str:
        return "tiktok"
    
    def can_handle(self, url: str) -> bool:
        return 'tiktok.com' in url.lower()
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        raise NotImplementedError("TikTok ingestion not implemented yet")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("TikTok normalization not implemented yet")

class InstagramIngestor(BaseIngestor):
    @property
    def platform(self) -> str:
        return "instagram"
    
    def can_handle(self, url: str) -> bool:
        return 'instagram.com' in url.lower()
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        raise NotImplementedError("Instagram ingestion not implemented yet")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("Instagram normalization not implemented yet")

class TwitterIngestor(BaseIngestor):
    @property
    def platform(self) -> str:
        return "twitter"
    
    def can_handle(self, url: str) -> bool:
        return 'twitter.com' in url.lower() or 'x.com' in url.lower()
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        raise NotImplementedError("Twitter ingestion not implemented yet")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("Twitter normalization not implemented yet") 