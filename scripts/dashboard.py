#!/usr/bin/env python3
"""Standalone entry point for the TUI dashboard.

Usage:
    python scripts/dashboard.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from tui.app import TUIApp


def main() -> None:
    app = TUIApp()
    app.run()


if __name__ == "__main__":
    main()
