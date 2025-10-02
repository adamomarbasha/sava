#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.whisper_transcript_service import get_tiktok_transcript
import tempfile
import yt_dlp

tiktok_url = "https://www.tiktok.com/@endisfree/video/7231033491788664110"

print("=" * 60)
print("Checking Video Duration vs Transcript Coverage")
print("=" * 60)

print("\n📹 Fetching video metadata...")
ydl_opts = {
    'quiet': True,
    'no_warnings': True,
    'skip_download': True,
}

try:
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(tiktok_url, download=False)
        duration = info.get('duration', 0)
        
    print(f"   Video Duration: {duration:.1f} seconds")
    
    print("\n⏳ Getting transcript (from cache if available)...")
    transcript = get_tiktok_transcript(tiktok_url)
    
    if transcript:
        first_segment = transcript[0]
        last_segment = transcript[-1]
        
        transcript_start = first_segment['start']
        transcript_end = last_segment['start'] + last_segment['duration']
        transcript_coverage = transcript_end - transcript_start
        
        print(f"\n📊 Transcript Analysis:")
        print(f"   Total Segments: {len(transcript)}")
        print(f"   First Speech: {transcript_start:.1f}s")
        print(f"   Last Speech: {transcript_end:.1f}s")
        print(f"   Coverage: {transcript_coverage:.1f}s")
        print(f"   Video Duration: {duration:.1f}s")
        
        if duration > 0:
            coverage_pct = (transcript_end / duration) * 100
            print(f"\n   Coverage: {coverage_pct:.1f}% of video")
            
            if coverage_pct >= 95:
                print("   ✅ Full video transcribed!")
            elif coverage_pct >= 80:
                print("   ⚠️  Most of video transcribed (some silence at end)")
            else:
                print("   ⚠️  Partial transcription - may be missing content")
        
        print(f"\n📝 All {len(transcript)} Segments:")
        print("=" * 60)
        for i, entry in enumerate(transcript, 1):
            end_time = entry['start'] + entry['duration']
            print(f"{i}. [{entry['start']:.1f}s - {end_time:.1f}s] ({entry['duration']:.1f}s)")
            print(f"   \"{entry['text']}\"")
            
    else:
        print("\n❌ No transcript generated")
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
