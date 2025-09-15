import re
import json
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from urllib.parse import urlparse
import httpx
from playwright.async_api import async_playwright
from .base import BaseIngestor

logger = logging.getLogger(__name__)

class TikTokIngestor(BaseIngestor):
    
    def __init__(self):
        self.client = None
        self._initialized = False
    
    async def _ensure_initialized(self):
        """Initialize HTTP client if not already done"""
        if not self._initialized:
            try:
                # Ultra-fast HTTP client with connection pooling
                limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
                self.client = httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0, connect=5.0),  # Faster timeouts
                    limits=limits,
                    http2=True,  # Use HTTP/2 for better performance
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
                logger.info("TikTok HTTP client initialized successfully with connection pooling")
            except Exception as e:
                logger.error(f"Failed to initialize TikTok client: {e}")
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
                't.tiktok.com' in parsed.netloc
            )
        except:
            return False
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from TikTok URL"""
        try:
            # Pattern for standard TikTok video URLs
            video_pattern = r'tiktok\.com/@[^/]+/video/(\d+)'
            match = re.search(video_pattern, url)
            if match:
                return match.group(1)
            
            # Pattern for short URLs
            short_pattern = r'vm\.tiktok\.com/([A-Za-z0-9]+)'
            match = re.search(short_pattern, url)
            if match:
                return match.group(1)
            
            return None
        except:
            return None
    
    def extract_username(self, url: str) -> Optional[str]:
        """Extract username from TikTok URL"""
        try:
            username_pattern = r'tiktok\.com/@([^/]+)'
            match = re.search(username_pattern, url)
            if match:
                return match.group(1)
            return None
        except:
            return None
    
    async def extract_metadata(self, url: str) -> Dict[str, Any]:
        """Extract TikTok metadata using web scraping with hidden JSON data"""
        await self._ensure_initialized()
        
        try:
            logger.info(f"Extracting TikTok metadata from: {url}")
            
            # Use Playwright for JavaScript rendering - ULTRA FAST MODE
            async with async_playwright() as p:
                browser = await p.chromium.launch(
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
                        '--disable-features=TranslateUI',
                        '--disable-ipc-flooding-protection',
                        '--disable-web-security',
                        '--disable-extensions',
                        '--disable-default-apps',
                        '--no-first-run',
                        '--no-default-browser-check',
                        '--disable-sync',
                        '--disable-background-networking'
                    ]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    java_script_enabled=True,
                    ignore_https_errors=True
                )
                page = await context.new_page()
                
                # Block unnecessary resources for speed
                await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf}", lambda route: route.abort())
                await page.route("**/analytics/**", lambda route: route.abort())
                await page.route("**/ads/**", lambda route: route.abort())
                
                # Navigate to the page - FASTEST SETTINGS
                await page.goto(url, wait_until='domcontentloaded', timeout=10000)
                
                # Minimal wait - just enough for script to load
                await page.wait_for_timeout(800)
                
                # Extract the hidden JSON data
                script_content = await page.evaluate("""
                    () => {
                        const script = document.querySelector('script[id="__UNIVERSAL_DATA_FOR_REHYDRATION__"]');
                        return script ? script.textContent : null;
                    }
                """)
                
                await browser.close()
                
                if not script_content:
                    raise ValueError("Could not find TikTok data script")
                
                # Parse the JSON data
                data = json.loads(script_content)
                
                # Extract video data from the nested structure
                video_data = None
                
                # Debug: log the available keys
                logger.info(f"Available top-level keys: {list(data.keys())}")
                
                try:
                    # Try different possible paths for video data
                    if "__DEFAULT_SCOPE__" in data:
                        scope_data = data["__DEFAULT_SCOPE__"]
                        logger.info(f"Available scope keys: {list(scope_data.keys())}")
                        
                        # Try webapp.video-detail path
                        if "webapp.video-detail" in scope_data:
                            video_detail = scope_data["webapp.video-detail"]
                            if "itemInfo" in video_detail:
                                video_data = video_detail["itemInfo"]["itemStruct"]
                                logger.info("Found video data via webapp.video-detail path")
                        
                        # Try webapp.a-b path
                        elif "webapp.a-b" in scope_data:
                            ab_data = scope_data["webapp.a-b"]
                            for key, value in ab_data.items():
                                if isinstance(value, dict) and "itemStruct" in value:
                                    video_data = value["itemStruct"]
                                    logger.info(f"Found video data via webapp.a-b.{key} path")
                                    break
                        
                        # Try other common paths
                        for key in scope_data.keys():
                            if not video_data and isinstance(scope_data[key], dict):
                                if "itemInfo" in scope_data[key]:
                                    if "itemStruct" in scope_data[key]["itemInfo"]:
                                        video_data = scope_data[key]["itemInfo"]["itemStruct"]
                                        logger.info(f"Found video data via {key}.itemInfo.itemStruct path")
                                        break
                                elif "itemStruct" in scope_data[key]:
                                    video_data = scope_data[key]["itemStruct"]
                                    logger.info(f"Found video data via {key}.itemStruct path")
                                    break
                
                except KeyError as e:
                    logger.warning(f"KeyError while extracting video data: {e}")
                
                if not video_data:
                    # Check if it's a "video doesn't exist" error
                    if "__DEFAULT_SCOPE__" in data and "webapp.video-detail" in data["__DEFAULT_SCOPE__"]:
                        video_detail = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]
                        if isinstance(video_detail, dict):
                            status_code = video_detail.get("statusCode")
                            status_msg = video_detail.get("statusMsg", "")
                            
                            if status_code == 10204 or "doesn't exist" in status_msg.lower():
                                raise ValueError("This TikTok video has been deleted, made private, or doesn't exist")
                            elif status_code and status_msg:
                                raise ValueError(f"TikTok error: {status_msg} (code: {status_code})")
                    
                    # Save the raw data for debugging other cases
                    with open("tiktok_debug_data.json", "w") as f:
                        json.dump(data, f, indent=2, default=str)
                    logger.error("Could not extract video data. Raw data saved to tiktok_debug_data.json")
                    raise ValueError("Could not extract video data from TikTok page - the video might be private, deleted, or region-blocked")
                
                logger.info(f"Successfully extracted TikTok metadata for: {video_data.get('desc', 'Unknown')[:50]}...")
                return {
                    'video_data': video_data,
                    'comments': []  # Comments would require additional API calls
                }
                
        except Exception as e:
            logger.error(f"Error extracting TikTok metadata for {url}: {e}")
            raise ValueError(f"TikTok metadata extraction failed: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize TikTok data for AI-ready format"""
        try:
            video_data = raw_data.get('video_data', {})
            comments_data = raw_data.get('comments', [])
            
            # Extract video info
            video_info = video_data.get('video', {})
            author_info = video_data.get('author', {})
            stats = video_data.get('stats', {})
            
            # Extract hashtags from description
            desc = video_data.get('desc', '') or ''
            hashtags = re.findall(r'#(\w+)', desc)
            
            # Clean description (remove hashtags for clean text)
            clean_desc = re.sub(r'#\w+', '', desc).strip()
            
            # Determine title priority: clean_desc > hashtags > user notes (handled later)
            title = None
            if clean_desc:
                title = clean_desc[:500]
            elif hashtags:
                # Use hashtags as title if no clean description
                title = ' '.join([f'#{tag}' for tag in hashtags[:5]])  # Limit to first 5 hashtags
            # If neither exists, title will be None and handled by the registry with user notes
            
            # Extract video URLs
            video_url = None
            thumbnail_url = None
            if video_info:
                # Try to get video URL (may require additional processing)
                video_url = video_info.get('playAddr') or video_info.get('downloadAddr')
                thumbnail_url = video_info.get('cover') or video_info.get('originCover')
            
            # Process comments for AI
            processed_comments = []
            for comment in comments_data[:20]:  # Limit for performance
                comment_text = comment.get('text', '').strip()
                if comment_text:
                    processed_comments.append({
                        'text': comment_text,
                        'author': comment.get('user', {}).get('uniqueId', 'unknown'),
                        'likes': comment.get('diggCount', 0),
                        'created_at': comment.get('createTime')
                    })
            
            # Create published_at datetime
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
                    # AI-ready data
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
                        }
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
        """Calculate engagement rate for ranking/trending analysis"""
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
        if self.client and self._initialized:
            try:
                await self.client.aclose()
            except:
                pass 