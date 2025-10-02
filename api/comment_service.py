import logging
import json
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse, parse_qs
from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_POPULAR, SORT_BY_RECENT
from sqlalchemy.orm import Session
from .models import Comment, Bookmark

logger = logging.getLogger(__name__)

class YouTubeCommentService:
    
    def __init__(self):
        self.downloader = YoutubeCommentDownloader()
    
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
    
    def get_comments(
        self, 
        video_input: str, 
        limit: int = 50,
        sort_by: str = 'popular'
    ) -> Dict[str, Union[bool, List[Dict], str]]:
        try:
            video_id = self.extract_video_id(video_input)
            if not video_id:
                return {
                    "success": False,
                    "comments": None,
                    "error": f"Could not extract video ID from: {video_input}",
                    "video_id": None
                }
            
            logger.info(f"Fetching comments for video ID: {video_id}")
            
            sort_order = SORT_BY_POPULAR if sort_by == 'popular' else SORT_BY_RECENT
            
            comments = []
            try:
                comment_generator = self.downloader.get_comments_from_url(
                    f"https://www.youtube.com/watch?v={video_id}",
                    sort_by=sort_order
                )
                
                for comment in comment_generator:
                    if len(comments) >= limit:
                        break
                    
                    processed_comment = {
                        "text": comment.get('text', '').strip(),
                        "author": comment.get('author', 'Unknown'),
                        "like_count": comment.get('votes', 0),
                        "time_parsed": comment.get('time_parsed'),
                        "raw": comment
                    }
                    
                    if processed_comment["text"]:
                        comments.append(processed_comment)
                
                logger.info(f"Successfully fetched {len(comments)} comments for video {video_id}")
                
                return {
                    "success": True,
                    "comments": comments,
                    "error": None,
                    "video_id": video_id,
                    "total_fetched": len(comments)
                }
                
            except Exception as e:
                logger.warning(f"Error fetching comments for video {video_id}: {e}")
                return {
                    "success": False,
                    "comments": None,
                    "error": f"Failed to fetch comments: {str(e)}",
                    "video_id": video_id
                }
                
        except Exception as e:
            logger.error(f"Error in get_comments: {e}")
            return {
                "success": False,
                "comments": None,
                "error": f"Internal server error: {str(e)}",
                "video_id": None
            }
    
    def save_comments_to_db(
        self, 
        bookmark_id: int, 
        comments: List[Dict], 
        db: Session
    ) -> Dict[str, Union[bool, int, str]]:
        try:
            saved_count = 0
            
            for comment_data in comments:
                try:
                    comment = Comment(
                        bookmark_id=bookmark_id,
                        platform_author=comment_data.get('author', 'Unknown'),
                        text=comment_data.get('text', ''),
                        like_count=comment_data.get('like_count', 0),
                        raw=json.dumps(comment_data.get('raw', {}))
                    )
                    
                    db.add(comment)
                    saved_count += 1
                    
                except Exception as e:
                    logger.warning(f"Error saving individual comment: {e}")
                    continue
            
            db.commit()
            logger.info(f"Successfully saved {saved_count} comments to database")
            
            return {
                "success": True,
                "saved_count": saved_count,
                "error": None
            }
            
        except Exception as e:
            logger.error(f"Error saving comments to database: {e}")
            db.rollback()
            return {
                "success": False,
                "saved_count": 0,
                "error": f"Database error: {str(e)}"
            }
    
    def get_comments_from_db(
        self, 
        bookmark_id: int, 
        db: Session,
        limit: int = 50
    ) -> Dict[str, Union[bool, List[Dict], str]]:
        try:
            comments = db.query(Comment).filter(
                Comment.bookmark_id == bookmark_id
            ).order_by(
                Comment.like_count.desc(),
                Comment.created_at.desc()
            ).limit(limit).all()
            
            comment_list = []
            for comment in comments:
                comment_list.append({
                    "id": comment.id,
                    "author": comment.platform_author,
                    "text": comment.text,
                    "like_count": comment.like_count,
                    "created_at": comment.created_at.isoformat() if comment.created_at else None,
                    "raw": json.loads(comment.raw) if comment.raw else {}
                })
            
            return {
                "success": True,
                "comments": comment_list,
                "error": None,
                "total_count": len(comment_list)
            }
            
        except Exception as e:
            logger.error(f"Error retrieving comments from database: {e}")
            return {
                "success": False,
                "comments": None,
                "error": f"Database error: {str(e)}"
            }

youtube_comment_service = YouTubeCommentService()
