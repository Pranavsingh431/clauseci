"""
Finds the Drive folder ClauseCI should read contracts from, and writes its id
to .env. Prefers a folder the AUTHORISED account owns and can write to, so the
whole demo lives in one Google account.

Run:  ./.venv/bin/python prep/smoke/resolve_folder.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_util as E
E.load()
from smoke import _google_creds
from googleapiclient.discovery import build

d = build("drive", "v3", credentials=_google_creds(), cache_discovery=False)
me = d.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
print(f"authorised as: {me}\n")

FIELDS = "files(id,name,owners(emailAddress),capabilities(canAddChildren))"
BASE = "mimeType='application/vnd.google-apps.folder' and trashed=false and name contains 'ClauseCI'"
seen, folders = set(), []
for q in (BASE, BASE + " and sharedWithMe=true"):
    for f in d.files().list(q=q, fields=FIELDS, pageSize=50).execute().get("files", []):
        if f["id"] not in seen:
            seen.add(f["id"]); folders.append(f)
# also include whatever .env already points at, even if not name-matched
cur = os.environ.get("CONTRACT_DRIVE_FOLDER_ID", "").strip()
if cur and cur not in seen:
    try:
        folders.append(d.files().get(fileId=cur, fields="id,name,owners(emailAddress),capabilities(canAddChildren)").execute())
    except Exception:
        pass
if not folders:
    sys.exit(f"No ClauseCI folder is visible to {me}.\n"
             "Create one named 'ClauseCI Demo Contracts' in this account's My Drive,\n"
             "drop the 8 PDFs from demo_contracts/ into it, then re-run this script.")

print(f"{'folder':<32} {'owner':<30} {'writable':<9} id")
print("-" * 100)
rows = []
for f in folders:
    owner = f["owners"][0]["emailAddress"]
    writable = bool(f["capabilities"].get("canAddChildren"))
    n = len(d.files().list(q=f"'{f['id']}' in parents and trashed=false",
                           fields="files(id)", pageSize=100).execute().get("files", []))
    print(f"{f['name'][:31]:<32} {owner:<30} {str(writable):<9} {f['id']}  ({n} files)")
    rows.append((f, owner, writable, n))

# Prefer: owned by me AND writable AND already has files > owned by me > anything readable
best = (sorted(rows, key=lambda r: (r[1] == me, r[2], r[3]), reverse=True))[0]
f, owner, writable, n = best
print(f"\nselected: {f['name']}  ({f['id']})")
if owner != me:
    print(f"  NOTE: owned by {owner}, not {me}.")
    print(f"        Readable, so ClauseCI will work — but upload the PDFs from {owner}.")
if not writable:
    print("  NOTE: this account cannot add files here.")
E.set_env_key("CONTRACT_DRIVE_FOLDER_ID", f["id"])
