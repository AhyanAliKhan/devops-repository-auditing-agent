from __future__ import annotations

import json
from pathlib import Path

import pytest

from report import ReportValidationError, build_report, load_report, write_report


def test_build_report_normalizes_and_dedupes() -> None:
    report = build_report(
        "https://github.com/example/demo",
        [
            {
                "rule_id": "SEC-001",
                "category": "security",
                "severity": "critical",
                "file": "config.py",
                "line": 17,
                "description": "Hardcoded API credential",
                "evidence": "Credential assigned directly in source code",
                "recommendation": "Use a secrets manager or environment variable",
            },
            {
                "rule_id": "SEC-001",
                "category": "security",
                "severity": "critical",
                "file": "config.py",
                "line": 17,
                "description": "Hardcoded API credential",
                "evidence": "Credential assigned directly in source code",
                "recommendation": "Use a secrets manager or environment variable",
            },
        ],
    )
    assert len(report["violations"]) == 1
    assert report["violations"][0]["severity"] == "critical"


def test_null_file_and_line_allowed() -> None:
    report = build_report(
        "https://github.com/example/demo",
        [
            {
                "rule_id": "HYG-001",
                "category": "repository_hygiene",
                "severity": "medium",
                "file": None,
                "line": None,
                "description": "Missing contribution guidelines",
                "evidence": "No CONTRIBUTING.md or equivalent found after listing files",
                "recommendation": "Add contribution and PR review guidance",
            }
        ],
    )
    assert report["violations"][0]["file"] is None
    assert report["violations"][0]["line"] is None


def test_invalid_severity_rejected() -> None:
    with pytest.raises(ReportValidationError):
        build_report(
            "https://github.com/example/demo",
            [
                {
                    "rule_id": "X-1",
                    "category": "security",
                    "severity": "urgent",
                    "file": None,
                    "line": None,
                    "description": "x",
                    "evidence": "y",
                    "recommendation": "z",
                }
            ],
        )


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    report = build_report(
        "https://github.com/example/demo",
        [
            {
                "rule_id": "CI-001",
                "category": "ci_cd",
                "severity": "high",
                "file": ".github/workflows/ci.yml",
                "line": 3,
                "description": "Overly broad workflow permissions",
                "evidence": "permissions: write-all",
                "recommendation": "Use least-privilege permissions",
            }
        ],
    )
    out = tmp_path / "report.json"
    write_report(report, out)
    loaded = load_report(out)
    assert loaded == report
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"repository", "violations"}
