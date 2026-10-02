import unittest
from datetime import datetime, timedelta

from cmdb_health.checks import (
    check_completeness, check_duplicates, check_naming, check_relationships,
    check_staleness, check_status, normalize_name,
)
from cmdb_health.config import load_rules
from cmdb_health.models import CI, Dataset, Relationship
from cmdb_health.scoring import build_scorecard, grade_for

NOW = datetime(2026, 10, 1, 9, 0)


def ci(sys_id, **kw):
    base = dict(
        name=f"hou-win-app-{sys_id[-2:]}", sys_class_name="cmdb_ci_win_server",
        serial_number=f"SN{sys_id}", ip_address="10.0.0.1", location="HOU",
        owned_by="u1", support_group="g1", last_discovered=NOW - timedelta(days=1),
    )
    base.update(kw)
    return CI(sys_id=sys_id, **base)


def ds(cis, rels=()):
    return Dataset(list(cis), list(rels), "test", NOW)


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules(None)

    def test_complete_ci_passes(self):
        r = check_completeness(ds([ci("a01")]), self.rules)
        self.assertEqual(r.findings, [])
        self.assertEqual(r.applicable, 1)

    def test_missing_support_group_is_high(self):
        r = check_completeness(ds([ci("a01", support_group="")]), self.rules)
        self.assertEqual(len(r.findings), 1)
        self.assertEqual(r.findings[0].severity, "high")
        self.assertIn("Support group", r.findings[0].message)

    def test_vendor_placeholder_serial_counts_as_missing(self):
        r = check_completeness(ds([ci("a01", serial_number="To Be Filled By O.E.M.")]), self.rules)
        self.assertIn("Serial number", r.findings[0].message)

    def test_retired_cis_are_skipped(self):
        r = check_completeness(ds([ci("a01", support_group="", install_status="7")]), self.rules)
        self.assertEqual(r.applicable, 0)


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules(None)

    def test_serial_match_ignores_case_and_spaces(self):
        old = ci("a01", serial_number="VMW-ABC123", last_discovered=NOW - timedelta(days=200))
        new = ci("a02", serial_number=" vmw-abc123 ")
        r = check_duplicates(ds([old, new]), self.rules)
        self.assertEqual([f.ci_sys_id for f in r.findings], ["a01"])  # the stale one is the duplicate

    def test_placeholder_serials_are_not_duplicates(self):
        a = ci("a01", serial_number="Default string")
        b = ci("a02", serial_number="Default string")
        self.assertEqual(check_duplicates(ds([a, b]), self.rules).findings, [])

    def test_fqdn_and_short_name_match(self):
        self.assertEqual(normalize_name("HOU-WIN-SQL-01.corp.local"), "hou-win-sql-01")
        a = ci("a01", name="hou-win-sql-01")
        b = ci("a02", name="HOU-WIN-SQL-01.corp.local", last_discovered=NOW - timedelta(days=50))
        r = check_duplicates(ds([a, b]), self.rules)
        self.assertEqual(len(r.findings), 1)
        self.assertEqual(r.findings[0].ci_sys_id, "a02")


class StalenessTests(unittest.TestCase):
    def test_old_and_never_discovered(self):
        rules = load_rules(None)
        data = ds([
            ci("a01"),
            ci("a02", last_discovered=NOW - timedelta(days=100)),
            ci("a03", last_discovered=NOW - timedelta(days=400)),
            ci("a04", last_discovered=None),
        ])
        r = check_staleness(data, rules)
        sev = {f.ci_sys_id: f.severity for f in r.findings}
        self.assertEqual(sev, {"a02": "medium", "a03": "high", "a04": "medium"})

    def test_threshold_is_configurable(self):
        rules = load_rules(None)
        rules["stale_after_days"] = 365
        r = check_staleness(ds([ci("a02", last_discovered=NOW - timedelta(days=100))]), rules)
        self.assertEqual(r.findings, [])


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules(None)

    def test_orphan_application(self):
        app = ci("p01", name="Billing", sys_class_name="cmdb_ci_appl", managed_by="u2")
        r = check_relationships(ds([app]), self.rules)
        self.assertIn("orphan", r.findings[0].message)

    def test_dependency_on_retired_server(self):
        app = ci("p01", name="Billing", sys_class_name="cmdb_ci_appl", managed_by="u2")
        old = ci("s01", install_status="7", operational_status="6")
        r = check_relationships(ds([app, old], [Relationship("r1", "p01", "s01")]), self.rules)
        self.assertEqual(len(r.findings), 1)
        self.assertIn("retired", r.findings[0].message)

    def test_link_to_deleted_ci(self):
        app = ci("p01", name="Billing", sys_class_name="cmdb_ci_appl", managed_by="u2")
        r = check_relationships(ds([app], [Relationship("r1", "p01", "gone")]), self.rules)
        self.assertIn("no longer exists", r.findings[0].message)


class StatusAndNamingTests(unittest.TestCase):
    def test_retired_but_operational(self):
        r = check_status(ds([ci("a01", install_status="7", operational_status="1")]), load_rules(None))
        self.assertEqual(len(r.findings), 1)

    def test_naming_standard(self):
        rules = load_rules(None)
        data = ds([ci("a01", name="hou-win-sql-01"), ci("a02", name="WIN-7F3K2QJ9")])
        r = check_naming(data, rules)
        self.assertEqual([f.ci_sys_id for f in r.findings], ["a02"])


class ScoringTests(unittest.TestCase):
    def test_perfect_cmdb_scores_100(self):
        data = ds([ci("a01"), ci("a02")])
        from cmdb_health.checks import run_checks
        card = build_scorecard(data, run_checks(data, load_rules(None)))
        self.assertEqual(card.score, 100.0)
        self.assertEqual(card.grade, "A")

    def test_grades(self):
        self.assertEqual(grade_for(95), "A")
        self.assertEqual(grade_for(85), "B")
        self.assertEqual(grade_for(59.9), "F")


if __name__ == "__main__":
    unittest.main()
