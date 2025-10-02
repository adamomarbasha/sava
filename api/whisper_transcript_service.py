import os
import logging
import tempfile
import hashlib
import time
from typing import List, Dict, Optional, Any
from pathlib import Path
import yt_dlp

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    FASTER_WHISPER_AVAILABLE = True
    logger.info("Using faster-whisper for transcription")
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    try:
        import whisper
        logger.info("Using standard whisper for transcription")
    except ImportError:
        logger.error("Neither faster-whisper nor whisper is installed")
        whisper = None

_transcript_cache: Dict[str, tuple] = {}
_cache_ttl = 3600 


class TranscriptEntry(Dict[str, Any]):
    text: str
    start: float
    duration: float


class WhisperTranscriptService:
    
    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or os.getenv("WHISPER_MODEL", "medium")
        self.model = None
        self._model_loaded = False
        
        valid_models = ["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]
        if self.model_name not in valid_models:
            logger.warning(f"Invalid model '{self.model_name}', using 'medium'")
            self.model_name = "medium"
    
    def _load_model(self):
        if self._model_loaded:
            return
        
        try:
            if FASTER_WHISPER_AVAILABLE:
                device = "cuda" if os.getenv("WHISPER_DEVICE") == "cuda" else "cpu"
                compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
                logger.info(f"Loading faster-whisper model: {self.model_name} on {device}")
                self.model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
            elif whisper:
                logger.info(f"Loading whisper model: {self.model_name}")
                self.model = whisper.load_model(self.model_name)
            else:
                raise ImportError("No Whisper implementation available")
            
            self._model_loaded = True
            logger.info(f"Successfully loaded Whisper model: {self.model_name}")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise
    
    def _get_cache_key(self, video_url: str) -> str:
        return hashlib.md5(f"{video_url}_{self.model_name}".encode()).hexdigest()
    
    def _get_cached_transcript(self, cache_key: str) -> Optional[List[TranscriptEntry]]:
        if cache_key in _transcript_cache:
            transcript, timestamp = _transcript_cache[cache_key]
            if time.time() - timestamp < _cache_ttl:
                logger.info(f"Cache hit for transcript: {cache_key}")
                return transcript
            else:
                del _transcript_cache[cache_key]
        return None
    
    def _cache_transcript(self, cache_key: str, transcript: List[TranscriptEntry]):
        _transcript_cache[cache_key] = (transcript, time.time())
        logger.info(f"Cached transcript: {cache_key}")
    
    def _download_tiktok_video(self, video_url: str, temp_dir: str) -> Optional[str]:
        try:
            output_template = os.path.join(temp_dir, "tiktok_video.%(ext)s")
            
            ydl_opts = {
                'format': 'best',
                'outtmpl': output_template,
                'quiet': True,
                'no_warnings': True,
                'extract_audio': False,
                'noplaylist': True,
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                },
            }
            
            logger.info(f"Downloading TikTok video: {video_url}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                
                if not info:
                    logger.error("No video info returned from yt-dlp")
                    return None
                
                if 'requested_downloads' in info and info['requested_downloads']:
                    video_path = info['requested_downloads'][0]['filepath']
                else:
                    video_path = None
                    for ext in ['mp4', 'webm', 'mkv']:
                        potential_path = os.path.join(temp_dir, f"tiktok_video.{ext}")
                        if os.path.exists(potential_path):
                            video_path = potential_path
                            break
                
                if video_path and os.path.exists(video_path):
                    logger.info(f"Successfully downloaded video to: {video_path}")
                    return video_path
                else:
                    logger.error("Downloaded video file not found")
                    return None
                    
        except yt_dlp.DownloadError as e:
            logger.error(f"yt-dlp download error for {video_url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading video: {e}")
            return None
    
    def _transcribe_with_faster_whisper(self, audio_path: str) -> List[TranscriptEntry]:
        try:
            segments, info = self.model.transcribe(
                audio_path,
                beam_size=5,
                language=None,  
                task="transcribe",
                vad_filter=False, 
                word_timestamps=False, 
                condition_on_previous_text=False,  
                temperature=0.0, 
                compression_ratio_threshold=2.4,  
                log_prob_threshold=-1.0, 
                no_speech_threshold=0.6, 
                initial_prompt=None
            )
            
            transcript = []
            for segment in segments:
                text = segment.text.strip()
                
                if not text:
                    continue
                
                words = text.split()
                if len(words) == 1 and len(transcript) > 2:
                    last_texts = [t["text"] for t in transcript[-3:]]
                    if all(t == text for t in last_texts):
                        logger.warning(f"Skipping repetitive segment: '{text}'")
                        continue
                
                entry = {
                    "text": text,
                    "start": round(segment.start, 2),
                    "duration": round(segment.end - segment.start, 2)
                }
                transcript.append(entry)
            
            logger.info(f"Detected language: {info.language} (probability: {info.language_probability:.2f})")
            logger.info(f"Transcribed {len(transcript)} segments")
            
            return transcript
            
        except Exception as e:
            logger.error(f"Faster-whisper transcription error: {e}")
            raise
    
    def _transcribe_with_whisper(self, audio_path: str) -> List[TranscriptEntry]:
        try:
            result = self.model.transcribe(
                audio_path,
                language=None,
                task="transcribe",
                verbose=False
            )
            
            transcript = []
            for segment in result.get("segments", []):
                entry = {
                    "text": segment.get("text", "").strip(),
                    "start": round(segment.get("start", 0), 2),
                    "duration": round(segment.get("end", 0) - segment.get("start", 0), 2)
                }
                if entry["text"]: 
                    transcript.append(entry)
            
            detected_language = result.get("language", "unknown")
            logger.info(f"Detected language: {detected_language}")
            logger.info(f"Transcribed {len(transcript)} segments")
            
            return transcript
            
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            raise
    
    def get_tiktok_transcript(
        self, 
        video_url: str, 
        video_id: Optional[str] = None
    ) -> List[TranscriptEntry]:
        cache_key = self._get_cache_key(video_url)
        
        cached_transcript = self._get_cached_transcript(cache_key)
        if cached_transcript is not None:
            return cached_transcript
        
        if not self._model_loaded:
            try:
                self._load_model()
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                return []
        
        temp_dir = None
        video_path = None
        
        try:
            temp_dir = tempfile.mkdtemp(prefix="tiktok_whisper_")
            logger.info(f"Created temp directory: {temp_dir}")
            
            video_path = self._download_tiktok_video(video_url, temp_dir)
            
            if not video_path:
                logger.error("Failed to download TikTok video")
                return []
            
            logger.info(f"Starting transcription with {self.model_name} model...")
            
            if FASTER_WHISPER_AVAILABLE:
                transcript = self._transcribe_with_faster_whisper(video_path)
            else:
                transcript = self._transcribe_with_whisper(video_path)
            
            self._cache_transcript(cache_key, transcript)
            
            logger.info(f"Successfully transcribed TikTok video: {len(transcript)} segments")
            return transcript
            
        except Exception as e:
            logger.error(f"Error transcribing TikTok video: {e}")
            return []
            
        finally:
            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    logger.info(f"Cleaned up video file: {video_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove video file: {e}")
            
            if temp_dir and os.path.exists(temp_dir):
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up temp directory: {temp_dir}")
                except Exception as e:
                    logger.warning(f"Failed to remove temp directory: {e}")
    
    def get_batch_transcripts(
        self, 
        video_urls: List[str]
    ) -> Dict[str, List[TranscriptEntry]]:
        results = {}
        
        for url in video_urls:
            try:
                transcript = self.get_tiktok_transcript(url)
                results[url] = transcript
            except Exception as e:
                logger.error(f"Error processing {url}: {e}")
                results[url] = []
        
        return results


def get_tiktok_transcript(
    video_url: str, 
    video_id: Optional[str] = None,
    model: Optional[str] = None
) -> List[TranscriptEntry]:
    service = WhisperTranscriptService(model_name=model)
    return service.get_tiktok_transcript(video_url, video_id)


def get_batch_tiktok_transcripts(
    video_urls: List[str],
    model: Optional[str] = None
) -> Dict[str, List[TranscriptEntry]]:
    service = WhisperTranscriptService(model_name=model)
    return service.get_batch_transcripts(video_urls)
