import re
import json
import logging
import asyncio
import hashlib
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from urllib.parse import urlparse
import httpx
import orjson
from playwright.async_api import async_playwright, Browser, BrowserContext
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer
from .base import BaseIngestor

logger = logging.getLogger(__name__)

cache = Cache(Cache.MEMORY, serializer=PickleSerializer(), ttl=1800)

class TikTokOptimizedIngestor(BaseIngestor):
    
    _browser_instance = None
    _context_instance = None
    _browser_lock = asyncio.Lock()
    
    def __init__(self):
        self.client = None
        self._initialized = False
        self._browser_ready = False
    
    @classmethod
    async def _get_shared_browser(cls) -> tuple[Browser, BrowserContext]:
        async with cls._browser_lock:
            if cls._browser_instance is None or not cls._browser_instance.is_connected():
                async with async_playwright() as p:
                    cls._browser_instance = await p.chromium.launch(
                        headless=True,
                        args=[
                            '--no-sandbox',
                            '--disable-setuid-sandbox',
                            '--disable-dev-shm-usage',
                            '--disable-accelerated-2d-canvas',
                            '--disable-gpu',
                            '--disable-background-timer-throttling',
                            '--disable-backgrounding-occluded-windows',
                            '--disable-renderer-backgrounding',
                            '--disable-features=TranslateUI,VizDisplayCompositor',
                            '--disable-ipc-flooding-protection',
                            '--disable-web-security',
                            '--disable-extensions',
                            '--disable-default-apps',
                            '--no-first-run',
                            '--no-default-browser-check',
                            '--disable-sync',
                            '--disable-background-networking',
                            '--disable-plugins',
                            '--disable-images',
                            '--disable-javascript-harmony-shipping',
                            '--disable-component-extensions-with-background-pages',
                            '--disable-permissions-api',
                            '--disable-notifications',
                            '--disable-popup-blocking',
                            '--memory-pressure-off',
                            '--max_old_space_size=4096'
                        ]
                    )
                    
                    cls._context_instance = await cls._browser_instance.new_context(
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        java_script_enabled=True,
                        ignore_https_errors=True,
                        bypass_csp=True,
                        extra_http_headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.5",
                            "Accept-Encoding": "gzip, deflate, br",
                            "DNT": "1",
                            "Connection": "keep-alive",
                        }
                    )
        
        return cls._browser_instance, cls._context_instance
    
    async def _ensure_initialized(self):
        """Initialize HTTP client if not already done"""
        if not self._initialized:
            try:
                limits = httpx.Limits(
                    max_keepalive_connections=50, 
                    max_connections=200,
                    keepalive_expiry=30
                )
                self.client = httpx.AsyncClient(
                    timeout=httpx.Timeout(8.0, connect=3.0),  # Ultra-fast timeouts
                    limits=limits,
                    http2=True,  # Enable HTTP/2 for better performance
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate, br",
                        "DNT": "1",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                    }
                )
                self._initialized = True
                logger.info("TikTok OPTIMIZED HTTP client initialized with ultra-fast settings")
            except Exception as e:
                logger.error(f"Failed to initialize TikTok optimized client: {e}")
                raise ValueError(f"TikTok client initialization failed: {str(e)}")
    
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
                't.tiktok.com' in parsed.netloc or
                'www.tiktok.com' in parsed.netloc
            )
        except:
            return False
    
    def _generate_cache_key(self, url: str) -> str:
        """Generate cache key for URL"""
        return f"tiktok_metadata_{hashlib.md5(url.encode()).hexdigest()}"
    
    @cached(ttl=1800, cache=cache, key_builder=lambda f, self, url: f"tiktok_{hashlib.md5(url.encode()).hexdigest()}")
    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        """Extract TikTok metadata with ULTRA PERFORMANCE optimizations"""
        await self._ensure_initialized()
        
        try:
            logger.info(f"🚀 ULTRA-FAST TikTok extraction from: {url}")
            start_time = asyncio.get_event_loop().time()
            
            browser, context = await self._get_shared_browser()
            page = await context.new_page()
            
            try:
                # AGGRESSIVE resource blocking for maximum speed
                await page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,css,woff,woff2,ttf,eot,mp4,webm,mp3,wav}", lambda route: route.abort())
                await page.route("**/analytics/**", lambda route: route.abort())
                await page.route("**/ads/**", lambda route: route.abort())
                await page.route("**/tracking/**", lambda route: route.abort())
                await page.route("**/metrics/**", lambda route: route.abort())
                await page.route("**/beacon/**", lambda route: route.abort())
                await page.route("**/collect/**", lambda route: route.abort())
                
                # Navigate with MINIMAL waiting - SPEED FIRST
                await page.goto(url, wait_until='domcontentloaded', timeout=8000)
                
                # ULTRA-MINIMAL wait - just enough for script injection
                await page.wait_for_timeout(500)
                
                # Extract JSON data with optimized selector
                script_content = await page.evaluate("""
                    () => {
                        const script = document.querySelector('script[id="__UNIVERSAL_DATA_FOR_REHYDRATION__"]');
                        return script ? script.textContent : null;
                    }
                """)
                
                if not script_content:
                    # Try alternative selectors quickly
                    script_content = await page.evaluate("""
                        () => {
                            const scripts = document.querySelectorAll('script');
                            for (const script of scripts) {
                                const text = script.textContent;
                                if (text && text.includes('__DEFAULT_SCOPE__')) {
                                    return text;
                                }
                            }
                            return null;
                        }
                    """)
                
                if not script_content:
                    raise ValueError("Could not find TikTok data script")
                
                # Use orjson for ultra-fast JSON parsing
                data = orjson.loads(script_content)
                
                # OPTIMIZED data extraction with early returns
                video_data = self._extract_video_data_fast(data)
                
                if not video_data:
                    raise ValueError("Could not extract video data - video might be private or deleted")
                
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"⚡ TikTok extraction completed in {elapsed:.2f}s")
                
                return {
                    'video_data': video_data,
                    'comments': [],  # Skip comments for speed
                    'extraction_time': elapsed
                }
                
            finally:
                await page.close()
                
        except Exception as e:
            logger.error(f"Error in optimized TikTok extraction for {url}: {e}")
            raise ValueError(f"TikTok metadata extraction failed: {str(e)}")
    
    def _extract_video_data_fast(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ultra-fast video data extraction with optimized paths"""
        try:
            if "__DEFAULT_SCOPE__" not in data:
                return None
            
            scope_data = data["__DEFAULT_SCOPE__"]
            
            # Try most common paths first for speed
            paths = [
                ("webapp.video-detail", "itemInfo", "itemStruct"),
                ("webapp.a-b", None, "itemStruct"),
            ]
            
            for path_parts in paths:
                try:
                    current = scope_data
                    for part in path_parts:
                        if part is None:
                            # Handle webapp.a-b case
                            for key, value in current.items():
                                if isinstance(value, dict) and "itemStruct" in value:
                                    return value["itemStruct"]
                            break
                        current = current[part]
                    
                    if current and isinstance(current, dict):
                        return current
                        
                except (KeyError, TypeError):
                    continue
            
            return None
            
        except Exception as e:
            logger.warning(f"Fast extraction failed: {e}")
            return None
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """OPTIMIZED metadata normalization"""
        try:
            video_data = raw_data.get('video_data', {})
            
            # Fast extraction with defaults
            video_info = video_data.get('video', {})
            author_info = video_data.get('author', {})
            stats = video_data.get('stats', {})
            
            desc = video_data.get('desc', '') or ''
            hashtags = re.findall(r'#(\w+)', desc) if desc else []
            clean_desc = re.sub(r'#\w+', '', desc).strip() if desc else ''
            
            # Optimized title selection
            title = clean_desc[:500] if clean_desc else (' '.join([f'#{tag}' for tag in hashtags[:5]]) if hashtags else None)
            
            # Fast URL extraction
            thumbnail_url = None
            if video_info:
                thumbnail_url = video_info.get('cover') or video_info.get('originCover') or video_info.get('dynamicCover')
            
            # Optimized datetime parsing
            published_at = None
            create_time = video_data.get('createTime')
            if create_time:
                try:
                    published_at = datetime.fromtimestamp(int(create_time))
                except:
                    pass
            
            # Streamlined response
            normalized = {
                "title": title,
                "author": (author_info.get('uniqueId', '') or '').strip()[:255] or None,
                "thumbnail_url": thumbnail_url,
                "description": desc[:2000] if desc else None,
                "published_at": published_at,
                "platform_specific": {
                    "video_id": str(video_data.get('id', '')).strip(),
                    "author_id": str(author_info.get('id', '')).strip() or None,
                    "author_name": (author_info.get('nickname', '') or '').strip() or None,
                    "duration_seconds": video_info.get('duration'),
                    "view_count": stats.get('playCount', 0),
                    "like_count": stats.get('diggCount', 0),
                    "comment_count": stats.get('commentCount', 0),
                    "share_count": stats.get('shareCount', 0),
                    "hashtags": hashtags,
                    "engagement_rate": self._calculate_engagement_rate_fast(stats)
                }
            }
            
            if not normalized["platform_specific"]["video_id"]:
                raise ValueError("No video ID found in metadata")
            
            return normalized
            
        except Exception as e:
            logger.error(f"Error normalizing TikTok metadata: {e}")
            raise ValueError(f"Failed to normalize metadata: {str(e)}")
    
    def _calculate_engagement_rate_fast(self, stats: Dict[str, Any]) -> float:
        """Ultra-fast engagement rate calculation"""
        try:
            views = stats.get('playCount', 0) or 0
            if views == 0:
                return 0.0
            
            engagement = (stats.get('diggCount', 0) or 0) + (stats.get('commentCount', 0) or 0) + (stats.get('shareCount', 0) or 0)
            return round((engagement / views) * 100, 2)
        except:
            return 0.0
    
    async def __aenter__(self):
        await self._ensure_initialized()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client and self._initialized:
            try:
                await self.client.aclose()
            except:
                pass
```

Now let me create the ultra-optimized YouTube ingestor:

```python:api/ingestors/youtube_optimized.py
import re
import logging
import asyncio
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import yt_dlp
import orjson
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer
from .base import BaseIngestor

logger = logging.getLogger(__name__)

# Ultra-fast caching
cache = Cache(Cache.MEMORY, serializer=PickleSerializer(), ttl=3600)  # 1 hour cache

class YouTubeOptimizedIngestor(BaseIngestor):
    
    _ydl_instance = None
    _ydl_lock = asyncio.Lock()
    
    @classmethod
    async def _get_optimized_ydl(cls):
        """Get shared yt-dlp instance with ultra-performance settings"""
        async with cls._ydl_lock:
            if cls._ydl_instance is None:
                cls._ydl_instance = yt_dlp.YoutubeDL({
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                    'no_check_formats': True,
                    'no_check_certificate': True,
                    'socket_timeout': 5,  # Ultra-fast timeout
                    'retries': 1,  # Minimal retries for speed
                    'fragment_retries': 1,
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
                    'format': 'worst',  # Fastest format selection
                    'noplaylist': True,
                    'playlistend': 1,
                    'geo_bypass': False,
                    'call_home': False,
                    'check_formats': False,
                    'cachedir': False,  # Disable caching for speed
                    'no_check_formats': True,
                    'prefer_insecure': True,  # Skip HTTPS verification for speed
                })
        return cls._ydl_instance
    
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
                'm.youtube.com' in parsed.netloc or
                'www.youtube.com' in parsed.netloc
            )
        except:
            return False
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Ultra-fast video ID extraction"""
        try:
            if 'youtu.be' in url:
                return url.split('/')[-1].split('?')[0]
            
            if 'youtube.com' in url:
                if 'v=' in url:
                    return url.split('v=')[1].split('&')[0]
                elif '/embed/' in url:
                    return url.split('/embed/')[1].split('?')[0]
            
            return None
        except:
            return None
    
    @cached(ttl=3600, cache=cache, key_builder=lambda f, self, url: f"youtube_{hashlib.md5(url.encode()).hexdigest()}")
    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        """ULTRA-FAST YouTube metadata extraction"""
        if not self.can_handle(url):
            raise ValueError(f"Cannot handle URL: {url}")
        
        try:
            logger.info(f"🚀 ULTRA-FAST YouTube extraction from: {url}")
            start_time = asyncio.get_event_loop().time()
            
            ydl = await self._get_optimized_ydl()
            
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None, 
                lambda: ydl.extract_info(url, download=False)
            )
            
            if not info:
                raise ValueError("No metadata extracted - video may be private or deleted")
            
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"⚡ YouTube extraction completed in {elapsed:.2f}s")
            
            # Add timing info
            info['extraction_time'] = elapsed
            return info
            
        except yt_dlp.DownloadError as e:
            error_msg = str(e)
            logger.error(f"yt-dlp error for {url}: {e}")
            
            # Fast error classification
            if any(keyword in error_msg.lower() for keyword in ["private", "unavailable", "removed", "blocked"]):
                if "private" in error_msg.lower():
                    raise ValueError("This video is private and cannot be accessed")
                elif "unavailable" in error_msg.lower():
                    raise ValueError("This video is unavailable or has been removed")
                elif "blocked" in error_msg.lower():
                    raise ValueError("This video is blocked in your region")
            
            raise ValueError(f"Could not access video: {error_msg}")
            
        except Exception as e:
            logger.error(f"Unexpected error extracting YouTube metadata for {url}: {e}")
            raise ValueError(f"Metadata extraction failed: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """OPTIMIZED YouTube metadata normalization"""
        try:
            # Fast datetime parsing
            published_at = None
            if raw_data.get('upload_date'):
                try:
                    upload_date_str = str(raw_data['upload_date'])
                    published_at = datetime.strptime(upload_date_str, '%Y%m%d')
                except:
                    pass
            
            # Optimized thumbnail selection
            thumbnail_url = None
            if raw_data.get('thumbnails'):
                thumbnails = raw_data['thumbnails']
                # Get highest quality thumbnail quickly
                thumbnail_url = thumbnails[-1].get('url') if thumbnails else None
            
            # Fallback thumbnail
            video_id = raw_data.get('id', '').strip()
            if not thumbnail_url and video_id:
                thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            
            # Fast description processing
            description = raw_data.get('description', '')
            if description and len(description) > 2000:
                description = description[:2000] + '...'
            
            # Optimized tags processing
            tags = raw_data.get('tags', [])
            if tags and isinstance(tags, list):
                tags = [str(tag)[:50] for tag in tags[:15]]  # Limit for performance
            else:
                tags = []
            
            # Streamlined response
            normalized = {
                "title": (raw_data.get('title', '') or '').strip()[:500] or None,
                "author": ((raw_data.get('uploader') or raw_data.get('channel', '')) or '').strip()[:255] or None,
                "thumbnail_url": thumbnail_url,
                "description": description or None,
                "published_at": published_at,
                "platform_specific": {
                    "video_id": video_id,
                    "channel_id": (raw_data.get('channel_id', '') or '').strip() or None,
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
```

Now let me create the optimized registry with background processing:

```python:api/ingestors/registry_optimized.py
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

# Use optimized ingestors
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

# Background processing pool
PROCESSING_POOL = ThreadPoolExecutor(max_workers=10, thread_name_prefix="video_processor")

def get_ingestor(url: str) -> Optional[BaseIngestor]:
    """Get optimized ingestor for URL"""
    for ingestor in OPTIMIZED_INGESTORS:
        if ingestor.can_handle(url):
            return ingestor
    return None

async def add_bookmark_ultra_fast(url: str, user_id: int, db: Session = None) -> Dict[str, Any]:
    """ULTRA-FAST bookmark processing with optimizations"""
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
        
        # Check for existing bookmark FIRST for speed
        existing = db.query(Bookmark).filter(Bookmark.url == url, Bookmark.user_id == user_id).first()
        
        if existing:
            logger.info(f"Bookmark already exists for URL: {url}")
            raise ValueError("You already have this link bookmarked! Check your existing bookmarks to find it.")
        
        # Process with optimized ingestor
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
    """Create new bookmark with ULTRA optimizations"""
    try:
        # Extract metadata with optimized ingestor
        raw_data = await ingestor.extract_metadata(url)
        normalized = ingestor.normalize_metadata(raw_data)
        
        # Create bookmark with optimized data
        bookmark = Bookmark(
            platform=ingestor.platform,
            url=url,
            title=normalized.get("title"),
            author=normalized.get("author"),
            thumbnail_url=normalized.get("thumbnail_url"),
            description=normalized.get("description"),
            published_at=normalized.get("published_at"),
            user_id=user_id,
            raw=json.dumps(raw_data, default=str)  # Use default serialization for speed
        )
        
        db.add(bookmark)
        db.flush()
        
        # Add platform-specific details
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
    """Optimized YouTube details creation"""
    details = YouTubeDetails(
        bookmark_id=bookmark_id,
        video_id=platform_data["video_id"],
        channel_id=platform_data.get("channel_id"),
        duration_seconds=platform_data.get("duration_seconds"),
        view_count=platform_data.get("view_count"),
        like_count=platform_data.get("like_count"),
        tags=json.dumps(platform_data.get("tags", [])),
        extra=json.dumps({})  # Minimal extra data for speed
    )
    db.add(details)

def _format_bookmark_response_optimized(bookmark: Bookmark) -> Dict[str, Any]:
    """Optimized bookmark response formatting"""
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
    
    # Add platform-specific metadata
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
    """Fast basic bookmark creation for unsupported platforms"""
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
    """Ultra-fast platform detection"""
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

# Background processing function
async def process_bookmark_background(url: str, user_id: int) -> Dict[str, Any]:
    """Process bookmark in background for maximum UI responsiveness"""
    try:
        return await add_bookmark_ultra_fast(url, user_id)
    except Exception as e:
        logger.error(f"Background processing failed for {url}: {e}")
        raise
```

Now let me update the main API to use the optimized system:

```python:api/main_optimized.py
from fastapi import FastAPI, HTTPException, Depends, status, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import timedelta
from typing import Optional, List
import logging
import json
import asyncio

from db import get_db, init_db
from models import User, Bookmark
from ingestors.registry_optimized import add_bookmark_ultra_fast, process_bookmark_background
from email_validation import validate_email_comprehensive
from auth import (
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

app = FastAPI(title="Sava ULTRA-FAST Bookmark API", version="3.0.0-OPTIMIZED")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3002", "http://127.0.0.1:3002"],
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

@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("🚀 Sava ULTRA-FAST API started successfully")

@app.get("/")
def health():
    return {"message": "Sava ULTRA-FAST API is running ⚡", "version": "3.0.0-OPTIMIZED"}

# ... (keep existing auth endpoints unchanged) ...

@app.post("/api/bookmarks/youtube")
async def create_youtube_bookmark_ultra_fast(
    bookmark_data: YouTubeBookmarkIn, 
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """ULTRA-FAST YouTube bookmark creation"""
    try:
        url = str(bookmark_data.url)
        
        if not ("youtube.com" in url.lower() or "youtu.be" in url.lower()):
            raise HTTPException(
                status_code=422,
                detail="URL must be a valid YouTube URL"
            )
        
        logger.info(f"🚀 ULTRA-FAST YouTube bookmark processing: {url}")
        
        # Use optimized processing
        result = await add_bookmark_ultra_fast(url, current_user["id"], db)
        
        logger.info(f"⚡ Successfully created OPTIMIZED YouTube bookmark: {result.get('title', 'Unknown')}")
        return result
        
    except ValueError as e:
        logger.error(f"Validation error creating optimized YouTube bookmark: {e}")
        error_msg = str(e)
        if "already" in error_msg.lower() and "bookmarked" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        else:
            raise HTTPException(status_code=422, detail=error_msg)
    except Exception as e:
        logger.error(f"Unexpected error creating optimized YouTube bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/bookmarks")
async def create_bookmark_ultra_fast(
    b: BookmarkIn, 
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """ULTRA-FAST bookmark creation for all platforms"""
    try:
        url = str(b.url)
        
        logger.info(f"🚀 ULTRA-FAST bookmark processing: {url}")
        
        # Use optimized processing
        result = await add_bookmark_ultra_fast(url, current_user["id"], db)
        
        # Update with user-provided data if available
        bookmark = db.query(Bookmark).filter(Bookmark.id == result["id"]).first()
        if bookmark:
            if b.title:
                bookmark.title = b.title
                result["title"] = b.title
            if b.note:
                bookmark.note = b.note
                result["note"] = b.note
            if b.title or b.note:
                db.commit()
        
        logger.info(f"⚡ Successfully created OPTIMIZED bookmark: {result.get('title', 'Unknown')}")
        return result
        
    except ValueError as e:
        error_msg = str(e)
        if "already" in error_msg.lower() and "bookmarked" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        else:
            raise HTTPException(status_code=422, detail=error_msg)
    except Exception as e:
        logger.error(f"Error creating optimized bookmark: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ... (keep other endpoints unchanged) ...
```

Finally, let me update the requirements.txt with the performance dependencies:

```12:18:api/requirements.txt
# ... existing code ...
httpx>=0.25.0
redis>=4.5.0
aiocache>=0.12.0
ujson>=5.7.0
orjson>=3.8.0
```

Now let me create a performance monitoring script to show you the speed improvements:

```python:api/performance_test.py
#!/usr/bin/env python3
"""
Performance testing script for ULTRA-FAST video processing
"""

import asyncio
import time
import logging
from typing import List, Dict, Any
from ingestors.registry_optimized import add_bookmark_ultra_fast
from ingestors.youtube_optimized import YouTubeOptimizedIngestor
from ingestors.tiktok_optimized import TikTokOptimizedIngestor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Test URLs
YOUTUBE_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/jNQXAC9IVRw",
    "https://www.youtube.com/watch?v=9bZkp7q19f0",
]

TIKTOK_URLS = [
    "https://www.tiktok.com/@username/video/1234567890",
    "https://vm.tiktok.com/ZMeFbDnwH/",
    "https://www.tiktok.com/@creator/video/9876543210",
]

async def benchmark_platform(urls: List[str], platform: str) -> Dict[str, Any]:
    """Benchmark a specific platform"""
    logger.info(f"🚀 Benchmarking {platform} with {len(urls)} URLs")
    
    start_time = time.time()
    results = []
    
    if platform.lower() == "youtube":
        ingestor = YouTubeOptimizedIngestor()
    elif platform.lower() == "tiktok":
        ingestor = TikTokOptimizedIngestor()
    else:
        logger.error(f"Unknown platform: {platform}")
        return {}
    
    for url in urls:
        try:
            url_start = time.time()
            
            # Test metadata extraction
            metadata = await ingestor.extract_metadata(url)
            normalized = ingestor.normalize_metadata(metadata)
            
            url_time = time.time() - url_start
            
            results.append({
                "url": url,
                "time": url_time,
                "title": normalized.get("title", "Unknown")[:50],
                "success": True
            })
            
            logger.info(f"⚡ {platform} URL processed in {url_time:.2f}s: {normalized.get('title', 'Unknown')[:30]}...")
            
        except Exception as e:
            url_time = time.time() - url_start
            results.append({
                "url": url,
                "time": url_time,
                "error": str(e),
                "success": False
            })
            logger.error(f"❌ {platform} URL failed in {url_time:.2f}s: {str(e)[:50]}...")
    
    total_time = time.time() - start_time
    successful = [r for r in results if r["success"]]
    
    return {
        "platform": platform,
        "total_urls": len(urls),
        "successful": len(successful),
        "failed": len(urls) - len(successful),
        "total_time": total_time,
        "avg_time": total_time / len(urls),
        "success_rate": len(successful) / len(urls) * 100,
        "results": results
    }

async def run_performance_test():
    """Run comprehensive performance test"""
    logger.info("🚀 Starting ULTRA-FAST Video Processing Performance Test")
    logger.info("=" * 60)
    
    youtube_results = await benchmark_platform(YOUTUBE_URLS, "YouTube")
    
    tiktok_results = await benchmark_platform(TIKTOK_URLS, "TikTok")
    
    # Print results
    logger.info("\n" + "=" * 60)
    logger.info("📊 PERFORMANCE RESULTS")
    logger.info("=" * 60)
    
    for results in [youtube_results, tiktok_results]:
        if results:
            logger.info(f"\n🎯 {results['platform'].upper()} RESULTS:")
            logger.info(f"   Total URLs: {results['total_urls']}")
            logger.info(f"   Successful: {results['successful']}")
            logger.info(f"   Failed: {results['failed']}")
            logger.info(f"   Success Rate: {results['success_rate']:.1f}%")
            logger.info(f"   Total Time: {results['total_time']:.2f}s")
            logger.info(f"   Average Time: {results['avg_time']:.2f}s per URL")
            logger.info(f"   Speed: {results['total_urls']/results['total_time']:.2f} URLs/second")
    
    logger.info("\n⚡ ULTRA-FAST Processing Complete!")

if __name__ == "__main__":
    asyncio.run(run_performance_test())
```