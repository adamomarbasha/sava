#!/usr/bin/env python3
"""
Startup script for Sava API.
Run this from the root directory to avoid import path issues.
"""

import sys
import os
from pathlib import Path

# Add the api directory to Python path
api_dir = Path(__file__).parent / "api"
sys.path.insert(0, str(api_dir))

if __name__ == "__main__":
    import uvicorn
    
    # Change to api directory for relative file paths
    os.chdir(api_dir)
    
    # Start the server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(api_dir)]
    ) 