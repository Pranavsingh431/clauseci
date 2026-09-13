"""
ClauseCI pre-hackathon plumbing verification.

Proves, against the real APIs, that we can:
  GitHub      read a PR + its diff + head SHA; write a commit status; read it back
  Google Drive list the contract folder; download a PDF; extract its text
  Gmail       CREATE a draft; read it back; delete it  (never send)
  Slack       resolve the channel; post; read the message back; delete it
  OpenRouter  return schema-valid structured JSON

Every check VERIFIES BY READING EXTERNAL STATE BACK, never by trusting a 200.

Usage:
    ./.venv/bin/python prep/smoke/smoke.py            # all
    ./.venv/bin/python prep/smoke/smoke.py github     # one
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import env_util as E  # noqa: E402

ROOT = E.ROOT
RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


# ══════════════════════════════════════════════════════════════════ GITHUB
def check_github():
    import requests
    print("\n── GitHub ─────────────────────────────────────────────")
    tok = E.need("GITHUB_TOKEN")
    owner, repo = E.need("GITHUB_OWNER"), E.need("GITHUB_REPO")
    print(f"    token {E.mask(tok)}  repo {owner}/{repo}")
    h = {"Authorization": f"Bearer {tok}",
         "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    api = "https://api.github.com"

    r = requests.get(f"{api}/repos/{owner}/{repo}", headers=h, timeout=30)
    if not record("repo metadata readable", r.status_code == 200,
                  f"HTTP {r.status_code}" + ("" if r.ok else f" {r.text[:120]}")):
        return
    default_branch = r.json()["default_branch"]

    r = requests.get(f"{api}/repos/{owner}/{repo}/pulls?state=open&per_page=50",
                     headers=h, timeout=30)
    if not record("list pull requests", r.status_code == 200, f"HTTP {r.status_code}"):
        return
    prs = r.json()
    record("5 demo PRs present", len(prs) >= 5, f"found {len(prs)}")
    for p in sorted(prs, key=lambda x: x["number"]):
        print(f"         #{p['number']}  {p['head']['sha'][:8]}  {p['title'][:52]}")

    target = next((p for p in prs if p["number"] == 1), prs[0] if prs else None)
    if not target:
        record("PR available for diff test", False)
        return
    num, sha = target["number"], target["head"]["sha"]

    r = requests.get(f"{api}/repos/{owner}/{repo}/pulls/{num}/files", headers=h, timeout=30)
    ok = r.status_code == 200 and len(r.json()) > 0
    record("read changed files for PR", ok, f"{len(r.json()) if r.ok else '?'} file(s)")
    if ok:
        f0 = r.json()[0]
        has_patch = bool(f0.get("patch"))
        record("unified diff (patch) available", has_patch,
               f"{f0['filename']} +{f0.get('additions')}/-{f0.get('deletions')}")

    # Write statuses on the DEFAULT BRANCH head, not the PR head, so the demo
    # PRs stay visually pristine.
    r = requests.get(f"{api}/repos/{owner}/{repo}/commits/{default_branch}", headers=h, timeout=30)
    main_sha = r.json()["sha"]
    ctx = "ClauseCI / smoke-test"
    for state, desc in (("pending", "smoke test: pending"),
                        ("failure", "smoke test: failure"),
                        ("success", "smoke test: success")):
        r = requests.post(f"{api}/repos/{owner}/{repo}/statuses/{main_sha}", headers=h, timeout=30,
                          json={"state": state, "context": ctx, "description": desc})
        if not record(f"POST commit status '{state}'", r.status_code == 201,
                      f"HTTP {r.status_code}" + ("" if r.ok else f" {r.text[:160]}")):
            return
        time.sleep(0.4)

    r = requests.get(f"{api}/repos/{owner}/{repo}/commits/{main_sha}/statuses",
                     headers=h, timeout=30)
    mine = [s for s in r.json() if s["context"] == ctx]
    record("VERIFY status readable back", bool(mine) and mine[0]["state"] == "success",
           f"latest state = {mine[0]['state'] if mine else 'none'}")


# ═════════════════════════════════════════════════════════════════ GOOGLE
def _google_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
              "https://www.googleapis.com/auth/gmail.compose"]
    tp = os.path.join(ROOT, os.environ.get("GOOGLE_TOKEN_PATH", "token.json"))
    if not os.path.exists(tp):
        raise SystemExit(f"FATAL: {tp} not found. Run: ./.venv/bin/python prep/generate_token.py")
    c = Credentials.from_authorized_user_file(tp, SCOPES)
    if c.expired and c.refresh_token:
        c.refresh(Request())
        open(tp, "w").write(c.to_json())
    return c


def check_drive():
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import io
    from pypdf import PdfReader
    print("\n── Google Drive ───────────────────────────────────────")
    creds = _google_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    who = drive.about().get(fields="user(emailAddress,displayName)").execute()["user"]
    record("authorised Google account", True, who["emailAddress"])
    E.set_env_key("GOOGLE_ACCOUNT_EMAIL", who["emailAddress"])
    if not os.environ.get("NOTIFY_EMAIL_TO"):
        E.set_env_key("NOTIFY_EMAIL_TO", who["emailAddress"])

    folder = E.need("CONTRACT_DRIVE_FOLDER_ID")
    try:
        meta = drive.files().get(
            fileId=folder,
            fields="name,owners(emailAddress),capabilities(canAddChildren)").execute()
        owner = meta["owners"][0]["emailAddress"]
        writable = bool(meta["capabilities"].get("canAddChildren"))
        note = f"'{meta['name']}' owned by {owner}"
        if owner != who["emailAddress"]:
            note += f" (shared with {who['emailAddress']}, upload={'ok' if writable else 'DENIED'})"
        record("contract folder reachable", True, note)
        if owner != who["emailAddress"] and not writable:
            record("can add files to folder", False,
                   f"upload as {owner}, or grant {who['emailAddress']} Editor")
    except Exception as e:
        record("contract folder reachable", False, f"{type(e).__name__}: {str(e)[:120]}")
        return

    res = drive.files().list(
        q=f"'{folder}' in parents and trashed=false",
        fields="files(id,name,mimeType,size,modifiedTime)",
        orderBy="name", pageSize=100).execute()
    files = res.get("files", [])
    record("list contract folder", bool(files), f"{len(files)} file(s)")
    for f in files:
        print(f"         {f['name']:<50} {f['mimeType']}")
    record("all 8 contracts uploaded", len(files) >= 8,
           f"{len(files)}/8 — upload demo_contracts/*.pdf to the Drive folder" if len(files) < 8 else "8/8")

    pdfs = [f for f in files if f["mimeType"] == "application/pdf"]
    if not pdfs:
        record("download + extract a contract", False, "no PDFs in folder")
        return
    tgt = next((f for f in pdfs if "AMENDMENT" in f["name"].upper() and "SIGNED" in f["name"].upper()), pdfs[0])
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=tgt["id"]))
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    text = " ".join(" ".join((p.extract_text() or "") for p in PdfReader(buf).pages).split())
    record("download PDF bytes", len(buf.getvalue()) > 1000, f"{tgt['name']} ({len(buf.getvalue())} B)")
    record("extract contract text", len(text) > 400, f"{len(text)} chars")
    if "SIGNED" in tgt["name"].upper():
        record("controlling clause found in text", "thirty (30) days" in text.lower(),
               "'…not be retained for more than thirty (30) days'")


def check_gmail():
    import base64
    from email.message import EmailMessage
    from googleapiclient.discovery import build
    print("\n── Gmail (draft only, never send) ─────────────────────")
    creds = _google_creds()
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)

    to = os.environ.get("NOTIFY_EMAIL_TO") or os.environ.get("GOOGLE_ACCOUNT_EMAIL")
    if not to:
        record("recipient known", False, "NOTIFY_EMAIL_TO unset")
        return

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = "[ClauseCI SMOKE TEST] please ignore"
    msg.set_content("Plumbing verification for ClauseCI. This draft is created, "
                    "read back, and deleted by prep/smoke/smoke.py. Nothing is sent.")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    d = gmail.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    did = d["id"]
    record("drafts.create", bool(did), f"draft id {did}")

    got = gmail.users().drafts().get(userId="me", id=did, format="metadata").execute()
    hdrs = {h["name"]: h["value"] for h in got["message"]["payload"].get("headers", [])}
    record("VERIFY draft readable back", got["id"] == did, f"subject: {hdrs.get('Subject','?')[:48]}")
    labels = got["message"].get("labelIds", [])
    record("draft is UNSENT", "DRAFT" in labels and "SENT" not in labels, f"labels={labels}")

    gmail.users().drafts().delete(userId="me", id=did).execute()
    remaining = gmail.users().drafts().list(userId="me", q="subject:[ClauseCI SMOKE TEST]").execute()
    record("cleanup: draft deleted", not remaining.get("drafts"), "inbox left clean")


# ══════════════════════════════════════════════════════════════════ SLACK
def check_slack():
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    print("\n── Slack ──────────────────────────────────────────────")
    tok = E.need("SLACK_BOT_TOKEN")
    print(f"    token {E.mask(tok)}")
    cl = WebClient(token=tok)

    try:
        a = cl.auth_test()
    except SlackApiError as e:
        record("auth.test", False, str(e.response["error"]))
        return
    record("auth.test", True, f"bot @{a['user']} in {a['team']}")

    cid = os.environ.get("SLACK_ALERT_CHANNEL_ID", "").strip()
    if not cid:
        try:
            chans = cl.conversations_list(types="public_channel", exclude_archived=True, limit=200)["channels"]
        except SlackApiError as e:
            record("conversations.list (needs channels:read)", False, e.response["error"])
            return
        match = (next((c for c in chans if c["name"] == "clauseci-alerts"), None)
                 or next((c for c in chans if "clauseci" in c["name"]), None))
        if not match:
            record("alert channel found", False,
                   "channels seen: " + ", ".join(c["name"] for c in chans[:10]))
            return
        cid = match["id"]
        record("resolved alert channel", True, f"#{match['name']} ({cid})")
        E.set_env_key("SLACK_ALERT_CHANNEL_ID", cid)
    else:
        record("channel id from .env", True, cid)

    try:
        post = cl.chat_postMessage(channel=cid, text="ClauseCI plumbing smoke test — safe to ignore.")
    except SlackApiError as e:
        err = e.response["error"]
        hint = "  <-- run '/invite @ClauseCI' in #clauseci-alerts" if err == "not_in_channel" else ""
        record("chat.postMessage", False, err + hint)
        return
    ts = post["ts"]
    record("chat.postMessage", True, f"ts={ts}")

    try:
        hist = cl.conversations_history(channel=cid, oldest=ts, inclusive=True, limit=5)
        found = any(m.get("ts") == ts for m in hist["messages"])
        record("VERIFY message readable back (channels:history)", found,
               "read-after-write confirmed" if found else "posted but not found")
    except SlackApiError as e:
        record("conversations.history (needs channels:history)", False, e.response["error"])

    try:
        cl.chat_delete(channel=cid, ts=ts)
        record("cleanup: message deleted", True, "channel left clean")
    except SlackApiError as e:
        record("cleanup: message deleted", False, e.response["error"])


# ═════════════════════════════════════════════════════════════ OPENROUTER
def check_openrouter():
    from openai import OpenAI
    print("\n── OpenRouter ─────────────────────────────────────────")
    key = E.need("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
    print(f"    key {E.mask(key)}  model {model}")
    cl = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)

    schema = {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string"},
            "max_retention_days": {"type": "integer"},
            "violation": {"type": "boolean"},
            "clause_quote": {"type": "string"},
        },
        "required": ["customer_id", "max_retention_days", "violation", "clause_quote"],
        "additionalProperties": False,
    }
    try:
        r = cl.chat.completions.create(
            model=model, temperature=0, max_tokens=400,
            messages=[
                {"role": "system",
                 "content": "Extract the controlling retention obligation. Reply only with JSON."},
                {"role": "user",
                 "content": "Customer acme-corp. Contract text: 'Customer Data, including application "
                            "logs, shall not be retained for more than thirty (30) days.' The proposed "
                            "code change sets retention to 90 days. Is that a violation?"},
            ],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "obligation", "strict": True, "schema": schema}},
        )
    except Exception as e:
        record("chat completion", False, f"{type(e).__name__}: {str(e)[:200]}")
        return
    txt = r.choices[0].message.content
    record("chat completion", True, f"{r.usage.total_tokens} tokens")
    try:
        data = json.loads(txt)
    except Exception:
        record("structured output is valid JSON", False, txt[:160])
        return
    record("structured output is valid JSON", True, json.dumps(data)[:120])
    record("schema keys all present", set(schema["required"]).issubset(data), "")
    record("model reasoned correctly", data.get("violation") is True and data.get("max_retention_days") == 30,
           f"violation={data.get('violation')} max_days={data.get('max_retention_days')}")


# ═══════════════════════════════════════════════════════════════════ MAIN
CHECKS = {"github": check_github, "drive": check_drive, "gmail": check_gmail,
          "slack": check_slack, "openrouter": check_openrouter}

if __name__ == "__main__":
    E.load()
    which = sys.argv[1:] or list(CHECKS)
    print("=" * 58)
    print("  ClauseCI — plumbing verification")
    print("=" * 58)
    for name in which:
        fn = CHECKS.get(name)
        if not fn:
            print(f"unknown check '{name}'. choose from: {', '.join(CHECKS)}")
            sys.exit(2)
        try:
            fn()
        except SystemExit as e:
            record(f"{name} (blocked)", False, str(e))
        except Exception as e:
            record(f"{name} (unhandled)", False, f"{type(e).__name__}: {str(e)[:220]}")

    print("\n" + "=" * 58)
    p = sum(1 for _, ok, _ in RESULTS if ok)
    f = len(RESULTS) - p
    print(f"  {p} passed, {f} failed")
    if f:
        print("\n  FAILURES:")
        for n, ok, d in RESULTS:
            if not ok:
                print(f"    - {n}: {d}")
    print("=" * 58)
    sys.exit(1 if f else 0)
