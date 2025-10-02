import requests
import json
import re
import os
import time
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class TikTokCommentService:
    def __init__(self):
        self.cookies = os.getenv('TIKTOK_COOKIES', '')
        self.user_agent = os.getenv('TIKTOK_USER_AGENT', 
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
        
        self.last_success = None
        self.consecutive_failures = 0
        
    def _get_headers(self):
        return {
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'priority': 'u=1, i',
            'referer': 'https://www.tiktok.com/',
            'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.user_agent,
            'cookie': self.cookies
        }
    
    def extract_video_id(self, url: str) -> str:
        patterns = [
            r'/video/(\d+)',
            r'@[^/]+/video/(\d+)',
            r'aweme_id=(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        raise ValueError(f"Could not extract video ID from URL: {url}")
    
    def test_connection(self) -> Dict[str, any]:
        try:
            headers = self._get_headers()
            
            response = requests.get('https://www.tiktok.com/', headers=headers, timeout=10)
            
            if response.status_code == 200:
                self.last_success = datetime.now()
                self.consecutive_failures = 0
                return {
                    'success': True,
                    'message': 'Connection working',
                    'status_code': response.status_code
                }
            else:
                self.consecutive_failures += 1
                return {
                    'success': False,
                    'message': f'HTTP {response.status_code}',
                    'status_code': response.status_code
                }
                
        except Exception as e:
            self.consecutive_failures += 1
            return {
                'success': False,
                'message': f'Connection error: {str(e)}',
                'status_code': None
            }
    
    def fetch_comments(self, video_id: str, max_comments: int = 100) -> Dict[str, any]:
        
        if not self.cookies or 'PASTE_YOUR_FULL_COOKIE_STRING_HERE' in self.cookies:
            return {
                'success': False,
                'comments': [],
                'total_count': 0,
                'message': 'TikTok cookies not configured. Please set up authentication.',
                'needs_refresh': True
            }
        
        connection_test = self.test_connection()
        if not connection_test['success']:
            return {
                'success': False,
                'comments': [],
                'total_count': 0,
                'message': f"Connection failed: {connection_test['message']}",
                'needs_refresh': True
            }
        
        headers = self._get_headers()
        comments = []
        cursor = 0
        
        logger.info(f"Fetching TikTok comments for video {video_id}")
        
        try:
            while len(comments) < max_comments:
                url = 'https://www.tiktok.com/api/comment/list/'
                
                params = {
                    'WebIdLastTime': '1752275464',
                    'aid': '1988',
                    'app_language': 'ja-JP',
                    'app_name': 'tiktok_web',
                    'aweme_id': video_id,
                    'browser_language': 'en-US',
                    'browser_name': 'Mozilla',
                    'browser_online': 'true',
                    'browser_platform': 'MacIntel',
                    'browser_version': '5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
                    'channel': 'tiktok_web',
                    'cookie_enabled': 'true',
                    'count': '20',
                    'current_region': 'JP',
                    'cursor': str(cursor),
                    'data_collection_enabled': 'true',
                    'device_id': '7525965771907450381',
                    'device_platform': 'web_pc',
                    'enter_from': 'tiktok_web',
                    'focus_state': 'true',
                    'fromWeb': '1',
                    'from_page': 'video',
                    'history_len': '9',
                    'is_fullscreen': 'true',
                    'is_non_personalized': 'false',
                    'is_page_visible': 'true',
                    'odinId': '6735739011908420614',
                    'os': 'mac',
                    'priority_region': 'US',
                    'referer': '',
                    'region': 'US',
                    'screen_height': '982',
                    'screen_width': '1512',
                    'tz_name': 'America/Los_Angeles',
                    'user_is_login': 'true',
                    'verifyFp': 'verify_mf1pulqm_btOvdKfi_nIrJ_4euB_Bo4b_aaq2Yg2Hi9Wl',
                    'webcast_language': 'en',
                    'msToken': 'FZpKOx4YqGpIXbfRUTtD9Xvqe7VzlVbs516yNJMronJMPnmDblQa-8ogzJ1RoUsF5Eeuzl-z4ptjSjXKzQe3upaKI9dKquUD3_VC1edz6Y1KAY4d4zTqAhRGok3bAIpB4RMQeH_GPfwtFSH4R3qC1o30',
                    'X-Bogus': 'DFSzswVuUJGANewlC9F4xXhGbwj-',
                    'X-Gnarly': 'MJGfHpXwABHARdS2SPZDyl0dmfImuqltpJBTZEFWdk-mw9NcS1cv6jm91REIhi03PksmUHCnWcOa-ZolbdVAZ/RYUlxzUOmZNK124M8n9EWnkemb90917/ibnEdQ2KGnEtzZKgzmR2XYGfWKmMymC1vm86SMn/rcl97fM07mQDmoaaBroFj24Z-izQAela4KfL/IZux-TW1tIfRAQCuP2I01N9rlndIanAG0bw9TFeh-XowT9W0pOqtv-H76QI/VVT6JXw6xQtY1Wd525mVQlR0Z9A2Oc7umY0SjS-g5yAfO'
                }
                
                response = requests.get(url, headers=headers, params=params, timeout=15)
                
                if response.status_code != 200:
                    logger.warning(f"TikTok API returned status {response.status_code}")
                    if response.status_code in [403, 401]:
                        return {
                            'success': False,
                            'comments': comments,
                            'total_count': len(comments),
                            'message': 'Authentication failed. Cookies may have expired.',
                            'needs_refresh': True
                        }
                    break
                
                if len(response.content) == 0:
                    logger.warning("Empty response from TikTok API")
                    return {
                        'success': False,
                        'comments': comments,
                        'total_count': len(comments),
                        'message': 'Empty response. Cookies may need refreshing.',
                        'needs_refresh': True
                    }
                
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error: {e}")
                    return {
                        'success': False,
                        'comments': comments,
                        'total_count': len(comments),
                        'message': 'Invalid response format. Cookies may need refreshing.',
                        'needs_refresh': True
                    }
                
                if data.get('status_code') != 0:
                    error_msg = data.get('status_msg', 'Unknown API error')
                    logger.error(f"TikTok API error: {error_msg}")
                    return {
                        'success': False,
                        'comments': comments,
                        'total_count': len(comments),
                        'message': f'API error: {error_msg}',
                        'needs_refresh': 'auth' in error_msg.lower() or 'login' in error_msg.lower()
                    }
                
                comment_list = data.get('comments', [])
                if not comment_list:
                    break
                
                for comment in comment_list:
                    try:
                        comment_data = {
                            'cid': comment.get('cid', ''),
                            'text': comment.get('text', ''),
                            'username': comment.get('user', {}).get('nickname', ''),
                            'user_id': comment.get('user', {}).get('uid', ''),
                            'like_count': comment.get('digg_count', 0),
                            'create_time': comment.get('create_time', 0),
                            'reply_count': comment.get('reply_comment_total', 0),
                        }
                        comments.append(comment_data)
                    except Exception as e:
                        logger.warning(f"Error parsing comment: {e}")
                        continue
                
                has_more = data.get('has_more', 0)
                if not has_more:
                    break
                
                cursor = data.get('cursor', cursor + 20)
                
                time.sleep(1)
            
            self.last_success = datetime.now()
            self.consecutive_failures = 0
            
            return {
                'success': True,
                'comments': comments,
                'total_count': len(comments),
                'message': f'Successfully fetched {len(comments)} comments',
                'needs_refresh': False
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            return {
                'success': False,
                'comments': comments,
                'total_count': len(comments),
                'message': f'Network error: {str(e)}',
                'needs_refresh': False
            }
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {
                'success': False,
                'comments': comments,
                'total_count': len(comments),
                'message': f'Unexpected error: {str(e)}',
                'needs_refresh': False
            }

tiktok_service = TikTokCommentService()