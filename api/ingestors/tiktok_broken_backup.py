import asyncio
import logging
import re
import json
import requests
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse
from datetime import datetime
from bs4 import BeautifulSoup

from .base import BaseIngestor

logger = logging.getLogger(__name__)

class TikTokIngestor(BaseIngestor):
    def __init__(self):
        super().__init__()

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
        logger.info(f"Extracting TikTok metadata from: {url}")
        
        try:
            video_id = self._extract_video_id(url)
            username = self._extract_username(url)
            
            if not video_id:
                logger.error(f"Could not extract video ID from URL: {url}")
                return self._fallback_extraction(url, username)
            
            caption = self._get_video_caption_aggressive(url, video_id)
            
            if caption and caption.strip():
                title = caption.strip()
                title = self._clean_title(title)
            else:
                title = f"TikTok video by @{username}" if username else "TikTok video"
            
            try:
                page_metadata = self._scrape_page_metadata(url)
                thumbnail_url = page_metadata.get('thumbnail', '') if page_metadata else ''
            except Exception as e:
                logger.warning(f"Could not scrape page metadata: {e}")
                thumbnail_url = ''
            
            if not thumbnail_url:
                thumbnail_url = self._generate_thumbnail_url(video_id)
            
            hashtags = self._extract_hashtags(caption or '')
            
            return {
                'platform': 'tiktok',
                'url': url,
                'title': title,
                'author': username or 'unknown',
                'thumbnail_url': thumbnail_url,
                'description': caption or '',
                'published_at': None,
                'raw': {
                    'id': video_id,
                    'hashtags': hashtags,
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
                        'username': username or 'unknown',
                        'nickname': username or 'unknown',
                    }
                }
            }
                
        except Exception as e:
            logger.error(f"Error extracting TikTok metadata: {e}")
            return self._fallback_extraction(url)

    def _get_video_caption_aggressive(self, url: str, video_id: str) -> Optional[str]:
        
        try:
            caption = self._scrape_caption_from_page(url)
            if caption:
                logger.info(f"Found caption via page scraping: {caption[:100]}...")
                return caption
        except Exception as e:
            logger.warning(f"Page scraping failed: {e}")
        
        try:
            caption = self._scrape_caption_from_json_ld(url)
            if caption:
                logger.info(f"Found caption via JSON-LD: {caption[:100]}...")
                return caption
        except Exception as e:
            logger.warning(f"JSON-LD scraping failed: {e}")
        
        try:
            caption = self._scrape_caption_from_meta_tags(url)
            if caption:
                logger.info(f"Found caption via meta tags: {caption[:100]}...")
                return caption
        except Exception as e:
            logger.warning(f"Meta tag scraping failed: {e}")
        
        try:
            caption = self._scrape_caption_with_regex(url)
            if caption:
                logger.info(f"Found caption via regex: {caption[:100]}...")
                return caption
        except Exception as e:
            logger.warning(f"Regex scraping failed: {e}")
        
        return None

    def _scrape_caption_from_page(self, url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                selectors = [
                    'meta[property="og:description"]',
                    'meta[name="description"]',
                    '[data-e2e="video-desc"]',
                    '.video-meta-caption',
                    '.video-desc',
                    '[class*="desc"]',
                    '[class*="caption"]'
                ]
                
                for selector in selectors:
                    element = soup.select_one(selector)
                    if element:
                        content = element.get('content') or element.get_text()
                        if content and content.strip() and content != 'TikTok':
                            return content.strip()
                
        except Exception as e:
            logger.warning(f"Error scraping caption from page: {e}")
        
        return None

    def _scrape_caption_from_json_ld(self, url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                json_scripts = soup.find_all('script', type='application/ld+json')
                for script in json_scripts:
                    try:
                        data = json.loads(script.string)
                        if isinstance(data, dict):
                            for field in ['description', 'caption', 'text', 'content']:
                                if field in data and data[field]:
                                    return str(data[field]).strip()
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    for field in ['description', 'caption', 'text', 'content']:
                                        if field in item and item[field]:
                                            return str(item[field]).strip()
                    except json.JSONDecodeError:
                        continue
                
        except Exception as e:
            logger.warning(f"Error scraping caption from JSON-LD: {e}")
        
        return None

    def _scrape_caption_from_meta_tags(self, url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                meta_patterns = [
                    'meta[property="og:description"]',
                    'meta[name="description"]',
                    'meta[property="twitter:description"]',
                    'meta[name="twitter:description"]'
                ]
                
                for pattern in meta_patterns:
                    meta = soup.select_one(pattern)
                    if meta and meta.get('content'):
                        content = meta.get('content').strip()
                        if content and content != 'TikTok' and len(content) > 10:
                            return content
                
        except Exception as e:
            logger.warning(f"Error scraping caption from meta tags: {e}")
        
        return None

    def _scrape_caption_with_regex(self, url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                content = response.text
                
                patterns = [
                    r'"desc":"([^"]+)"',
                    r'"description":"([^"]+)"',
                    r'"caption":"([^"]+)"',
                    r'"text":"([^"]+)"',
                    r'"content":"([^"]+)"',
                    r'<meta property="og:description" content="([^"]+)"',
                    r'<meta name="description" content="([^"]+)"',
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, content)
                    for match in matches:
                        if match and match.strip() and match != 'TikTok' and len(match) > 10:
                            decoded = match.replace('\\"', '"').replace('\\n', '\n').replace('\\/', '/')
                            return decoded.strip()
                
        except Exception as e:
            logger.warning(f"Error scraping caption with regex: {e}")
        
        return None

    def _clean_title(self, title: str) -> str:
        if not title:
            return title
        
        hashtags = re.findall(r'#\w+', title)
        if len(hashtags) > 5:
            title = re.sub(r'#\w+', '', title, flags=re.IGNORECASE)
            title = title.strip()
            title += ' ' + ' '.join(hashtags[:3])
        
        mentions = re.findall(r'@\w+', title)
        if len(mentions) > 3:
            title = re.sub(r'@\w+', '', title, flags=re.IGNORECASE)
            title = title.strip()
            title += ' ' + ' '.join(mentions[:2])
        
        title = re.sub(r'\s+', ' ', title).strip()
        
        if len(title) > 200:
            title = title[:197] + "..."
        
        return title

    def _scrape_page_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                metadata = {}
                
                img_selectors = [
                    'meta[property="og:image"]',
                    'meta[name="twitter:image"]',
                    'img[data-e2e="video-cover"]',
                    'img[class*="cover"]'
                ]
                
                for selector in img_selectors:
                    element = soup.select_one(selector)
                    if element:
                        img_url = element.get('content') or element.get('src')
                        if img_url and img_url.startswith('http'):
                            metadata['thumbnail'] = img_url
                            break
                
                return metadata if metadata else None
                
        except Exception as e:
            logger.warning(f"Error scraping page metadata: {e}")
        
        return None

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

    def _extract_username(self, url: str) -> Optional[str]:
        try:
            match = re.search(r'@([^/]+)', url)
            if match:
                return match.group(1)
        except Exception as e:
            logger.error(f"Error extracting username: {e}")
        
        return None

    def _extract_hashtags(self, description: str) -> List[str]:
        if not description:
            return []
        
        hashtags = re.findall(r'#\w+', description)
        return hashtags

    def _generate_thumbnail_url(self, video_id: str) -> str:
        if video_id:
            possible_urls = [
                f"https://p16-sign-va.tiktokcdn-us.com/obj/tos-useast2a-p-0068-tx/{video_id}",
                f"https://p16-sign.tiktokcdn-us.com/obj/tos-useast2a-p-0068-tx/{video_id}",
                f"https://p16-sign-va.tiktokcdn.com/obj/tos-useast2a-p-0068-tx/{video_id}",
            ]
            return possible_urls[0] 
        
        return ""

    def _fallback_extraction(self, url: str, username: Optional[str] = None, video_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            if not username:
                username = self._extract_username(url)
            
            if not video_id:
                video_id = self._extract_video_id(url)
            
            thumbnail_url = self._generate_thumbnail_url(video_id) if video_id else ""
            
            if username:
                title = f"TikTok video by @{username}"
            else:
                title = "TikTok video"
            
            return {
                'platform': 'tiktok',
                'url': url,
                'title': title,
                'author': username or 'unknown',
                'thumbnail_url': thumbnail_url,
                'description': '',
                'published_at': None,
                'raw': {
                    'id': video_id or '',
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
                        'username': username or 'unknown',
                        'nickname': username or 'unknown',
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
