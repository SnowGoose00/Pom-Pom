"""Guard rail: nothing that looks like a credential may be committed.

The full audit (history included) is ``scripts/check_secrets.py``; this test
keeps the cheap working-tree check running with the normal test suite, so a key
pasted into a file gets caught before it is ever committed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_secrets import scan_tracked_files  # noqa: E402


def test_tracked_files_contain_no_looking_credentials():
    findings = scan_tracked_files()
    assert findings == [], "疑似密钥/隐私信息，先处理再提交：\n" + "\n".join(findings)


def test_env_file_is_ignored_but_the_template_is_not():
    import subprocess

    def ignored(path: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", path],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        return result.returncode == 0

    assert ignored(".env"), ".env 必须被 gitignore"
    assert not ignored(".env.example"), ".env.example 必须可以提交"


def test_env_template_carries_no_credential_values():
    """The template may name variables (and public URLs), never a credential."""
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            assert value.strip() == "", f"{name} 在模板里必须是空值"
