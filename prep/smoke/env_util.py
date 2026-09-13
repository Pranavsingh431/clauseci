"""Tiny .env helpers. Never prints secret values."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(ROOT, ".env")


def load():
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)


def need(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        raise SystemExit(f"FATAL: {key} is missing or empty in .env")
    return v


def mask(v: str) -> str:
    if not v:
        return "<empty>"
    return f"{v[:6]}…{v[-3:]} (len={len(v)})"


def set_env_key(key: str, value: str) -> None:
    """Write/replace a single key in .env, leaving every other line untouched."""
    with open(ENV_PATH) as fh:
        lines = fh.read().splitlines()
    pat = re.compile(rf"^{re.escape(key)}=")
    found = False
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    with open(ENV_PATH, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.environ[key] = value
    print(f"    .env updated: {key}={value}")
