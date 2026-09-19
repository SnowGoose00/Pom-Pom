"""Guard rail: nothing that looks like a credential may be committed.

The full audit (history included) is ``scripts/check_secrets.py``; this test
keeps the cheap working-tree check running with the normal test suite, so a key
pasted into a file gets caught before it is ever committed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_secrets import scan_tracked_files  # noqa: E402

FAKE_KEY = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.test",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.test",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


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


def test_scanner_catches_a_key_that_only_lives_in_the_index(tmp_path):
    """`git add` then editing the key out of the worktree still commits the key."""
    repo = _repo(tmp_path)
    config = repo / "config.txt"
    config.write_text(f"LLM_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    _git(repo, "add", "config.txt")
    config.write_text("LLM_API_KEY=\n", encoding="utf-8")  # worktree looks clean now

    findings = scan_tracked_files(repo)

    assert findings, "暂存区里的密钥必须被抓到"


def test_scanner_reads_non_ascii_filenames(tmp_path):
    """git ls-files escapes non-ASCII names; the scan must not skip those files."""
    repo = _repo(tmp_path)
    (repo / "配置.txt").write_text(f"LLM_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    _git(repo, "add", "配置.txt")

    findings = scan_tracked_files(repo)

    assert findings, "中文文件名不能被跳过"


def test_scanner_ignores_untracked_files(tmp_path):
    """A key in a file that is not staged is not going to be committed."""
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f"LLM_API_KEY={FAKE_KEY}\n", encoding="utf-8")

    assert scan_tracked_files(repo) == []
