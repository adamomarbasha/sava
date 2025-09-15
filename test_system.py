#!/usr/bin/env python3

import requests
import sys
import json

API_BASE = "http://localhost:8000"

def test_health():
    try:
        response = requests.get(f"{API_BASE}/")
        assert response.status_code == 200
        data = response.json()
        assert "Sava API is running" in data["message"]
        print("✅ Health check passed")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False

def test_auth():
    try:
        user_data = {
            "email": "test@example.com",
            "password": "testpass123"
        }
        
        response = requests.post(f"{API_BASE}/auth/register", json=user_data)
        if response.status_code == 400 and "already registered" in response.text:
            print("ℹ️  Test user already exists")
        elif response.status_code == 200:
            print("✅ User registration successful")
        else:
            print(f"❌ Registration failed: {response.status_code} {response.text}")
            return False, None
        
        response = requests.post(f"{API_BASE}/auth/login", json=user_data)
        assert response.status_code == 200
        token_data = response.json()
        token = token_data["access_token"]
        print("✅ Login successful")
        
        return True, token
    except Exception as e:
        print(f"❌ Auth test failed: {e}")
        return False, None

def test_youtube_bookmark(token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        bookmark_data = {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        }
        
        response = requests.post(
            f"{API_BASE}/api/bookmarks/youtube", 
            json=bookmark_data,
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ YouTube bookmark created: {data.get('title', 'Unknown')}")
            return True, data["id"]
        elif response.status_code == 409:
            print("ℹ️  YouTube bookmark already exists (updating)")
            data = response.json()
            return True, data["id"]
        else:
            print(f"❌ YouTube bookmark failed: {response.status_code} {response.text}")
            return False, None
            
    except Exception as e:
        print(f"❌ YouTube bookmark test failed: {e}")
        return False, None

def test_bookmark_listing(token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(f"{API_BASE}/api/bookmarks", headers=headers)
        assert response.status_code == 200
        all_bookmarks = response.json()
        print(f"✅ Listed {len(all_bookmarks)} total bookmarks")
        
        response = requests.get(f"{API_BASE}/api/bookmarks?platform=youtube", headers=headers)
        assert response.status_code == 200
        youtube_bookmarks = response.json()
        print(f"✅ Listed {len(youtube_bookmarks)} YouTube bookmarks")
        
        response = requests.get(f"{API_BASE}/api/bookmarks?q=rick", headers=headers)
        assert response.status_code == 200
        search_results = response.json()
        print(f"✅ Search returned {len(search_results)} results")
        
        return True
    except Exception as e:
        print(f"❌ Bookmark listing test failed: {e}")
        return False

def main():
    print("🧪 Starting Sava system tests...\n")
    
    if not test_health():
        print("\n❌ System tests failed - API not responding")
        sys.exit(1)
    
    auth_success, token = test_auth()
    if not auth_success:
        print("\n❌ System tests failed - Authentication not working")
        sys.exit(1)
    
    youtube_success, bookmark_id = test_youtube_bookmark(token)
    if not youtube_success:
        print("\n❌ System tests failed - YouTube ingestion not working")
        sys.exit(1)
    
    if not test_bookmark_listing(token):
        print("\n❌ System tests failed - Bookmark listing not working")
        sys.exit(1)
    
    print("\n🎉 All system tests passed!")
    print("\nYour Sava bookmark system is working correctly!")
    print(f"- API running at {API_BASE}")
    print(f"- API docs at {API_BASE}/docs")
    print("- Frontend should be at http://localhost:3000")

if __name__ == "__main__":
    main() 