"""Default health rules. Every value here can be overridden with a JSON file
passed to --rules, so a team can encode its own CMDB standards."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

DEFAULT_RULES: dict = {
    # Fields that must be filled in, per class. "*" applies to every class.
    "required_fields": {
        "*": ["owned_by", "support_group"],
        "cmdb_ci_win_server": ["serial_number", "ip_address", "location"],
        "cmdb_ci_linux_server": ["serial_number", "ip_address", "location"],
        "cmdb_ci_computer": ["serial_number", "asset_tag", "location"],
        "cmdb_ci_appl": ["managed_by"],
        "cmdb_ci_db_instance": ["managed_by"],
    },
    # Operational CIs not seen by discovery in this many days are stale.
    "stale_after_days": 90,
    # Classes that should always have at least one relationship.
    "must_have_relationships": [
        "cmdb_ci_appl",
        "cmdb_ci_db_instance",
        "cmdb_ci_service",
    ],
    # Classes populated by discovery tools, so a missing last_discovered is a problem.
    "discovered_classes": [
        "cmdb_ci_win_server",
        "cmdb_ci_linux_server",
        "cmdb_ci_computer",
    ],
    # Naming standards as regular expressions, per class.
    "naming_patterns": {
        "cmdb_ci_win_server": r"^[a-z]{3}-win-[a-z0-9]{2,12}-\d{2}$",
        "cmdb_ci_linux_server": r"^[a-z]{3}-lnx-[a-z0-9]{2,12}-\d{2}$",
        "cmdb_ci_computer": r"^(lt|dt)-\d{5}$",
    },
    # Serial numbers that vendors ship as placeholders. They are ignored when
    # looking for duplicates, and flagged as missing serials instead.
    "junk_serials": [
        "to be filled by o.e.m.",
        "default string",
        "system serial number",
        "none",
        "n/a",
        "0",
        "0000000000",
        "123456789",
        "not specified",
    ],
    # How much each check counts toward the overall score.
    "weights": {
        "completeness": 25,
        "duplicates": 20,
        "staleness": 20,
        "relationships": 15,
        "status": 10,
        "naming": 10,
    },
}


def load_rules(path: str | Path | None) -> dict:
    rules = deepcopy(DEFAULT_RULES)
    if not path:
        return rules
    with open(path, encoding="utf-8") as fh:
        custom = json.load(fh)
    for key, value in custom.items():
        if isinstance(value, dict) and isinstance(rules.get(key), dict):
            rules[key].update(value)
        else:
            rules[key] = value
    return rules
