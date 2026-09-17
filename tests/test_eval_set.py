"""Guard rails for the unified evaluation set (``eval/testset.json``).

The set is the single source of truth for behaviour-level checks. These tests
keep it well-formed and make sure it stays a superset of the older, scattered
probe scripts -- so consolidating them can never silently drop coverage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_acceptance  # noqa: E402
import run_deep_smoke  # noqa: E402

PAYLOAD = json.loads((ROOT / "eval" / "testset.json").read_text(encoding="utf-8"))
CASES = PAYLOAD["cases"]
QUESTIONS = {case["question"] for case in CASES}

ASSERTION_KEYS = (
    "context_any",
    "context_all",
    "context_not",
    "answer_any",
    "answer_all",
    "answer_min",
    "answer_not",
    "answer_corrected",
    "no_answer",
    "refusal",
    "needs_web",
    "min_steps",
    "language",
    "persona",
)


def _legacy(name: str) -> list[dict]:
    return json.loads((SCRIPTS / name).read_text(encoding="utf-8"))


def test_every_case_is_well_formed():
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    for case in CASES:
        assert case["tier"] in {"fast", "llm", "deep"}, case["id"]
        assert case["axis"] in PAYLOAD["axes"], case["id"]
        assert case["question"].strip(), case["id"]
        has_assertion = any(case.get(key) for key in ASSERTION_KEYS)
        assert has_assertion or case.get("no_assertion"), (
            f"{case['id']} has no assertion and is not marked no_assertion"
        )


def test_all_axes_are_covered():
    covered = {case["axis"] for case in CASES}
    assert covered == set(PAYLOAD["axes"])


def test_fast_tier_never_needs_the_model():
    for case in CASES:
        if case["tier"] == "fast":
            assert not case.get("answer_any"), case["id"]
            assert not case.get("persona"), case["id"]


def test_entity_probes_are_still_covered():
    missing = [
        probe["id"]
        for probe in _legacy("entity_probes.json")
        if probe["question"] not in QUESTIONS
    ]
    assert missing == []


def test_plot_probes_are_still_covered():
    missing = [
        probe["id"]
        for probe in _legacy("plot_probes.json")
        if probe["question"] not in QUESTIONS
    ]
    assert missing == []


def test_acceptance_questions_are_still_covered():
    missing = [question for _, question in run_acceptance.QUESTIONS if question not in QUESTIONS]
    assert missing == []


def test_deep_smoke_cases_are_still_covered():
    missing = [question for _, question in run_deep_smoke.CASES if question not in QUESTIONS]
    assert missing == []


def test_known_gaps_carry_an_explanation():
    for case in CASES:
        if "known_gap" in case:
            assert len(case["known_gap"]) > 10, case["id"]
