"""
Deployment entry point for Streamlit Community Cloud.

Deliberately tiny. All of the console lives in `ui/console.py`, and this file
only makes the repository root the entry point so cloud configuration is
obvious. There is no duplicated logic here.

It calls a function rather than relying on an import to draw the page.
Streamlit re-runs this script on every rerun and every new browser session,
while `sys.modules` survives for the life of the server process, so an import
side effect renders once and then never again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.console import main  # noqa: E402  path set above

main()
