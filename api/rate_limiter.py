import time
from typing import Dict, Optional

class RateLimiter:
    
    def __init__(self, max_requests: int = 10, time_window: int = 3600):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
    
    def can_make_request(self) -> bool:
        now = time.time()
        
        self.requests = [req_time for req_time in self.requests if now - req_time < self.time_window]
        
        return len(self.requests) < self.max_requests
    
    def record_request(self):
        self.requests.append(time.time())
    
    def get_wait_time(self) -> float:
        if self.can_make_request():
            return 0.0
        
        if not self.requests:
            return 0.0
        
        oldest_request = min(self.requests)
        wait_time = self.time_window - (time.time() - oldest_request)
        return max(0.0, wait_time)
    
    def get_status(self) -> Dict:
        now = time.time()
        recent_requests = [req_time for req_time in self.requests if now - req_time < self.time_window]
        
        return {
            "can_make_request": self.can_make_request(),
            "requests_made": len(recent_requests),
            "max_requests": self.max_requests,
            "time_window": self.time_window,
            "wait_time": self.get_wait_time()
        }

rate_limiter = RateLimiter(max_requests=50, time_window=3600)
