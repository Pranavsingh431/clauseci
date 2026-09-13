"""
Deployment entry point for Streamlit Community Cloud.

Deliberately tiny. All of the console lives in `ui/console.py`, and this file
only makes the repository root the entry point so cloud configuration is
obvious. There is no duplicated logic here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ui.console  # noqa: F401,E402  importing renders the page
