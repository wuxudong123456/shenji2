"""统一规则注册表契约测试。"""
import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.rule_engine_registry import execute_violation_rule, precheck_violation_rule  # noqa: E402
from services import execution_planner  # noqa: E402


class RuleEngineRegistryTests(unittest.TestCase):
    def test_cross_document_precheck_does_not_parse_pseudo_sql(self):
        rule = {
            "violation_id": 90001,
            "executor_type": "procurement_cross_doc",
            "executor_key": "GP-FINANCE-001",
            "result_group_key": "F04_DUPLICATE_INVOICE",
            "threshold": {},
        }
        facts = {"finance": [{"document_trace_id": 1}]}
        with patch("services.rule_engine_registry.load_project_facts", return_value=facts):
            result = precheck_violation_rule(rule, "project-1")

        self.assertEqual("hittable", result["verdict"])
        self.assertEqual("procurement_cross_doc", result["executor_type"])

    def test_cross_document_execution_returns_legacy_compatible_fields(self):
        rule = {
            "violation_id": 90001,
            "violation_name": "重复发票",
            "executor_type": "procurement_cross_doc",
            "executor_key": "GP-FINANCE-001",
            "result_group_key": "F04_DUPLICATE_INVOICE",
            "threshold": {},
        }
        facts = {"finance": [
            {"document_trace_id": 1, "voucher_no": "V1", "invoice_no": "INV-1", "amount": 100},
            {"document_trace_id": 2, "voucher_no": "V2", "invoice_no": "INV-1", "amount": 100},
        ]}
        with patch("services.rule_engine_registry.load_project_facts", return_value=facts):
            result = execute_violation_rule(rule, "project-1")

        self.assertTrue(result["executable"])
        self.assertEqual(1, result["hits"])
        self.assertEqual("F04_DUPLICATE_INVOICE", result["finding_key"])
        self.assertEqual("deterministic", result["judge_source"])
        self.assertEqual(2, len(result["evidence_refs"]))

    def test_execution_planner_dispatches_cross_document_rule(self):
        rule = {
            "violation_id": 90001,
            "violation_name": "重复发票",
            "executor_type": "procurement_cross_doc",
            "executor_key": "GP-FINANCE-001",
            "result_group_key": "F04_DUPLICATE_INVOICE",
        }
        expected = {"violation_id": 90001, "executable": True, "hits": 1, "rows": []}
        with patch("services.audit_item_rule_service.get_violation_rule", return_value=rule), \
             patch("services.rule_engine_registry.execute_violation_rule", return_value=expected) as execute_rule:
            results = execution_planner.build_and_execute([90001], "project-1")

        self.assertEqual([expected], results)
        execute_rule.assert_called_once_with(rule, "project-1")


if __name__ == "__main__":
    unittest.main()
