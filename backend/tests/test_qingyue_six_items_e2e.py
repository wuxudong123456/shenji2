"""清岳案例六事项端到端规则与疑点验收。"""
import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.suspicion_generator import build_deterministic_suspicion_report  # noqa: E402
from services.audit_item_rule_service import get_item_rules  # noqa: E402
from services.execution_planner import build_and_execute  # noqa: E402


PROJECT_ID = "3bf1fcf4fafb"
ITEM_IDS = (182, 183, 184, 185, 186, 187)


class QingyueSixItemsE2E(unittest.TestCase):
    def test_item_binding_counts_and_all_rules_hit(self):
        rules_by_item = [get_item_rules(PROJECT_ID, item_id) for item_id in ITEM_IDS]
        self.assertEqual([1, 1, 1, 2, 1, 1], [len(rules) for rules in rules_by_item])
        violation_ids = [rule["violation_id"] for rules in rules_by_item for rule in rules]

        results = build_and_execute(violation_ids, PROJECT_ID)

        self.assertEqual(7, len(results))
        self.assertTrue(all(r["executable"] for r in results))
        self.assertTrue(all(r["hits"] > 0 for r in results))
        self.assertTrue(all(r["evidence_refs"] for r in results))
        self.assertTrue(all(e["document_trace_id"] for r in results for e in r["evidence_refs"]))

    def test_seven_checks_are_deduplicated_to_six_traceable_findings(self):
        rules = [rule for item_id in ITEM_IDS for rule in get_item_rules(PROJECT_ID, item_id)]
        results = build_and_execute([rule["violation_id"] for rule in rules], PROJECT_ID)

        report = build_deterministic_suspicion_report(results)

        self.assertEqual(6, report["total_suspicions"])
        self.assertEqual(6, len(report["items"]))
        finding_keys = {item["finding_key"] for item in report["items"]}
        self.assertEqual({
            "F01_SPLIT_TENDER", "F02_SIGN_AFTER_DELIVERY",
            "F03_ADDITION_OVER_10_PERCENT", "F04_DUPLICATE_INVOICE",
            "F05_ACCEPT_BEFORE_PERFORMANCE", "F06_SHARED_CONTACT",
        }, finding_keys)
        self.assertTrue(all(item["evidence_refs"] for item in report["items"]))
        by_key = {item["finding_key"]: item for item in report["items"]}
        self.assertEqual("4,188,800.00元", by_key["F01_SPLIT_TENDER"]["involved_amount"])
        self.assertEqual("166,752.00元", by_key["F03_ADDITION_OVER_10_PERCENT"]["involved_amount"])
        self.assertIn("TEST-510025000002", by_key["F04_DUPLICATE_INVOICE"]["description"])


if __name__ == "__main__":
    unittest.main()
