#!/usr/bin/env python3

import sys
import os
from pathlib import Path

api_dir = Path(__file__).parent / "api"
sys.path.insert(0, str(api_dir))

if __name__ == "__main__":
    import uvicorn
    
    os.chdir(api_dir)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(api_dir)]
    ) 