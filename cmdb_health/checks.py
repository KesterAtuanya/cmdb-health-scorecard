"""The health checks. Each one takes the dataset and rules and returns a
CheckResult listing every CI it looked at and every problem it found."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from typing import Callable

from .models import CI, CheckResult, Dataset, Finding, OPERATIONAL, RETIRED_INSTALL, RETIRED_OPERATIONAL

FIELD_LABELS = {
    "owned_by": "Owned by",
    "support_group": "Support group",
    "managed_by": "Managed by",
    "serial_number": "Serial number",
    "asset_tag": "Asset tag",
    "ip_address": "IP address",
    "location": "Location",
}


def _finding(check: str, ci: CI, severity: str, message: str, fix: str) -> Finding:
    return Finding(check, ci.sys_id, ci.name, ci.sys_class_name, severity, message, fix)


def normalize_serial(value: str) -> str:
    return re.sub(r"[\s\-_.]", "", (value or "").strip().lower())


def normalize_name(value: str) -> str:
    """Lowercase and drop a DNS suffix, so 'HOU-WIN-SQL-01.corp.local'
    and 'hou-win-sql-01' count as the same host."""
    name = (value or "").strip().lower()
    return name.split(".")[0] if name else ""


def is_junk_serial(value: str, rules: dict) -> bool:
    cleaned = (value or "").strip().lower()
    return cleaned in {s.lower() for s in rules["junk_serials"]}


# ---------------------------------------------------------------- checks


def check_completeness(data: Dataset, rules: dict) -> CheckResult:
    result = CheckResult(
        "completeness",
        "Required fields",
        "Ownership, support group and identifying fields that your standards require.",
        rules["weights"]["completeness"],
        0,
    )
    required = rules["required_fields"]
    for ci in data.cis:
        if ci.is_retired:
            continue
        result.applicable += 1
        fields = list(dict.fromkeys(required.get("*", []) + required.get(ci.sys_class_name, [])))
        missing = []
        for f in fields:
            value = getattr(ci, f, "")
            if not value or (f == "serial_number" and is_junk_serial(value, rules)):
                missing.append(f)
        if missing:
            labels = ", ".join(FIELD_LABELS.get(f, f) for f in missing)
            ownership_gap = any(f in ("owned_by", "support_group") for f in missing)
            result.findings.append(
                _finding(
                    "completeness",
                    ci,
                    "high" if ownership_gap else "medium",
                    f"Missing {labels}",
                    "Fill in the missing fields, or set a default through a data policy or the discovery pattern.",
                )
            )
    return result


def check_duplicates(data: Dataset, rules: dict) -> CheckResult:
    result = CheckResult(
        "duplicates",
        "Duplicates",
        "CIs that share a serial number, or share a host name within the same class.",
        rules["weights"]["duplicates"],
        0,
    )
    active = [ci for ci in data.cis if not ci.is_retired]
    result.applicable = len(active)

    def keeper(group: list[CI]) -> CI:
        # Keep the record discovery saw most recently; the rest are the duplicates.
        def ts(d):
            return d.timestamp() if d else 0.0

        return max(group, key=lambda c: (ts(c.last_discovered), ts(c.sys_updated_on)))

    by_serial: dict[str, list[CI]] = defaultdict(list)
    by_name: dict[tuple[str, str], list[CI]] = defaultdict(list)
    for ci in active:
        if ci.serial_number and not is_junk_serial(ci.serial_number, rules):
            by_serial[normalize_serial(ci.serial_number)].append(ci)
        if ci.name:
            by_name[(ci.sys_class_name, normalize_name(ci.name))].append(ci)

    flagged: set[str] = set()
    for serial, group in by_serial.items():
        if len(group) < 2:
            continue
        keep = keeper(group)
        for ci in group:
            if ci is keep:
                continue
            flagged.add(ci.sys_id)
            result.findings.append(
                _finding(
                    "duplicates",
                    ci,
                    "high",
                    f"Same serial as {keep.name} ({len(group)} records share it)",
                    f"Merge into {keep.name} with CMDB Duplicate Remediation, then review the identification rule for {ci.sys_class_name}.",
                )
            )
    for (cls, name), group in by_name.items():
        if len(group) < 2:
            continue
        keep = keeper(group)
        for ci in group:
            if ci is keep or ci.sys_id in flagged:
                continue
            result.findings.append(
                _finding(
                    "duplicates",
                    ci,
                    "medium",
                    f"Same host name as another {cls} record ({len(group)} records)",
                    "Confirm it is the same device, then merge. Check whether the duplicate came from a second discovery source.",
                )
            )
    return result


def check_staleness(data: Dataset, rules: dict) -> CheckResult:
    days = int(rules["stale_after_days"])
    result = CheckResult(
        "staleness",
        "Stale records",
        f"Operational discovered CIs not seen by discovery in the last {days} days.",
        rules["weights"]["staleness"],
        0,
    )
    cutoff = data.collected_at - timedelta(days=days)
    for ci in data.cis:
        if not ci.is_operational or ci.sys_class_name not in rules["discovered_classes"]:
            continue
        result.applicable += 1
        if ci.last_discovered is None:
            result.findings.append(
                _finding(
                    "staleness",
                    ci,
                    "medium",
                    "Never discovered",
                    "Confirm the device exists. If it does, add it to a discovery schedule; if not, retire it.",
                )
            )
        elif ci.last_discovered < cutoff:
            age = (data.collected_at - ci.last_discovered).days
            result.findings.append(
                _finding(
                    "staleness",
                    ci,
                    "high" if age > days * 2 else "medium",
                    f"Last discovered {age} days ago",
                    "Check whether the device was decommissioned. Retire it, or fix the discovery credentials or schedule.",
                )
            )
    return result


def check_relationships(data: Dataset, rules: dict) -> CheckResult:
    result = CheckResult(
        "relationships",
        "Relationships",
        "Applications and databases with no relationships, links to missing CIs, and live CIs that depend on retired ones.",
        rules["weights"]["relationships"],
        0,
    )
    by_id = {ci.sys_id: ci for ci in data.cis}
    related: set[str] = set()
    for rel in data.relationships:
        related.add(rel.parent)
        related.add(rel.child)

    must = set(rules["must_have_relationships"])
    looked_at: set[str] = set()

    for ci in data.cis:
        if ci.is_retired:
            continue
        if ci.sys_class_name in must:
            looked_at.add(ci.sys_id)
            if ci.sys_id not in related:
                result.findings.append(
                    _finding(
                        "relationships",
                        ci,
                        "high",
                        "Has no relationships (orphan)",
                        "Map what it runs on and what uses it, for example with Service Mapping or a Depends on::Used by relationship.",
                    )
                )

    for rel in data.relationships:
        parent, child = by_id.get(rel.parent), by_id.get(rel.child)
        if parent and not parent.is_retired:
            looked_at.add(parent.sys_id)
        if parent is None and child is not None and not child.is_retired:
            looked_at.add(child.sys_id)
            result.findings.append(
                _finding("relationships", child, "medium", "Linked to a CI that no longer exists",
                         "Delete the broken cmdb_rel_ci record.")
            )
        elif child is None and parent is not None and not parent.is_retired:
            result.findings.append(
                _finding("relationships", parent, "medium", "Linked to a CI that no longer exists",
                         "Delete the broken cmdb_rel_ci record.")
            )
        elif parent and child and parent.is_operational and child.is_retired:
            result.findings.append(
                _finding(
                    "relationships",
                    parent,
                    "high",
                    f"Depends on retired CI {child.name}",
                    "Point the relationship at the replacement CI, or remove it if the dependency is gone.",
                )
            )
    result.applicable = len(looked_at)
    return result


def check_status(data: Dataset, rules: dict) -> CheckResult:
    result = CheckResult(
        "status",
        "Status conflicts",
        "Install status and operational status that contradict each other.",
        rules["weights"]["status"],
        len(data.cis),
    )
    for ci in data.cis:
        if ci.install_status == RETIRED_INSTALL and ci.operational_status == OPERATIONAL:
            result.findings.append(
                _finding("status", ci, "medium", "Retired, but still marked Operational",
                         "Set Operational status to Retired, or reinstate the CI if it is in use.")
            )
        elif ci.operational_status == RETIRED_OPERATIONAL and ci.install_status != RETIRED_INSTALL:
            result.findings.append(
                _finding("status", ci, "low", "Operational status is Retired, but install status is not",
                         "Align both fields, ideally with a business rule that keeps them in sync.")
            )
    return result


def check_naming(data: Dataset, rules: dict) -> CheckResult:
    patterns = {cls: re.compile(p) for cls, p in rules["naming_patterns"].items()}
    result = CheckResult(
        "naming",
        "Naming standard",
        "Names that don't follow your naming convention for the class.",
        rules["weights"]["naming"],
        0,
    )
    for ci in data.cis:
        pattern = patterns.get(ci.sys_class_name)
        if ci.is_retired or pattern is None:
            continue
        result.applicable += 1
        if not pattern.match(normalize_name(ci.name)):
            result.findings.append(
                _finding("naming", ci, "low", f"'{ci.name}' doesn't match {pattern.pattern}",
                         "Rename to the standard, or fix the hostname at the source so discovery brings it in correctly.")
            )
    return result


ALL_CHECKS: list[Callable[[Dataset, dict], CheckResult]] = [
    check_completeness,
    check_duplicates,
    check_staleness,
    check_relationships,
    check_status,
    check_naming,
]


def run_checks(data: Dataset, rules: dict) -> list[CheckResult]:
    return [check(data, rules) for check in ALL_CHECKS]
