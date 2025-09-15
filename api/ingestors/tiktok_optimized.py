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
        if not self._initialized:
            try:
                limits = httpx.Limits(
                    max_keepalive_connections=50, 
                    max_connections=200,
                    keepalive_expiry=30
                )
                self.client = httpx.AsyncClient(
                    timeout=httpx.Timeout(8.0, connect=3.0), 
                    limits=limits,
                    http2=True, 
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
        return f"tiktok_metadata_{hashlib.md5(url.encode()).hexdigest()}"
    
    @cached(ttl=1800, cache=cache, key_builder=lambda f, self, url: f"tiktok_{hashlib.md5(url.encode()).hexdigest()}")
    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        await self._ensure_initialized()
        
        try:
            logger.info(f"🚀 ULTRA-FAST TikTok extraction from: {url}")
            start_time = asyncio.get_event_loop().time()
            
            browser, context = await self._get_shared_browser()
            page = await context.new_page()
            
            try:
                await page.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,css,woff,woff2,ttf,eot,mp4,webm,mp3,wav}", lambda route: route.abort())
                await page.route("**/analytics/**", lambda route: route.abort())
                await page.route("**/ads/**", lambda route: route.abort())
                await page.route("**/tracking/**", lambda route: route.abort())
                await page.route("**/metrics/**", lambda route: route.abort())
                await page.route("**/beacon/**", lambda route: route.abort())
                await page.route("**/collect/**", lambda route: route.abort())
                
                await page.goto(url, wait_until='domcontentloaded', timeout=8000)
                
                await page.wait_for_timeout(500)
                
                script_content = await page.evaluate("""
                    () => {
                        const script = document.querySelector('script[id="__UNIVERSAL_DATA_FOR_REHYDRATION__"]');
                        return script ? script.textContent : null;
                    }
                """)
                
                if not script_content:
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
                
                data = orjson.loads(script_content)
                
                video_data = self._extract_video_data_fast(data)
                
                if not video_data:
                    raise ValueError("Could not extract video data - video might be private or deleted")
                
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"⚡ TikTok extraction completed in {elapsed:.2f}s")
                
                return {
                    'video_data': video_data,
                    'comments': [],  
                    'extraction_time': elapsed
                }
                
            finally:
                await page.close()
                
        except Exception as e:
            logger.error(f"Error in optimized TikTok extraction for {url}: {e}")
            raise ValueError(f"TikTok metadata extraction failed: {str(e)}")
    
    def _extract_video_data_fast(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            if "__DEFAULT_SCOPE__" not in data:
                return None
            
            scope_data = data["__DEFAULT_SCOPE__"]
            
            paths = [
                ("webapp.video-detail", "itemInfo", "itemStruct"),
                ("webapp.a-b", None, "itemStruct"),
            ]
            
            for path_parts in paths:
                try:
                    current = scope_data
                    for part in path_parts:
                        if part is None:
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
        try:
            video_data = raw_data.get('video_data', {})
            
            video_info = video_data.get('video', {})
            author_info = video_data.get('author', {})
            stats = video_data.get('stats', {})
            
            desc = video_data.get('desc', '') or ''
            hashtags = re.findall(r'#(\w+)', desc) if desc else []
            clean_desc = re.sub(r'#\w+', '', desc).strip() if desc else ''
            
            title = clean_desc[:500] if clean_desc else (' '.join([f'#{tag}' for tag in hashtags[:5]]) if hashtags else None)
            
            thumbnail_url = None
            if video_info:
                thumbnail_url = video_info.get('cover') or video_info.get('originCover') or video_info.get('dynamicCover')
            
            published_at = None
            create_time = video_data.get('createTime')
            if create_time:
                try:
                    published_at = datetime.fromtimestamp(int(create_time))
                except:
                    pass
            
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