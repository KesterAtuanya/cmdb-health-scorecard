"""Generates a realistic, repeatable sample CMDB for a fictional company,
with the kinds of problems real CMDBs have planted in it. This lets anyone
run the tool without a ServiceNow instance."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from ..models import CI, Dataset, NON_OPERATIONAL, OPERATIONAL, RETIRED_INSTALL, RETIRED_OPERATIONAL, Relationship

SITES = ["hou", "dal", "atl", "chi", "phx"]
WIN_ROLES = ["sql", "app", "web", "file", "dc", "print", "rds", "iis", "sccm", "sftp"]
LNX_ROLES = ["web", "api", "db", "kafka", "redis", "etl", "proxy", "jenkins", "elk", "nfs"]
GROUPS = ["Windows Server Team", "Linux Engineering", "Desktop Support", "Database Admins",
          "App Support - Logistics", "App Support - Finance", "Network Operations"]
OWNERS = ["a.ramirez", "j.chen", "m.okafor", "s.patel", "d.nguyen", "l.walker", "r.johnson", "k.ito"]
APPS = ["Freight Billing", "Dock Scheduler", "Fleet Telematics", "Driver Portal", "Customs Broker Gateway",
        "Warehouse Mgmt", "Route Optimizer", "Payroll Connector", "Carrier EDI Hub", "Claims Tracker",
        "Yard Check-in", "Fuel Card Sync", "Load Board", "Rate Engine", "Proof of Delivery",
        "HR Onboarding", "Vendor Portal", "Invoice OCR", "Maintenance Planner", "Safety Incident Log",
        "Shipment Tracker API", "Pallet Labeler", "Cold Chain Monitor", "Contract Repository",
        "Quote Builder", "Dispatch Board", "Returns Desk", "Inventory Forecast", "BI Reporting Hub",
        "Customer Self-Service", "Toll Reconciliation", "Asset Disposal Log", "Badge Access Sync",
        "Telecom Expense", "Fleet Fuel Analytics", "Driver Training LMS"]
DB_KIND = ["MSSQL", "PostgreSQL", "Oracle", "MySQL"]


class _Gen:
    def __init__(self, seed: int, now: datetime):
        self.r = random.Random(seed)
        self.now = now
        self.cis: list[CI] = []
        self.rels: list[Relationship] = []
        self.used_serials: set[str] = set()

    def sid(self) -> str:
        return uuid.UUID(int=self.r.getrandbits(128)).hex

    def serial(self, prefix: str) -> str:
        while True:
            s = prefix + "".join(self.r.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(8))
            if s not in self.used_serials:
                self.used_serials.add(s)
                return s

    def recent(self, max_days: int = 20) -> datetime:
        return self.now - timedelta(days=self.r.randint(0, max_days), hours=self.r.randint(0, 23))

    def ip(self) -> str:
        return f"10.{self.r.randint(10, 60)}.{self.r.randint(0, 254)}.{self.r.randint(2, 250)}"

    def add(self, **kw) -> CI:
        kw.setdefault("sys_id", self.sid())
        kw.setdefault("sys_updated_on", self.recent(30))
        ci = CI(**kw)
        self.cis.append(ci)
        return ci

    def rel(self, parent: CI | str, child: CI | str, kind: str) -> None:
        p = parent if isinstance(parent, str) else parent.sys_id
        c = child if isinstance(child, str) else child.sys_id
        self.rels.append(Relationship(self.sid(), p, c, kind))


def build_demo_dataset(seed: int = 42, now: datetime | None = None) -> Dataset:
    now = now or datetime(2026, 10, 1, 9, 0, 0)
    g = _Gen(seed, now)
    r = g.r

    # ---- servers -------------------------------------------------------
    servers: list[CI] = []
    for cls, roles, tag, group, prefix, count in [
        ("cmdb_ci_win_server", WIN_ROLES, "win", "Windows Server Team", "VMW-", 110),
        ("cmdb_ci_linux_server", LNX_ROLES, "lnx", "Linux Engineering", "PE", 85),
    ]:
        for i in range(count):
            site, role = r.choice(SITES), r.choice(roles)
            ci = g.add(
                name=f"{site}-{tag}-{role}-{i % 40 + 1:02d}",
                sys_class_name=cls,
                serial_number=g.serial(prefix),
                ip_address=g.ip(),
                owned_by=r.choice(OWNERS),
                support_group=group,
                location=site.upper() + " Data Center",
                discovery_source="ServiceNow",
                last_discovered=g.recent(14),
            )
            servers.append(ci)

    # ---- end-user computers ---------------------------------------------
    computers: list[CI] = []
    for i in range(160):
        kind = "lt" if r.random() < 0.7 else "dt"
        computers.append(
            g.add(
                name=f"{kind}-{10000 + i * 7}",
                sys_class_name="cmdb_ci_computer",
                serial_number=g.serial("5CD"),
                asset_tag=f"P{200000 + i}",
                ip_address=g.ip(),
                owned_by=r.choice(OWNERS),
                support_group="Desktop Support",
                location=r.choice(SITES).upper() + " Office",
                discovery_source="SCCM",
                last_discovered=g.recent(25),
            )
        )

    # ---- applications and databases -------------------------------------
    apps: list[CI] = []
    for name in APPS:
        apps.append(
            g.add(
                name=name,
                sys_class_name="cmdb_ci_appl",
                owned_by=r.choice(OWNERS),
                support_group=r.choice(GROUPS[4:6]),
                managed_by=r.choice(OWNERS),
            )
        )
    dbs: list[CI] = []
    for i in range(24):
        kind = r.choice(DB_KIND)
        dbs.append(
            g.add(
                name=f"{kind.lower()}-{r.choice(SITES)}-{i + 1:02d}",
                sys_class_name="cmdb_ci_db_instance",
                owned_by=r.choice(OWNERS),
                support_group="Database Admins",
                managed_by=r.choice(OWNERS),
            )
        )

    # Healthy relationships: apps run on servers and use databases.
    for app in apps:
        for host in r.sample(servers, r.randint(1, 3)):
            g.rel(app, host, "Runs on::Runs")
        if r.random() < 0.7:
            g.rel(app, r.choice(dbs), "Depends on::Used by")
    for db in dbs:
        g.rel(db, r.choice(servers), "Runs on::Runs")

    # ---- plant the problems a real CMDB has -----------------------------
    pool = servers + computers

    # Missing ownership and identifying fields
    for ci in r.sample(pool, 22):
        ci.support_group = ""
    for ci in r.sample(pool, 14):
        ci.owned_by = ""
    for ci in r.sample(servers, 6):
        ci.location = ""
    for ci in r.sample(computers, 9):
        ci.asset_tag = ""
    for ci in r.sample(pool, 7):
        ci.serial_number = r.choice(["To Be Filled By O.E.M.", "Default string", "System Serial Number", "0"])
    for ci in r.sample(apps, 3):
        ci.managed_by = ""

    # Duplicates: a second discovery source re-created the same device
    for original in r.sample(servers, 7):
        g.add(
            name=original.name.upper(),
            sys_class_name=original.sys_class_name,
            serial_number=original.serial_number.lower(),
            ip_address=original.ip_address,
            owned_by=original.owned_by,
            support_group=original.support_group,
            location=original.location,
            discovery_source="Manual import",
            last_discovered=now - timedelta(days=r.randint(120, 300)),
        )
    for original in r.sample(servers, 4):
        g.add(
            name=original.name + ".corp.northwind.local",
            sys_class_name=original.sys_class_name,
            serial_number=g.serial("VMW-"),
            ip_address=original.ip_address,
            owned_by=original.owned_by,
            support_group=original.support_group,
            location=original.location,
            discovery_source="Agent Client Collector",
            last_discovered=g.recent(10),
        )
    for original in r.sample(computers, 6):
        g.add(
            name=original.name,
            sys_class_name="cmdb_ci_computer",
            serial_number=" " + original.serial_number + " ",
            asset_tag=original.asset_tag,
            owned_by=original.owned_by,
            support_group=original.support_group,
            location=original.location,
            discovery_source="Intune",
            last_discovered=g.recent(5),
        )

    # Stale and never-discovered CIs
    for ci in r.sample(pool, 26):
        ci.last_discovered = now - timedelta(days=r.randint(95, 400))
    for ci in r.sample(computers, 5):
        ci.last_discovered = None
        ci.discovery_source = "Manual entry"

    # Names that break the standard (default Windows names, uppercase, old schemes)
    bad_names = ["WIN-7F3K2QJ9", "HOUSQL01", "dal_web_03", "SERVER2019", "test-box",
                 "DESKTOP-4KT8M1P", "LAPTOP-JCHEN", "chi-win-sql01", "PHX-LNX-API-7"]
    for ci, new in zip(r.sample(pool, len(bad_names)), bad_names):
        ci.name = new

    # Retired infrastructure that live applications still point at
    retired_hosts = r.sample(servers, 8)
    for ci in retired_hosts:
        ci.install_status = RETIRED_INSTALL
        ci.operational_status = RETIRED_OPERATIONAL
    for app in r.sample(apps, 5):
        g.rel(app, r.choice(retired_hosts), "Runs on::Runs")

    # Status conflicts
    for ci in r.sample([c for c in servers if c not in retired_hosts], 5):
        ci.install_status = RETIRED_INSTALL
        ci.operational_status = OPERATIONAL
    for ci in r.sample(computers, 3):
        ci.operational_status = RETIRED_OPERATIONAL
    for ci in r.sample(computers, 6):
        ci.operational_status = NON_OPERATIONAL

    # Orphans: applications and databases nobody mapped
    orphan_ids = {ci.sys_id for ci in r.sample(apps, 4) + r.sample(dbs, 3)}
    g.rels = [rel for rel in g.rels if rel.parent not in orphan_ids and rel.child not in orphan_ids]

    # Relationships pointing at CIs that were deleted
    for app in r.sample([a for a in apps if a.sys_id not in orphan_ids], 3):
        g.rel(app, g.sid(), "Depends on::Used by")

    return Dataset(g.cis, g.rels, "Demo data: Northwind Logistics (fictional)", now)
