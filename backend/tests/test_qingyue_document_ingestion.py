"""清岳案例文档分类与关键字段补抽回归测试。"""
import os
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.field_mapper import enrich_fields_from_text  # noqa: E402
from services.procurement_audit_rules import build_facts_from_rows  # noqa: E402
from services.task_worker import _classify_for_table  # noqa: E402
from services.task_worker import ingest_preextracted_document  # noqa: E402


class QingyueDocumentIngestionTests(unittest.TestCase):
    def test_case_documents_are_routed_to_rule_readable_tables(self):
        cases = {
            "02_B01_S01报价函_2025-03-12.pdf": "采购类",
            "05_B01供应商资格审查表_2025-03-14.pdf": "采购类",
            "06_B01评审记录及成交意见_2025-03-14.pdf": "采购类",
            "01_B01成交通知书_2025-03-17.pdf": "采购类",
            "01_B03设备送货清单_2025-07-22.pdf": "登记台账类",
            "02_B03安装调试记录_2025-07-24.pdf": "登记台账类",
            "03_B03项目验收报告_2025-07-18.pdf": "登记台账类",
            "02_PAY-B01-001付款申请及审批单_2025-04-25.pdf": "财务凭证类",
            "06_PAY-B01-001模拟银行电子回单_2025-04-25.pdf": "财务凭证类",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(expected, _classify_for_table("", filename))

    def test_enrichment_extracts_supplier_contact_and_role(self):
        text = (
            "锦川智联科技有限公司报价函\n"
            "报价编号：S01-B01-2025\n"
            "联系人：何嘉树；联系电话：TEST-028-80001234；"
            "电子邮箱：test-bid@jczhilian.example；联系地址：测试地址。\n"
            "报价人：锦川智联科技有限公司（模拟测试专用章）\n2025-03-12"
        )
        fields = enrich_fields_from_text({}, text, "02_B01_S01报价函_2025-03-12.pdf")

        self.assertEqual("supplier_response", fields["文档角色"])
        self.assertEqual("B01", fields["批次编号"])
        self.assertEqual("S01", fields["供应商编号"])
        self.assertEqual("TEST-028-80001234", fields["联系电话"])
        self.assertEqual("test-bid@jczhilian.example", fields["电子邮箱"])

    def test_enrichment_extracts_finance_linkage_fields(self):
        text = (
            "采购资金付款申请及审批单\n付款申请编号：PAY-B02-002\n"
            "申请支付第二批办公电脑补充采购追加设备款人民币166,752.00元。\n"
            "合同编号 | QYZW-CG-2025-026 | 付款性质 | 追加设备款\n"
            "申请金额 | 166,752.00 | 发票号码 | TEST-510025000002\n2025-06-18"
        )
        fields = enrich_fields_from_text({}, text, "04_PAY-B02-002付款申请及审批单_2025-06-18.pdf")

        self.assertEqual("payment_application", fields["文档角色"])
        self.assertEqual("B02", fields["批次编号"])
        self.assertEqual("PAY-B02-002", fields["付款申请编号"])
        self.assertEqual("QYZW-CG-2025-026", fields["合同编号"])
        self.assertEqual("166,752.00", fields["申请金额"])
        self.assertEqual("TEST-510025000002", fields["发票号码"])
        self.assertEqual("2025-06-18", fields["文档日期"])

    def test_filename_supplies_sequence_date_and_document_role(self):
        cases = [
            ("01_B03设备送货清单_2025-07-22.pdf", "delivery", "送货日期", "2025-07-22"),
            ("02_B03安装调试记录_2025-07-24.pdf", "installation", "安装日期", "2025-07-24"),
            ("03_B03项目验收报告_2025-07-18.pdf", "acceptance", "验收日期", "2025-07-18"),
            ("02_B02设备采购合同_2025-05-20.pdf", "contract", "签订日期", "2025-05-20"),
        ]
        for filename, role, date_field, expected_date in cases:
            with self.subTest(filename=filename):
                fields = enrich_fields_from_text({}, "测试正文", filename)
                self.assertEqual(role, fields["文档角色"])
                self.assertEqual("B03" if "B03" in filename else "B02", fields["批次编号"])
                self.assertEqual(expected_date, fields[date_field])

    def test_structured_rows_are_normalized_into_cross_document_facts(self):
        rows_by_table = {
            "data_procurements": [{
                "document_trace_id": 1,
                "doc_name": "003_2025年度信息化设备采购计划_2025-02-20.pdf",
                "extra_fields": {"文档角色": "annual_plan"},
                "raw_text": (
                    "本项目预算控制数为人民币4,400,000.00元。\n"
                    "B01 | 第一批办公电脑 | 询价采购 | 1,462,800.00 | 2025-03\n"
                    "B02 | 第二批办公电脑 | 询价采购 | 1,389,600.00 | 2025-05\n"
                    "B03 | 第三批终端设备 | 询价采购 | 1,336,400.00 | 2025-07"
                ),
            }, {
                "document_trace_id": 2,
                "doc_name": "02_B01_S01报价函_2025-03-12.pdf",
                "supplier": "锦川智联科技有限公司",
                "extra_fields": {"文档角色": "supplier_response", "批次编号": "B01",
                                 "供应商编号": "S01", "联系电话": "TEST-028-80001234",
                                 "电子邮箱": "test-bid@jczhilian.example"},
                "raw_text": "",
            }],
            "data_contracts": [{
                "document_trace_id": 3,
                "doc_name": "02_B02设备采购合同_2025-05-20.pdf",
                "contract_no": "QYZW-CG-2025-026",
                "amount": 1_389_600,
                "sign_date": "2025-05-20",
                "extra_fields": {"文档角色": "contract", "批次编号": "B02"},
            }],
            "data_registers": [{
                "document_trace_id": 4,
                "doc_name": "01_B02设备送货清单_2025-05-18.pdf",
                "extra_fields": {"文档角色": "delivery", "批次编号": "B02",
                                 "送货日期": "2025-05-18"},
            }],
            "data_finance": [{
                "document_trace_id": 5,
                "doc_name": "04_PAY-B02-002付款申请及审批单_2025-06-18.pdf",
                "extra_fields": {"文档角色": "payment_application", "批次编号": "B02",
                                 "付款申请编号": "PAY-B02-002", "合同编号": "QYZW-CG-2025-026",
                                 "申请金额": "166,752.00", "发票号码": "TEST-510025000002"},
                "raw_text": "申请支付追加设备款人民币166,752.00元",
            }],
        }

        facts = build_facts_from_rows(rows_by_table)

        self.assertEqual(4_400_000.0, facts["annual_plan"][0]["budget_amount"])
        self.assertEqual(["B01", "B02", "B03"],
                         [row["batch_no"] for row in facts["procurement_batches"]])
        self.assertEqual("TEST-028-80001234", facts["suppliers"][0]["phone"])
        self.assertEqual("B02", facts["contracts"][0]["batch_no"])
        self.assertEqual("2025-05-18", facts["deliveries"][0]["delivery_date"])
        self.assertEqual(166_752.0, facts["contract_additions"][0]["addition_amount"])
        self.assertEqual("PAY-B02-002", facts["finance"][0]["voucher_no"])

    def test_contract_amount_falls_back_to_pdf_text_layer(self):
        facts = build_facts_from_rows({
            "data_procurements": [], "data_registers": [], "data_finance": [],
            "data_contracts": [{
                "document_trace_id": 3,
                "doc_name": "02_B02设备采购合同_2025-05-20.pdf",
                "amount": None,
                "raw_text": "合同含税总价为人民币1,389,600.00元，已包括运输费用。",
                "extra_fields": {"批次编号": "B02"},
            }],
        })

        self.assertEqual(1_389_600.0, facts["contracts"][0]["amount"])

    def test_preextracted_ingestion_builds_traceable_row(self):
        with mock.patch("services.task_worker._insert_into_data_table", return_value=77) as insert_row, \
             mock.patch("services.task_worker._build_field_sources") as build_sources:
            result = ingest_preextracted_document(
                project_id="project-1",
                trace_id=9,
                filename="03_B03项目验收报告_2025-07-18.pdf",
                text="第三批设备采购项目验收报告",
                fields={},
                parse_engine="pypdf",
                persist_trace=False,
            )

        self.assertEqual("data_registers", result["table"])
        self.assertEqual(77, result["row_id"])
        args = insert_row.call_args.args
        self.assertEqual(9, args[2])
        # 文档角色/验收日期 经 field_mapper 映射进标准列(register_type/register_date)，落在 row_dict(args[5])
        self.assertEqual("acceptance", args[5]["register_type"])
        self.assertEqual("2025-07-18", args[5]["register_date"])
        build_sources.assert_called_once()


if __name__ == "__main__":
    unittest.main()
