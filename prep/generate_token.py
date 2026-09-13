"""
Generates token.json for ClauseCI.

Requests the FINAL scope set so we never have to re-authorise mid-hackathon:
  - Drive  : READ-ONLY  (agent can read contracts, can never modify Drive)
  - Gmail  : COMPOSE    (agent can create drafts; gmail.compose is required to
                         create a draft, but the Action Kernel simply never
                         exposes drafts.send as a tool)

Run:  ./.venv/bin/python prep/generate_token.py
"""
import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS = os.path.join(ROOT, "credentials.json")
TOKEN = os.path.join(ROOT, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


def main() -> int:
    if not os.path.exists(CREDS):
        print(f"FATAL: credentials.json not found at {CREDS}")
        return 1

    creds = None
    if os.path.exists(TOKEN):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
        except Exception:
            creds = None
        if creds and creds.valid:
            print("token.json already exists and is valid. Nothing to do.")
            _report(creds)
            return 0
        if creds and creds.expired and creds.refresh_token:
            print("token.json expired -> refreshing...")
            creds.refresh(Request())
            _save(creds)
            _report(creds)
            return 0

    print("Opening your browser for Google authorisation...")
    print("  -> Pick the Google account that OWNS the 'ClauseCI Demo Contracts' folder.")
    print("  -> On the 'Google hasn't verified this app' screen: Advanced -> Go to ClauseCI (unsafe).")
    print("     That warning is expected for your own unpublished test app.")
    print("  -> Tick BOTH permission checkboxes, then Continue.\n")

    flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",   # we need a refresh_token
        prompt="consent",        # force refresh_token even on re-auth
        open_browser=True,
    )
    _save(creds)
    _report(creds)
    return 0


def _save(creds) -> None:
    with open(TOKEN, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(TOKEN, 0o600)


def _report(creds) -> None:
    data = json.loads(creds.to_json())
    print("\n================ SUCCESS ================")
    print(f"token.json written -> {TOKEN}")
    print(f"has refresh_token  : {bool(data.get('refresh_token'))}")
    print("granted scopes     :")
    for s in data.get("scopes", []):
        print(f"   - {s}")
    missing = [s for s in SCOPES if s not in data.get("scopes", [])]
    if missing:
        print("\nWARNING: missing scopes (you unticked a box?):")
        for s in missing:
            print(f"   - {s}")
        print("Delete token.json and re-run.")
    else:
        print("\nAll required scopes granted.")
    print("=========================================")


if __name__ == "__main__":
    sys.exit(main())
