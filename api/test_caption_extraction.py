#!/usr/bin/env python3
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ingestors.tiktok import TikTokIngestor

def test_caption_extraction():
    ingestor = TikTokIngestor()
    
    test_url = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
    
    print(f"Testing caption extraction for: {test_url}")
    print("="*80)
    
    try:
        metadata = ingestor.extract_metadata(test_url)
        
        print(f"Platform: {metadata.get('platform')}")
        print(f"Title: {metadata.get('title')}")
        print(f"Author: {metadata.get('author')}")
        print(f"Description: {metadata.get('description', '')[:200]}...")
        print(f"Thumbnail URL: {metadata.get('thumbnail_url')}")
        
        raw = metadata.get('raw', {})
        print(f"Video ID: {raw.get('id')}")
        print(f"Hashtags: {raw.get('hashtags', [])}")
        
        title = metadata.get('title', '')
        if 'TikTok video by @' in title:
            print("\n❌ FALLBACK TITLE - Could not extract actual caption")
        else:
            print("\n✅ ACTUAL CAPTION EXTRACTED!")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_caption_extraction()
