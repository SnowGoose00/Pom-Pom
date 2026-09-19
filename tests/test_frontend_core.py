"""Run the browser-side state tests (static/chat-core.js) from pytest."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "frontend_core.test.js"


def test_frontend_conversation_state():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 未安装，跳过前端状态测试")
    result = subprocess.run(
        [node, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
