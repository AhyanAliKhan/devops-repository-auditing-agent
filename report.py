"""JSON report validation and serialization for the auditing agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}
ALLOWED_CATEGORIES = {
    "repository_hygiene",
    "dependency_management",
    "code_quality",
    "testing",
    "ci_cd",
    "security",
    "configuration",
    "collaboration",
    "build_and_release",
}

REQUIRED_VIOLATION_FIELDS = (
    "rule_id",
    "category",
    "severity",
    "file",
    "line",
    "description",
    "evidence",
    "recommendation",
)


class ReportValidationError(ValueError):
    pass


def normalize_violation(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReportValidationError("Each violation must be an object")

    missing = [f for f in REQUIRED_VIOLATION_FIELDS if f not in raw]
    if missing:
        raise ReportValidationError(f"Violation missing fields: {missing}")

    severity = str(raw["severity"]).strip().lower()
    if severity not in ALLOWED_SEVERITIES:
        raise ReportValidationError(
            f"Invalid severity {raw['severity']!r}. "
            f"Allowed: {sorted(ALLOWED_SEVERITIES)}"
        )

    category = str(raw["category"]).strip().lower().replace(" ", "_").replace("-", "_")
    # Map common aliases from the model
    aliases = {
        "github_actions": "ci_cd",
        "github_actions_ci": "ci_cd",
        "ci": "ci_cd",
        "cicd": "ci_cd",
        "hygiene": "repository_hygiene",
        "repo_hygiene": "repository_hygiene",
        "dependencies": "dependency_management",
        "deps": "dependency_management",
        "build": "build_and_release",
        "release": "build_and_release",
        "config": "configuration",
    }
    category = aliases.get(category, category)
    if category not in ALLOWED_CATEGORIES:
        # Keep original but coerce to a safe bucket rather than failing the run
        category = "configuration"

    file_val = raw["file"]
    if file_val is not None:
        file_val = str(file_val).strip() or None

    line_val = raw["line"]
    if line_val is not None and line_val != "":
        try:
            line_val = int(line_val)
        except (TypeError, ValueError) as exc:
            raise ReportValidationError(f"Invalid line value: {raw['line']!r}") from exc
        if line_val < 1:
            line_val = None
    else:
        line_val = None

    return {
        "rule_id": str(raw["rule_id"]).strip(),
        "category": category,
        "severity": severity,
        "file": file_val,
        "line": line_val,
        "description": str(raw["description"]).strip(),
        "evidence": str(raw["evidence"]).strip(),
        "recommendation": str(raw["recommendation"]).strip(),
    }


def build_report(repository: str, violations: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(violations, list):
        raise ReportValidationError("violations must be a list")
    normalized = [normalize_violation(v) for v in violations]
    # Deduplicate exact rule_id + file + description triples
    seen: set[tuple[str, str | None, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in normalized:
        key = (item["rule_id"], item["file"], item["description"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"repository": repository, "violations": unique}


def write_report(report: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate once more by round-tripping
    validated = build_report(report["repository"], report.get("violations", []))
    path.write_text(json.dumps(validated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_report(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return build_report(data["repository"], data.get("violations", []))
