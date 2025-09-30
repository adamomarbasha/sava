import re
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import yt_dlp
import requests
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
            'noplaylist': True,
            'playlistend': 1,
            'geo_bypass': False,
            'call_home': False,
            'check_formats': False,
        }
        
        def normalize_info_object(info: Dict[str, Any]) -> Dict[str, Any]:
            if isinstance(info, dict) and 'entries' in info and isinstance(info['entries'], list) and info['entries']:
                return info['entries'][0]
            return info
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                logger.info(f"Extracting metadata for YouTube URL: {url}")
                info = ydl.extract_info(url, download=False)
                info = normalize_info_object(info)
                if not info:
                    raise ValueError("No metadata extracted - video may be private or deleted")
                logger.info(f"Successfully extracted metadata for video: {info.get('title', 'Unknown')}")
                return info
        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            logger.error(f"yt-dlp download error for {url}: {e}")
            lower = error_msg.lower()
            
            if "private" in lower:
                raise ValueError("This video is private and cannot be accessed")
            elif "unavailable" in lower or "this video is not available" in lower:
                raise ValueError("This video is unavailable or has been removed")
            elif "blocked" in lower or "copyright" in lower:
                raise ValueError("This video is blocked in your region")
            elif "timeout" in lower or "timed out" in lower:
                raise ValueError("Request timed out - please try again")
            
            if "requested format is not available" in lower or "format is not available" in lower or "no such format" in lower:
                try:
                    safe_opts = { **ydl_opts, 'extract_flat': True }
                    with yt_dlp.YoutubeDL(safe_opts) as ydl:
                        logger.info(f"Retrying YouTube metadata with safe options (extract_flat) for URL: {url}")
                        info = ydl.extract_info(url, download=False)
                        info = normalize_info_object(info)
                        if info:
                            return info
                except Exception as inner_e:
                    logger.warning(f"Safe retry failed for {url}: {inner_e}")
                
                try:
                    vid = self.extract_video_id(url)
                    oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
                    r = requests.get(oembed_url, timeout=8)
                    if r.ok:
                        data = r.json()
                        if not vid:
                            vid = self.extract_video_id(data.get('url', url))
                        logger.info("Using YouTube oEmbed fallback for metadata")
                        return {
                            'id': vid or '',
                            'webpage_url': url,
                            'title': data.get('title'),
                            'uploader': data.get('author_name'),
                            'thumbnail': data.get('thumbnail_url'),
                            'duration': None,
                            'view_count': None,
                            'like_count': None,
                            'tags': [],
                        }
                except Exception as oembed_e:
                    logger.warning(f"oEmbed fallback failed for {url}: {oembed_e}")
                
                vid = self.extract_video_id(url)
                if not vid:
                    raise ValueError(f"Could not extract video ID from URL: {url}")
                logger.info(f"Falling back to minimal YouTube metadata for video id {vid}")
                return {
                    'id': vid,
                    'webpage_url': url,
                    'title': None,
                    'uploader': None,
                    'channel_id': None,
                    'duration': None,
                    'view_count': None,
                    'like_count': None,
                    'tags': [],
                    'thumbnails': [{'url': f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"}]
                }
            
            raise ValueError(f"Could not access video: {error_msg}")
        except Exception as e:
            logger.error(f"Unexpected error extracting metadata for {url}: {e}")
            raise ValueError(f"Metadata extraction failed: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            def to_str(val) -> str:
                if val is None:
                    return ""
                try:
                    return str(val)
                except Exception:
                    return ""
            
            def clean(val, max_len: Optional[int] = None) -> str:
                s = to_str(val).strip()
                if max_len is not None and s:
                    return s[:max_len]
                return s
            
            def none_if_empty(s: str) -> Optional[str]:
                return s if s else None
            
            published_at = None
            upload_date_val = raw_data.get('upload_date')
            if upload_date_val:
                try:
                    upload_date_str = clean(upload_date_val)
                    if upload_date_str:
                        published_at = datetime.strptime(upload_date_str, '%Y%m%d')
                except (ValueError, TypeError):
                    pass
            
            thumbnail_url = None
            thumbs = raw_data.get('thumbnails') or []
            if isinstance(thumbs, list) and thumbs:
                for t in reversed(thumbs):
                    url = t.get('url') if isinstance(t, dict) else None
                    if url:
                        thumbnail_url = to_str(url)
                        break
            if not thumbnail_url:
                thumb_single = raw_data.get('thumbnail')
                if thumb_single:
                    thumbnail_url = clean(thumb_single)
            
            video_id = clean(raw_data.get('id'))
            if not thumbnail_url and video_id:
                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            
            description_raw = raw_data.get('description')
            description = clean(description_raw)
            if description:
                if len(description) > 2000:
                    description = description[:2000] + '...'
            else:
                description = None
            
            raw_tags = raw_data.get('tags', [])
            if isinstance(raw_tags, list) and raw_tags:
                tags = [clean(tag, 50) for tag in raw_tags[:15] if clean(tag)]
            else:
                tags = []
            
            title = none_if_empty(clean(raw_data.get('title'), 500))
            uploader = none_if_empty(clean(raw_data.get('uploader'), 255))
            channel_name = none_if_empty(clean(raw_data.get('channel'), 255))
            author = uploader or channel_name
            
            normalized = {
                "title": title,
                "author": author,
                "thumbnail_url": thumbnail_url,
                "description": description,
                "published_at": published_at,
                "platform_specific": {
                    "video_id": video_id,
                    "channel_id": none_if_empty(clean(raw_data.get('channel_id'))),
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