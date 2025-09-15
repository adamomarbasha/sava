from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union, Awaitable
from datetime import datetime
import asyncio

class BaseIngestor(ABC):
    
    @property
    @abstractmethod
    def platform(self) -> str:
        pass
    
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        pass
    
    @abstractmethod
    def extract_metadata(self, url: str) -> Union[Dict[str, Any], Awaitable[Dict[str, Any]]]:
        pass
    
    @abstractmethod
    def normalize_metadata(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        pass
    
    def validate_url(self, url: str) -> bool:
        try:
            from urllib.parse import urlparse
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False
    
    def is_async(self) -> bool:
        return asyncio.iscoroutinefunction(self.extract_metadata) 