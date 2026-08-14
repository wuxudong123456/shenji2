"""清岳办公电脑采购案例：六事项确定性规则契约测试。"""
import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.procurement_audit_rules import (  # noqa: E402
    evaluate_rule,
    precheck_rule,
)


def _case_facts():
    return {
        "annual_plan": [{
            "document_trace_id": 1,
            "doc_name": "003_年度采购预算批复.pdf",
            "budget_amount": 4_400_000,
            "procurement_method": "询价采购",
        }],
        "procurement_batches": [
            {"document_trace_id": 2, "batch_no": "B01", "budget_amount": 1_500_000,
             "procurement_method": "询价采购"},
            {"document_trace_id": 3, "batch_no": "B02", "budget_amount": 1_450_000,
             "procurement_method": "询价采购"},
            {"document_trace_id": 4, "batch_no": "B03", "budget_amount": 1_450_000,
             "procurement_method": "询价采购"},
        ],
        "suppliers": [
            {"document_trace_id": 11, "supplier_code": "S01", "supplier": "川甲公司",
             "phone": "13800001111", "email": "bid@example.test"},
            {"document_trace_id": 12, "supplier_code": "S02", "supplier": "川乙公司",
             "phone": "13800001111", "email": "bid@example.test"},
            {"document_trace_id": 13, "supplier_code": "S03", "supplier": "川丙公司",
             "phone": "13900002222", "email": "other@example.test"},
        ],
        "contracts": [
            {"document_trace_id": 21, "contract_no": "B01", "amount": 1_462_800,
             "sign_date": "2025-04-15"},
            {"document_trace_id": 22, "contract_no": "B02", "amount": 1_389_600,
             "sign_date": "2025-05-20"},
            {"document_trace_id": 23, "contract_no": "B03", "amount": 1_336_400,
             "sign_date": "2025-06-25"},
        ],
        "contract_additions": [{
            "document_trace_id": 24,
            "contract_no": "B02",
            "addition_amount": 166_752,
            "addition_ratio": 0.12,
        }],
        "deliveries": [
            {"document_trace_id": 31, "contract_no": "B02", "delivery_date": "2025-05-18"},
            {"document_trace_id": 32, "contract_no": "B03", "delivery_date": "2025-07-22"},
        ],
        "installations": [{
            "document_trace_id": 33, "contract_no": "B03", "installation_date": "2025-07-24",
        }],
        "acceptances": [{
            "document_trace_id": 34, "contract_no": "B03", "acceptance_date": "2025-07-18",
        }],
        "finance": [
            {"document_trace_id": 41, "voucher_no": "V-B02-001",
             "invoice_no": "TEST-510025000002", "amount": 800_000},
            {"document_trace_id": 42, "voucher_no": "V-B02-002",
             "invoice_no": "TEST-510025000002", "amount": 756_352},
        ],
    }


class ProcurementAuditRuleTests(unittest.TestCase):
    def test_all_seven_rule_bindings_are_executable_and_hit(self):
        facts = _case_facts()
        expected = {
            "GP-PLAN-001": "F01_SPLIT_TENDER",
            "GP-METHOD-001": "F01_SPLIT_TENDER",
            "GP-SUPPLIER-001": "F06_SHARED_CONTACT",
            "GP-CONTRACT-001": "F02_SIGN_AFTER_DELIVERY",
            "GP-CONTRACT-002": "F03_ADDITION_OVER_10_PERCENT",
            "GP-ACCEPT-001": "F05_ACCEPT_BEFORE_PERFORMANCE",
            "GP-FINANCE-001": "F04_DUPLICATE_INVOICE",
        }

        for rule_code, finding_key in expected.items():
            with self.subTest(rule_code=rule_code):
                precheck = precheck_rule(rule_code, facts)
                self.assertEqual("hittable", precheck["verdict"])
                self.assertEqual([], precheck["missing_roles"])

                result = evaluate_rule(rule_code, facts)
                self.assertTrue(result["success"])
                self.assertEqual("deterministic", result["executor_type"])
                self.assertGreater(result["total"], 0)
                self.assertGreater(result["hits"], 0)
                self.assertEqual(finding_key, result["result_group_key"])
                self.assertTrue(result["rows"][0]["evidence"])
                self.assertTrue(all(e.get("document_trace_id") for e in result["rows"][0]["evidence"]))

    def test_missing_required_role_is_reported_instead_of_silent_zero(self):
        facts = _case_facts()
        facts["finance"] = []

        precheck = precheck_rule("GP-FINANCE-001", facts)

        self.assertEqual("missing_data", precheck["verdict"])
        self.assertEqual(["finance"], precheck["missing_roles"])
        result = evaluate_rule("GP-FINANCE-001", facts)
        self.assertFalse(result["success"])
        self.assertEqual("missing_data", result["status"])
        self.assertIn("finance", result["reason"])

    def test_same_finding_key_supports_cross_item_deduplication(self):
        facts = _case_facts()
        plan_result = evaluate_rule("GP-PLAN-001", facts)
        method_result = evaluate_rule("GP-METHOD-001", facts)

        self.assertEqual(plan_result["result_group_key"], method_result["result_group_key"])
        self.assertEqual(plan_result["rows"][0]["hit_key"], method_result["rows"][0]["hit_key"])

    def test_single_invoice_plus_one_voucher_is_not_duplicate_posting(self):
        facts = _case_facts()
        facts["finance"] = [
            {"document_trace_id": 1, "role": "invoice", "invoice_no": "INV-1", "voucher_no": ""},
            {"document_trace_id": 2, "role": "payment_application", "invoice_no": "INV-1", "voucher_no": "PAY-1"},
            {"document_trace_id": 3, "role": "accounting_voucher", "invoice_no": "INV-1", "voucher_no": "V-1"},
        ]

        result = evaluate_rule("GP-FINANCE-001", facts)

        self.assertEqual(0, result["hits"])

    def test_supplier_contact_is_deduplicated_across_procurement_batches(self):
        facts = _case_facts()
        facts["suppliers"].extend([
            {"document_trace_id": 14, "batch_no": "B02", "supplier_code": "S01",
             "supplier": "川甲公司", "phone": "13800001111", "email": "bid@example.test"},
            {"document_trace_id": 15, "batch_no": "B02", "supplier_code": "S02",
             "supplier": "川乙公司", "phone": "13800001111", "email": "bid@example.test"},
        ])

        result = evaluate_rule("GP-SUPPLIER-001", facts)

        self.assertEqual(1, result["hits"])
        self.assertEqual(["川乙公司", "川甲公司"], result["rows"][0]["suppliers"])


if __name__ == "__main__":
    unittest.main()
