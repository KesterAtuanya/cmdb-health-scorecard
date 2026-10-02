import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cmdb_health.cli import main
from cmdb_health.sources.servicenow import ServiceNowClient, ServiceNowError


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session and replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.auth = None
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


def ci_row(i, **kw):
    row = {"sys_id": f"id{i}", "name": f"hou-win-app-{i:02d}", "sys_class_name": "cmdb_ci_win_server",
           "owned_by": {"value": "u1", "link": "x"}, "support_group": "g1", "operational_status": "1",
           "install_status": "1", "last_discovered": "2026-09-30 10:00:00"}
    row.update(kw)
    return row


class ClientTests(unittest.TestCase):
    def test_instance_name_is_expanded(self):
        c = ServiceNowClient("dev12345", "u", "p", session=FakeSession([]))
        self.assertEqual(c.base, "https://dev12345.service-now.com")

    def test_requires_credentials(self):
        with self.assertRaises(ServiceNowError):
            ServiceNowClient("dev12345", session=FakeSession([]))

    def test_pagination_reads_every_page(self):
        s = FakeSession([
            FakeResponse(200, {"result": [ci_row(1), ci_row(2)]}),
            FakeResponse(200, {"result": [ci_row(3)]}),
        ])
        c = ServiceNowClient("dev1", "u", "p", page_size=2, session=s)
        rows = list(c.iter_table("cmdb_ci", ["sys_id"]))
        self.assertEqual(len(rows), 3)
        self.assertEqual(s.calls[1][1]["sysparm_offset"], 2)

    @mock.patch("cmdb_health.sources.servicenow.time.sleep")
    def test_retries_when_throttled(self, sleep):
        s = FakeSession([FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200, {"result": []})])
        c = ServiceNowClient("dev1", "u", "p", session=s)
        self.assertEqual(list(c.iter_table("cmdb_ci", ["sys_id"])), [])
        sleep.assert_called_once_with(1.0)

    def test_bad_credentials_message(self):
        c = ServiceNowClient("dev1", "u", "p", session=FakeSession([FakeResponse(401)]))
        with self.assertRaisesRegex(ServiceNowError, "credentials"):
            list(c.iter_table("cmdb_ci", ["sys_id"]))

    def test_load_maps_rows_and_reference_fields(self):
        s = FakeSession([
            FakeResponse(200, {"result": [ci_row(1), ci_row(2)]}),  # cmdb_ci
            FakeResponse(200, {"result": [{"sys_id": "r1", "parent": "id1", "child": "id2", "type": "x"}]}),
        ])
        data = ServiceNowClient("dev1", "u", "p", session=s).load()
        self.assertEqual(len(data.cis), 2)
        self.assertEqual(data.cis[0].owned_by, "u1")
        self.assertEqual(data.cis[0].last_discovered.year, 2026)
        self.assertEqual(len(data.relationships), 1)


class CliTests(unittest.TestCase):
    def test_demo_writes_all_three_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("sys.stdout"):
                code = main(["--demo", "--out", tmp])
            self.assertEqual(code, 0)
            for name in ("cmdb_health_report.html", "cmdb_findings.csv", "cmdb_health_summary.json"):
                self.assertTrue((Path(tmp) / name).exists(), name)
            summary = json.loads((Path(tmp) / "cmdb_health_summary.json").read_text())
            self.assertTrue(0 < summary["score"] < 100)

    def test_fail_under_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                self.assertEqual(main(["--demo", "--out", tmp, "--fail-under", "99"]), 1)


if __name__ == "__main__":
    unittest.main()
