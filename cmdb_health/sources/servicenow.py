"""Reads CIs and relationships from a ServiceNow instance through the
Table API (GET /api/now/table/{table}).

Credentials come from environment variables so they never land in the repo:
    SN_INSTANCE   e.g. dev12345 or dev12345.service-now.com
    SN_USER / SN_PASSWORD   basic auth, or
    SN_TOKEN               an OAuth bearer token
The account only needs read access (the itil role or cmdb_read) on
cmdb_ci and cmdb_rel_ci.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

from ..models import CI, Dataset, Relationship

CI_FIELDS = [
    "sys_id", "name", "sys_class_name", "serial_number", "asset_tag", "ip_address",
    "operational_status", "install_status", "owned_by", "support_group", "managed_by",
    "location", "discovery_source", "last_discovered", "sys_updated_on",
]
REL_FIELDS = ["sys_id", "parent", "child", "type"]

DEFAULT_CLASSES = [
    "cmdb_ci_win_server",
    "cmdb_ci_linux_server",
    "cmdb_ci_computer",
    "cmdb_ci_appl",
    "cmdb_ci_db_instance",
]


class ServiceNowError(RuntimeError):
    pass


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _value(field) -> str:
    """Reference fields come back as {"value": sys_id, "link": ...} unless
    sysparm_exclude_reference_link is set; handle both shapes."""
    if isinstance(field, dict):
        return str(field.get("value", "") or "")
    return "" if field is None else str(field)


class ServiceNowClient:
    def __init__(self, instance: str, user: str = "", password: str = "", token: str = "",
                 page_size: int = 1000, session=None, timeout: int = 60):
        if not instance:
            raise ServiceNowError("Set SN_INSTANCE to your instance name, for example dev12345.")
        host = instance.replace("https://", "").replace("http://", "").strip("/")
        if "." not in host:
            host += ".service-now.com"
        self.base = f"https://{host}"
        self.host = host
        self.page_size = page_size
        self.timeout = timeout
        if session is None:
            import requests  # only needed for live mode

            session = requests.Session()
        self.session = session
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif user and password:
            self.session.auth = (user, password)
        else:
            raise ServiceNowError("Set SN_USER and SN_PASSWORD, or SN_TOKEN.")

    @classmethod
    def from_env(cls) -> "ServiceNowClient":
        return cls(
            os.environ.get("SN_INSTANCE", ""),
            os.environ.get("SN_USER", ""),
            os.environ.get("SN_PASSWORD", ""),
            os.environ.get("SN_TOKEN", ""),
        )

    def _get(self, table: str, params: dict) -> list[dict]:
        url = f"{self.base}/api/now/table/{table}"
        for attempt in range(5):
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(wait, 30))
                continue
            if resp.status_code == 401:
                raise ServiceNowError("ServiceNow rejected the credentials (401). Check SN_USER/SN_PASSWORD or SN_TOKEN.")
            if resp.status_code == 403:
                raise ServiceNowError(f"The account can't read {table} (403). Give it the itil or cmdb_read role.")
            if resp.status_code >= 400:
                raise ServiceNowError(f"ServiceNow returned {resp.status_code} for {table}: {resp.text[:200]}")
            return resp.json().get("result", [])
        raise ServiceNowError(f"ServiceNow kept throttling requests to {table}. Try again later.")

    def iter_table(self, table: str, fields: list[str], query: str = "") -> Iterator[dict]:
        offset = 0
        while True:
            params = {
                "sysparm_fields": ",".join(fields),
                "sysparm_limit": self.page_size,
                "sysparm_offset": offset,
                "sysparm_exclude_reference_link": "true",
                "sysparm_display_value": "false",
            }
            if query:
                params["sysparm_query"] = query + "^ORDERBYsys_id"
            else:
                params["sysparm_query"] = "ORDERBYsys_id"
            rows = self._get(table, params)
            yield from rows
            if len(rows) < self.page_size:
                return
            offset += self.page_size

    def load(self, classes: list[str] | None = None) -> Dataset:
        classes = classes or DEFAULT_CLASSES
        query = "sys_class_nameIN" + ",".join(classes)
        cis: list[CI] = []
        for row in self.iter_table("cmdb_ci", CI_FIELDS, query):
            cis.append(
                CI(
                    sys_id=_value(row.get("sys_id")),
                    name=_value(row.get("name")),
                    sys_class_name=_value(row.get("sys_class_name")),
                    serial_number=_value(row.get("serial_number")),
                    asset_tag=_value(row.get("asset_tag")),
                    ip_address=_value(row.get("ip_address")),
                    operational_status=_value(row.get("operational_status")) or "1",
                    install_status=_value(row.get("install_status")) or "1",
                    owned_by=_value(row.get("owned_by")),
                    support_group=_value(row.get("support_group")),
                    managed_by=_value(row.get("managed_by")),
                    location=_value(row.get("location")),
                    discovery_source=_value(row.get("discovery_source")),
                    last_discovered=_parse_dt(_value(row.get("last_discovered"))),
                    sys_updated_on=_parse_dt(_value(row.get("sys_updated_on"))),
                )
            )
        ids = {ci.sys_id for ci in cis}
        rels = [
            Relationship(_value(r.get("sys_id")), _value(r.get("parent")), _value(r.get("child")), _value(r.get("type")))
            for r in self.iter_table("cmdb_rel_ci", REL_FIELDS)
        ]
        # Keep relationships that touch at least one CI in scope.
        rels = [r for r in rels if r.parent in ids or r.child in ids]
        # A link to a CI outside the classes we pulled is not "missing";
        # only treat it as broken if that CI really doesn't exist.
        outside = {r.parent for r in rels if r.parent not in ids} | {r.child for r in rels if r.child not in ids}
        existing_outside = self._existing(outside)
        rels = [r for r in rels if (r.parent in ids or r.parent not in existing_outside)
                and (r.child in ids or r.child not in existing_outside)]
        return Dataset(cis, rels, self.host, datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0))

    def _existing(self, sys_ids: set[str]) -> set[str]:
        found: set[str] = set()
        ids = sorted(i for i in sys_ids if i)
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            for row in self.iter_table("cmdb_ci", ["sys_id"], "sys_idIN" + ",".join(chunk)):
                found.add(_value(row.get("sys_id")))
        return found
