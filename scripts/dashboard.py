#!/usr/bin/env python3
"""Standalone entry point for the web dashboard.

Usage:
    python scripts/dashboard.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from web.server import create_app


def main() -> None:
    app = create_app()
    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
