from .base import BaseIngestor
from typing import Dict, Any, Optional
import re
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


class SocialMediaIngestor(BaseIngestor):
    
    def __init__(self, platform: str, domain_patterns: list):
        self._platform = platform
        self.domain_patterns = domain_patterns
    
    @property
    def platform(self) -> str:
        return self._platform
    
    def can_handle(self, url: str) -> bool:
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in self.domain_patterns)
    
    def extract_metadata(self, url: str) -> Dict[str, Any]:
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid URL format: {url}")
            
            metadata = {
                "url": url,
                "platform": self.platform,
                "domain": parsed.netloc,
                "path": parsed.path,
                "extracted_at": None,
                "scraping_available": False,
            }
            
            if self._platform == "tiktok":
                metadata.update(self._extract_tiktok_info(url, parsed))
            elif self._platform == "instagram":
                metadata.update(self._extract_instagram_info(url, parsed))
            elif self._platform == "twitter":
                metadata.update(self._extract_twitter_info(url, parsed))
            elif self._platform == "linkedin":
                metadata.update(self._extract_linkedin_info(url, parsed))
            elif self._platform == "reddit":
                metadata.update(self._extract_reddit_info(url, parsed))
            elif self._platform == "pinterest":
                metadata.update(self._extract_pinterest_info(url, parsed))
            elif self._platform == "snapchat":
                metadata.update(self._extract_snapchat_info(url, parsed))
            elif self._platform == "facebook":
                metadata.update(self._extract_facebook_info(url, parsed))
            
            logger.info(f"Extracted basic metadata for {self._platform} URL: {url}")
            return metadata
            
        except Exception as e:
            logger.error(f"Error extracting metadata from {self._platform} URL {url}: {e}")
            raise ValueError(f"Failed to process {self._platform} URL: {str(e)}")
    
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "title": raw_data.get("title") or f"{self._platform.title()} Link",
            "author": raw_data.get("author"),
            "description": raw_data.get("description"),
            "thumbnail_url": raw_data.get("thumbnail_url"),
            "published_at": raw_data.get("published_at"),
            "platform_specific": {
                "content_id": raw_data.get("content_id"),
                "content_type": raw_data.get("content_type"),
                "username": raw_data.get("username"),
                "domain": raw_data.get("domain"),
                "scraping_available": raw_data.get("scraping_available", False),
            }
        }
    
    def _extract_tiktok_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {"content_type": "video"}
        
        path_match = re.search(r'/@([^/]+)/video/(\d+)', parsed.path)
        if path_match:
            info["username"] = path_match.group(1)
            info["content_id"] = path_match.group(2)
            info["title"] = f"TikTok video by @{info['username']}"
            info["author"] = f"@{info['username']}"
        
        return info
    
    def _extract_instagram_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        if "/p/" in parsed.path:
            info["content_type"] = "post"
            post_match = re.search(r'/p/([^/]+)', parsed.path)
            if post_match:
                info["content_id"] = post_match.group(1)
                info["title"] = f"Instagram post ({info['content_id']})"
        elif "/reel/" in parsed.path:
            info["content_type"] = "reel"
            reel_match = re.search(r'/reel/([^/]+)', parsed.path)
            if reel_match:
                info["content_id"] = reel_match.group(1)
                info["title"] = f"Instagram reel ({info['content_id']})"
        elif "/stories/" in parsed.path:
            info["content_type"] = "story"
            story_match = re.search(r'/stories/([^/]+)/(\d+)', parsed.path)
            if story_match:
                info["username"] = story_match.group(1)
                info["content_id"] = story_match.group(2)
                info["title"] = f"Instagram story by @{info['username']}"
                info["author"] = f"@{info['username']}"
        
        return info
    
    def _extract_twitter_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        status_match = re.search(r'/([^/]+)/status/(\d+)', parsed.path)
        if status_match:
            info["username"] = status_match.group(1)
            info["content_id"] = status_match.group(2)
            info["content_type"] = "tweet"
            info["title"] = f"Tweet by @{info['username']}"
            info["author"] = f"@{info['username']}"
        
        return info
    
    def _extract_linkedin_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        if "/posts/" in parsed.path:
            info["content_type"] = "post"
            post_match = re.search(r'/posts/([^_]+)', parsed.path)
            if post_match:
                info["username"] = post_match.group(1)
                info["title"] = f"LinkedIn post by {info['username']}"
                info["author"] = info["username"]
        elif "/feed/update/" in parsed.path:
            info["content_type"] = "feed_update"
            info["title"] = "LinkedIn feed update"
        
        return info
    
    def _extract_reddit_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        comment_match = re.search(r'/r/([^/]+)/comments/([^/]+)', parsed.path)
        if comment_match:
            info["subreddit"] = comment_match.group(1)
            info["content_id"] = comment_match.group(2)
            info["content_type"] = "post"
            info["title"] = f"Reddit post in r/{info['subreddit']}"
            info["author"] = f"r/{info['subreddit']}"
        
        return info
    
    def _extract_pinterest_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        if "pin.it" in parsed.netloc:
            info["content_type"] = "pin"
            info["title"] = "Pinterest pin"
        elif "/pin/" in parsed.path:
            pin_match = re.search(r'/pin/(\d+)', parsed.path)
            if pin_match:
                info["content_id"] = pin_match.group(1)
                info["content_type"] = "pin"
                info["title"] = f"Pinterest pin ({info['content_id']})"
        
        return info
    
    def _extract_snapchat_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        if "/add/" in parsed.path:
            add_match = re.search(r'/add/([^/]+)', parsed.path)
            if add_match:
                info["username"] = add_match.group(1)
                info["content_type"] = "profile"
                info["title"] = f"Snapchat profile: @{info['username']}"
                info["author"] = f"@{info['username']}"
        elif "/discover/" in parsed.path:
            info["content_type"] = "story"
            info["title"] = "Snapchat story"
        
        return info
    
    def _extract_facebook_info(self, url: str, parsed) -> Dict[str, Any]:
        info = {}
        
        if "/posts/" in parsed.path:
            info["content_type"] = "post"
            post_match = re.search(r'/([^/]+)/posts/', parsed.path)
            if post_match:
                info["username"] = post_match.group(1)
                info["title"] = f"Facebook post by {info['username']}"
                info["author"] = info["username"]
        elif "photo.php" in parsed.path:
            info["content_type"] = "photo"
            info["title"] = "Facebook photo"
        elif "/videos/" in parsed.path:
            info["content_type"] = "video"
            info["title"] = "Facebook video"
        
        return info


class TikTokIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("tiktok", ["tiktok.com"])


class InstagramIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("instagram", ["instagram.com"])


class TwitterIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("twitter", ["twitter.com", "x.com"])


class LinkedInIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("linkedin", ["linkedin.com"])


class RedditIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("reddit", ["reddit.com"])


class PinterestIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("pinterest", ["pinterest.com", "pin.it"])


class SnapchatIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("snapchat", ["snapchat.com"])


class FacebookIngestor(SocialMediaIngestor):
    def __init__(self):
        super().__init__("facebook", ["facebook.com", "fb.com"]) 