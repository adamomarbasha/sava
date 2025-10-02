import re
import logging
import json
import time
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import JSONFormatter
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    YouTubeRequestFailed
)
try:
    from youtube_transcript_api._errors import TooManyRequests
except ImportError:
    TooManyRequests = Exception

try:
    from rate_limiter import rate_limiter
except ImportError:
    class MockRateLimiter:
        def get_status(self):
            return {"can_make_request": True, "requests_made": 0, "max_requests": 5, "time_window": 3600, "wait_time": 0.0}
        def can_make_request(self):
            return True
        def record_request(self):
            pass
    rate_limiter = MockRateLimiter()

logger = logging.getLogger(__name__)

_transcript_cache = {}
_cache_ttl = 3600 


class YouTubeTranscriptService:
    
    def __init__(self):
        self.formatter = JSONFormatter()
    
    def extract_video_id(self, input_value: str) -> Optional[str]:
        try:
            if len(input_value) == 11 and input_value.replace('-', '').replace('_', '').isalnum():
                return input_value
            
            parsed_url = urlparse(input_value)
            
            if 'youtu.be' in parsed_url.netloc:
                return parsed_url.path.lstrip('/').split('?')[0]
            
            if 'youtube.com' in parsed_url.netloc:
                if parsed_url.path == '/watch':
                    return parse_qs(parsed_url.query).get('v', [None])[0]
                elif parsed_url.path.startswith('/embed/'):
                    return parsed_url.path.split('/embed/')[1].split('?')[0]
                elif parsed_url.path.startswith('/v/'):
                    return parsed_url.path.split('/v/')[1].split('?')[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting video ID from {input_value}: {e}")
            return None
    
    def _get_cache_key(self, video_id: str, languages: List[str], preserve_formatting: bool) -> str:
        lang_str = ",".join(sorted(languages or []))
        return f"{video_id}_{lang_str}_{preserve_formatting}"
    
    def _get_cached_transcript(self, cache_key: str) -> Optional[Dict]:
        if cache_key in _transcript_cache:
            cached_data, timestamp = _transcript_cache[cache_key]
            if time.time() - timestamp < _cache_ttl:
                logger.info(f"Cache hit for transcript: {cache_key}")
                return cached_data
            else:
                del _transcript_cache[cache_key]
        return None
    
    def _cache_transcript(self, cache_key: str, transcript_data: Dict):
        _transcript_cache[cache_key] = (transcript_data, time.time())
        logger.info(f"Cached transcript: {cache_key}")

    def get_transcript(
        self, 
        video_input: str, 
        languages: List[str] = None,
        preserve_formatting: bool = False
    ) -> Dict[str, Union[str, List[Dict], bool]]:
        
        try:
            video_id = self.extract_video_id(video_input)
            if not video_id:
                return {
                    "success": False,
                    "transcript": None,
                    "error": f"Could not extract video ID from: {video_input}",
                    "video_id": None,
                    "language": None
                }
            
            cache_key = self._get_cache_key(video_id, languages, preserve_formatting)
            cached_result = self._get_cached_transcript(cache_key)
            if cached_result:
                return cached_result
            
            if not rate_limiter.can_make_request():
                wait_time = rate_limiter.get_wait_time()
                return {
                    "success": False,
                    "transcript": None,
                    "error": f"Rate limit exceeded (50 requests per hour). Please wait {wait_time:.0f} seconds before trying again.",
                    "video_id": video_id,
                    "language": None
                }
            
            logger.info(f"Fetching transcript for video ID: {video_id}")
            
            if not languages:
                languages = ['en']
            
            rate_limiter.record_request()
            
            try:
                ytt_api = YouTubeTranscriptApi()
                
                preferred_languages = languages or ['en']
                try:
                    transcript_list = ytt_api.fetch(
                        video_id, 
                        languages=preferred_languages,
                        preserve_formatting=preserve_formatting
                    )
                    logger.info(f"Successfully fetched transcript in preferred language")
                    
                except (NoTranscriptFound, TranscriptsDisabled) as e:
                    logger.warning(f"No transcript found in preferred languages: {e}")
                    try:
                        available_transcripts = ytt_api.list(video_id)
                        if not available_transcripts:
                            raise NoTranscriptFound(f"No subtitles are available for this video. This is common for music videos, instrumentals, or content without speech.")
                    except:
                        raise NoTranscriptFound(f"No subtitles are available for this video. This is common for music videos, instrumentals, or content without speech.")
                    
                    fallback_languages = ['en-US', 'en-GB', 'en', 'es', 'fr', 'de', 'pt', 'it']
                    transcript_list = None
                    
                    for lang in fallback_languages:
                        try:
                            transcript_list = ytt_api.fetch(
                                video_id, 
                                languages=[lang],
                                preserve_formatting=preserve_formatting
                            )
                            logger.info(f"Found transcript in fallback language: {lang}")
                            break
                        except:
                            continue
                    
                    if transcript_list is None:
                        raise NoTranscriptFound(f"No subtitles are available for this video. This is common for music videos, instrumentals, or content without speech.")
                        
            except TooManyRequests as e:
                logger.warning(f"Rate limited by YouTube: {e}")
                raise
            except Exception as e:
                logger.warning(f"Unexpected error fetching transcript: {e}")
                raise
            
            processed_transcript = []
            
            if hasattr(transcript_list, 'to_raw_data'):
                raw_data = transcript_list.to_raw_data()
                for entry in raw_data:
                    processed_entry = {
                        "text": entry.get('text', '').strip(),
                        "start": round(entry.get('start', 0), 2),
                        "duration": round(entry.get('duration', 0), 2)
                    }
                    processed_transcript.append(processed_entry)
            else:
                for entry in transcript_list:
                    processed_entry = {
                        "text": entry.get('text', '').strip(),
                        "start": round(entry.get('start', 0), 2),
                        "duration": round(entry.get('duration', 0), 2)
                    }
                    processed_transcript.append(processed_entry)
            
            if hasattr(transcript_list, 'language_code'):
                transcript_language = transcript_list.language_code
            elif hasattr(transcript_list, 'to_raw_data'):
                raw_data = transcript_list.to_raw_data()
                transcript_language = raw_data[0].get('language', 'unknown') if raw_data else 'unknown'
            else:
                transcript_language = transcript_list[0].get('language', 'unknown') if transcript_list else 'unknown'
            
            logger.info(f"Successfully fetched transcript with {len(processed_transcript)} entries")
            
            result = {
                "success": True,
                "transcript": processed_transcript,
                "error": None,
                "video_id": video_id,
                "language": transcript_language
            }
            
            self._cache_transcript(cache_key, result)
            
            return result
            
        except TranscriptsDisabled:
            error_msg = "This video has subtitles disabled by the creator. This is common for music videos, instrumentals, or content without speech."
            logger.warning(f"Transcripts disabled for video: {video_id}")
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
            
        except NoTranscriptFound:
            error_msg = "No subtitles are available for this video. This is common for music videos, instrumentals, or content without speech."
            logger.warning(f"No transcript found for video: {video_id}")
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
            
        except VideoUnavailable:
            error_msg = f"Video is unavailable: {video_id}"
            logger.warning(error_msg)
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
            
        except TooManyRequests:
            error_msg = "Rate limit reached. Please wait a few minutes before trying again."
            logger.warning(error_msg)
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
            
        except YouTubeRequestFailed as e:
            error_msg = f"YouTube API request failed: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
            
        except Exception as e:
            error_msg = f"Unexpected error fetching transcript: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "transcript": None,
                "error": error_msg,
                "video_id": video_id,
                "language": None
            }
    
    def get_available_languages(self, video_input: str) -> Dict[str, Union[bool, List[Dict], str]]:
        try:
            video_id = self.extract_video_id(video_input)
            if not video_id:
                return {
                    "success": False,
                    "languages": None,
                    "error": f"Could not extract video ID from: {video_input}"
                }
            
            try:
                ytt_api = YouTubeTranscriptApi()
                transcript_list = ytt_api.list(video_id)
                
                languages = []
                for transcript in transcript_list:
                    languages.append({
                        "language": transcript.language,
                        "language_code": transcript.language_code,
                        "is_generated": transcript.is_generated,
                        "is_translatable": transcript.is_translatable
                    })
                
                return {
                    "success": True,
                    "languages": languages,
                    "error": None
                }
                
            except Exception as e:
                return {
                    "success": True,
                    "languages": [{"language": "en", "language_code": "en", "is_generated": False, "is_translatable": True}],
                    "error": None
                }
            
        except Exception as e:
            error_msg = f"Error getting available languages: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "languages": None,
                "error": error_msg
            }


def get_youtube_transcript(
    video_input: str, 
    languages: List[str] = None,
    preserve_formatting: bool = False
) -> Dict[str, Union[str, List[Dict], bool]]:
    service = YouTubeTranscriptService()
    return service.get_transcript(video_input, languages, preserve_formatting)


def get_available_transcript_languages(video_input: str) -> Dict[str, Union[bool, List[Dict], str]]:
    service = YouTubeTranscriptService()
    return service.get_available_languages(video_input)
