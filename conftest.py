"""Save a summary of every pytest run into test-results/."""
from __future__ import annotations

import json
import time
from pathlib import Path

RESULT_DIR = Path(__file__).resolve().parent / "test-results"


def pytest_sessionstart(session) -> None:
    session._pom_start_time = time.time()


def pytest_sessionfinish(session, exitstatus) -> None:
    try:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats if reporter is not None else {}
        ts = time.strftime("%Y-%m-%d_%H%M%S")
        failed = stats.get("failed", [])
        start = getattr(session, "_pom_start_time", None)
        duration = round(time.time() - start, 2) if start else 0.0
        record = {
            "timestamp": ts,
            "exit_code": int(exitstatus),
            "tests_collected": session.testscollected,
            "passed": len(stats.get("passed", [])),
            "failed": len(failed),
            "errors": len(stats.get("errors", [])),
            "failed_tests": [report.nodeid for report in failed],
            "duration_seconds": duration,
        }
        json_path = RESULT_DIR / f"pytest-{ts}.json"
        json_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        latest = RESULT_DIR / "latest-pytest.json"
        latest.write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")

        txt = (
            f"pytest run @ {ts}\n"
            f"exit code: {record['exit_code']}\n"
            f"tests: {record['tests_collected']}, "
            f"passed: {record['passed']}, failed: {record['failed']}, "
            f"errors: {record['errors']}\n"
            f"duration: {record['duration_seconds']}s\n"
        )
        if record["failed_tests"]:
            txt += "failed tests:\n" + "\n".join(
                f"- {name}" for name in record["failed_tests"]
            )
        txt_path = RESULT_DIR / f"pytest-{ts}.txt"
        txt_path.write_text(txt, encoding="utf-8")
        (RESULT_DIR / "latest-pytest.txt").write_text(txt, encoding="utf-8")
    except Exception:
        # Never let result-saving break the test run itself.
        pass
