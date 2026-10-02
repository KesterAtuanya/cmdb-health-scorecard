"""Plain data structures shared by every part of the tool.

Field names mirror the ServiceNow CMDB so that records pulled from the
Table API (cmdb_ci and cmdb_rel_ci) map onto them one to one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ServiceNow choice values for operational_status on cmdb_ci
OPERATIONAL = "1"
NON_OPERATIONAL = "2"
RETIRED_OPERATIONAL = "6"

# ServiceNow choice values for install_status on cmdb_ci
INSTALLED = "1"
RETIRED_INSTALL = "7"


@dataclass
class CI:
    sys_id: str
    name: str
    sys_class_name: str
    serial_number: str = ""
    asset_tag: str = ""
    ip_address: str = ""
    operational_status: str = OPERATIONAL
    install_status: str = INSTALLED
    owned_by: str = ""
    support_group: str = ""
    managed_by: str = ""
    location: str = ""
    discovery_source: str = ""
    last_discovered: Optional[datetime] = None
    sys_updated_on: Optional[datetime] = None

    @property
    def is_operational(self) -> bool:
        return self.operational_status == OPERATIONAL and self.install_status != RETIRED_INSTALL

    @property
    def is_retired(self) -> bool:
        return (
            self.install_status == RETIRED_INSTALL
            or self.operational_status == RETIRED_OPERATIONAL
        )


@dataclass
class Relationship:
    sys_id: str
    parent: str  # sys_id of the parent CI
    child: str  # sys_id of the child CI
    type: str = "Depends on::Used by"


@dataclass
class Finding:
    """One problem found on one CI by one check."""

    check: str
    ci_sys_id: str
    ci_name: str
    ci_class: str
    severity: str  # "high", "medium" or "low"
    message: str
    fix: str


@dataclass
class CheckResult:
    key: str
    title: str
    description: str
    weight: float
    applicable: int  # how many CIs the check looked at
    findings: list[Finding] = field(default_factory=list)

    @property
    def affected(self) -> int:
        return len({f.ci_sys_id for f in self.findings})

    @property
    def pass_rate(self) -> float:
        if self.applicable == 0:
            return 1.0
        return max(0.0, 1.0 - self.affected / self.applicable)


@dataclass
class Dataset:
    cis: list[CI]
    relationships: list[Relationship]
    source: str  # human readable, e.g. "Demo data" or "acme.service-now.com"
    collected_at: datetime
