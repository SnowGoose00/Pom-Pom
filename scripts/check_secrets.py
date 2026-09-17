"""Pre-publish secret audit.

Scans tracked files (and, with ``--history``, every blob in every commit) for
things that must never reach GitHub: API keys, tokens, passwords, private
paths, emails, phone numbers, internal IPs.

    .venv\\Scripts\\python.exe scripts/check_secrets.py             # 提交前跑这个
    .venv\\Scripts\\python.exe scripts/check_secrets.py --history   # 连历史一起查（慢）

Exit code 1 when something is found. Only masked previews are printed.
Mirrors of these checks live in ``tests/test_no_secrets.py``.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Patterns are deliberately narrow: a false alarm costs the same as a missed
# key does, so we match "looks like a literal secret", not "mentions a key".
PATTERNS: dict[str, re.Pattern] = {
    # sk-… (OpenAI / DeepSeek / 博查 …)
    "api-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    "bearer-token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    # KEY=realvalue in a file that is not .env.example
    "env-value": re.compile(
        r"(?m)^\s*[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*(\S{12,})\s*$"
    ),
    # api_key="literal" / token='literal'
    "assigned-literal": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
        r"\s*[:=]\s*[\"']([A-Za-z0-9_\-]{20,})[\"']"
    ),
    "email": re.compile(r"[\w.%+\-]+@[\w\-]+\.[A-Za-z]{2,}"),
    "cn-mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "private-path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+"),
    "private-ip": re.compile(
        r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"
    ),
}

# Things that look like a hit but are documentation or test fixtures.
ALLOW_SUBSTRINGS = (
    "sk-xxx", "sk-your", "your-key", "YOUR_KEY", "你的key", "<key>",
    "example", "Example", "placeholder", "REDACTED", "changeme",
    "test-key", "dummy", "fake", "settings.api_key", "os.environ",
)

SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}
MAX_FILE_BYTES = 2_000_000


def mask(value: str) -> str:
    value = value.strip()
    return f"{value[:4]}{'*' * 6}(len={len(value)})"


def scan_text(text: str, *, label: str) -> list[str]:
    findings: list[str] = []
    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            hit = match.group(0)
            if any(allowed in hit for allowed in ALLOW_SUBSTRINGS):
                continue
            findings.append(f"{label}: {name}: {mask(hit)}")
    return findings


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout


def tracked_files() -> list[str]:
    return [line for line in git("ls-files").splitlines() if line.strip()]


def scan_tracked_files() -> list[str]:
    """Audit what a ``git push`` would actually send."""
    findings: list[str] = []
    for name in tracked_files():
        path = ROOT / name
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings += scan_text(text, label=name)
    return findings


def scan_history() -> list[str]:
    """Audit every blob reachable from any ref -- a key deleted later is still there."""
    findings: list[str] = []
    listing = [line for line in git("rev-list", "--objects", "--all").splitlines() if line]
    for line in listing:
        sha, _, path = line.partition(" ")
        if Path(path).suffix.lower() in SKIP_SUFFIXES:
            continue
        if git("cat-file", "-t", sha).strip() != "blob":
            continue
        content = subprocess.run(
            ["git", "cat-file", "-p", sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout
        if "\x00" in content:  # binary blob (e.g. a stray .exe): strings inside are noise
            continue
        findings += scan_text(content, label=f"{path or sha[:10]}@{sha[:10]}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="also scan every commit")
    args = parser.parse_args()

    print(f"tracked files: {len(tracked_files())}")
    findings = scan_tracked_files()
    if args.history:
        history_findings = scan_history()
        print(f"history findings: {len(history_findings)}")
        findings += history_findings

    if not findings:
        print("no secrets found in tracked files" + (" or history" if args.history else ""))
        return 0
    for finding in findings:
        print("  " + finding)
    print(f"\n{len(findings)} finding(s) -- do not push until these are handled")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
