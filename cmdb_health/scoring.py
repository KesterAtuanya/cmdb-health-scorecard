"""Turns check results into an overall 0-100 score, a letter grade and a
per-class breakdown."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .models import CheckResult, Dataset

GRADES = [(90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]

CLASS_LABELS = {
    "cmdb_ci_win_server": "Windows servers",
    "cmdb_ci_linux_server": "Linux servers",
    "cmdb_ci_computer": "Computers",
    "cmdb_ci_appl": "Applications",
    "cmdb_ci_db_instance": "Database instances",
    "cmdb_ci_service": "Services",
    "cmdb_ci_netgear": "Network gear",
}


def grade_for(score: float) -> str:
    for floor, letter in GRADES:
        if score >= floor:
            return letter
    return "F"


def class_label(name: str) -> str:
    return CLASS_LABELS.get(name, name)


@dataclass
class Scorecard:
    score: float
    grade: str
    checks: list[CheckResult]
    total_cis: int
    active_cis: int
    affected_cis: int
    severity_counts: dict[str, int]
    class_rows: list[dict]


def build_scorecard(data: Dataset, checks: list[CheckResult]) -> Scorecard:
    total_weight = sum(c.weight for c in checks if c.applicable) or 1
    score = sum(c.pass_rate * c.weight for c in checks if c.applicable) / total_weight * 100

    all_findings = [f for c in checks for f in c.findings]
    affected = {f.ci_sys_id for f in all_findings}
    severity = Counter(f.severity for f in all_findings)

    per_class_total: Counter = Counter(ci.sys_class_name for ci in data.cis if not ci.is_retired)
    per_class_bad: dict[str, set] = defaultdict(set)
    per_class_issues: Counter = Counter()
    for f in all_findings:
        per_class_bad[f.ci_class].add(f.ci_sys_id)
        per_class_issues[f.ci_class] += 1

    rows = []
    for cls, total in per_class_total.most_common():
        bad = len(per_class_bad.get(cls, set()))
        rows.append(
            {
                "class": cls,
                "label": class_label(cls),
                "total": total,
                "affected": bad,
                "issues": per_class_issues.get(cls, 0),
                "healthy_pct": round(100 * (1 - bad / total), 1) if total else 100.0,
            }
        )
    rows.sort(key=lambda r: r["healthy_pct"])

    return Scorecard(
        score=round(score, 1),
        grade=grade_for(score),
        checks=checks,
        total_cis=len(data.cis),
        active_cis=sum(1 for ci in data.cis if not ci.is_retired),
        affected_cis=len(affected),
        severity_counts={k: severity.get(k, 0) for k in ("high", "medium", "low")},
        class_rows=rows,
    )
