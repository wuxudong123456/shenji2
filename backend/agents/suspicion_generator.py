"""疑点生成专家 Agent — 方案B 子类

核心原则（法规引用可溯源，杜绝幻觉）:
  步骤1 retrieve: 接收上游 audit_analyzer 的分析结果 + regulation_advisor 的已确认法规
  步骤2 invoke:   通过 get_law_detail 校验并补全每条法规的条款原文（确保法规真实存在）
  步骤3 synthesize: LLM 仅把分析结果格式化为结构化疑点报告，法规只能引用已确认清单

关键差异（对比纯 LLM）:
  - 疑点的法规依据只能来自上游已确认的 primary_laws，不得新增
  - 每条法规通过 MCP 工具二次校验存在性（防上游 LLM 误填）
  - 每条疑点携带上游 trace_id，形成疑点→分析→法规→原始数据的完整溯源链
"""
from agents.base import BaseAgent, AgentDefinition


def build_deterministic_suspicion_report(analysis_results: list[dict]) -> dict:
    """把确定性命中按 finding_key 合并为可追溯疑点，不调用 LLM。"""
    groups = {}
    for result in analysis_results or []:
        if not result.get("executable") or result.get("hits", 0) <= 0:
            continue
        key = result.get("finding_key") or result.get("result_group_key")
        if not key:
            continue
        group = groups.setdefault(key, {
            "finding_key": key,
            "violation_ids": [],
            "violation_models": [],
            "rows": [],
            "evidence_refs": [],
        })
        violation_id = result.get("violation_id")
        if violation_id is not None and violation_id not in group["violation_ids"]:
            group["violation_ids"].append(violation_id)
        model = result.get("violation_name") or result.get("rule_code") or ""
        if model and model not in group["violation_models"]:
            group["violation_models"].append(model)
        group["rows"].extend(result.get("rows") or [])
        seen = {e.get("document_trace_id") for e in group["evidence_refs"]}
        for evidence in result.get("evidence_refs") or []:
            if evidence.get("document_trace_id") not in seen:
                group["evidence_refs"].append(evidence)
                seen.add(evidence.get("document_trace_id"))

    items = []
    for idx, key in enumerate(sorted(groups), 1):
        group = groups[key]
        first = group["rows"][0] if group["rows"] else {}
        title, amount = _deterministic_title_amount(key, first)
        description = first.get("summary") or title
        if first.get("invoice_no"):
            description += f"；发票号码：{first['invoice_no']}"
        if first.get("addition_ratio") is not None:
            description += f"；计算比例：{float(first['addition_ratio']) * 100:.2f}%"
        items.append({
            "suspicion_id": f"SP-{idx:04d}",
            "finding_key": key,
            "title": title,
            "risk_level": "medium" if key in ("F05_ACCEPT_BEFORE_PERFORMANCE", "F06_SHARED_CONTACT") else "high",
            "violation_ids": group["violation_ids"],
            "violation_model": "；".join(group["violation_models"]),
            "description": description,
            "involved_amount": amount,
            "involved_period": "2025年",
            "data_source": {"table": "cross_document", "record_id": first.get("hit_key", ""),
                            "trace_anchor": "；".join(e.get("position_anchor") or "" for e in group["evidence_refs"])},
            "legal_basis": [],
            "evidence_chain": [
                f"{e.get('doc_name') or '原始文档'} → PDF文本层 → 结构化字段 → 确定性规则命中"
                for e in group["evidence_refs"]
            ],
            "evidence_refs": group["evidence_refs"],
            "suggested_actions": ["调取原始资料并向被审计单位核实后再作审计定性"],
        })
    high = sum(1 for item in items if item["risk_level"] == "high")
    medium = sum(1 for item in items if item["risk_level"] == "medium")
    return {
        "report_title": "政府采购项目确定性扫描疑点报告",
        "generated_at": "",
        "summary": f"确定性规则形成{len(items)}条待核实疑点，最终定性需履行审计复核程序。",
        "total_suspicions": len(items),
        "high_risk_count": high,
        "medium_risk_count": medium,
        "low_risk_count": len(items) - high - medium,
        "items": items,
    }


def _deterministic_title_amount(key: str, row: dict) -> tuple[str, str]:
    titles = {
        "F01_SPLIT_TENDER": "拆分采购规避公开招标疑点",
        "F02_SIGN_AFTER_DELIVERY": "合同倒签疑点",
        "F03_ADDITION_OVER_10_PERCENT": "超比例追加采购疑点",
        "F04_DUPLICATE_INVOICE": "发票重复入账疑点",
        "F05_ACCEPT_BEFORE_PERFORMANCE": "验收时间异常疑点",
        "F06_SHARED_CONTACT": "供应商关联疑点",
    }
    amount = ""
    if key == "F01_SPLIT_TENDER" and row.get("batch_total") is not None:
        amount = f"{float(row['batch_total']):,.2f}元"
    elif key == "F03_ADDITION_OVER_10_PERCENT" and row.get("addition_amount") is not None:
        amount = f"{float(row['addition_amount']):,.2f}元"
    elif key == "F04_DUPLICATE_INVOICE" and row.get("amount_total") is not None:
        amount = f"{float(row['amount_total']):,.2f}元"
    return titles.get(key, "审计疑点"), amount


class SuspicionGeneratorAgent(BaseAgent):
    """疑点生成专家 — 知识优先 + 显式工具调用"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        analysis_results = input_data.get("analysis_results", [])
        overall_assessment = input_data.get("overall_assessment", "")
        domain = input_data.get("domain", "")
        item = input_data.get("item", "")

        # 上游已确认的法规（regulation_advisor 输出 / 人工确认的选择）
        primary_laws = input_data.get("primary_laws", [])
        selected_laws = input_data.get("selected_laws", [])

        # ── 步骤2: 校验上游法规真实存在 + 补全条款原文 ──
        verified_laws = self._verify_laws(primary_laws, selected_laws)

        # ── 步骤3: 把分析结果 + 已校验法规交给 LLM 格式化 ──
        lines = [
            "## 审计疑点报告生成任务",
            "",
            "请基于【上游分析结果】和【已校验的真实法规】，生成结构化的审计疑点报告。",
            "每条疑点的法规依据必须且只能引用下列已校验法规，不得编造任何不在此清单中的法规。",
            "严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain or '未指定'}")
        lines.append(f"- 审计事项: {item or '未指定'}")
        lines.append("")

        # 整体评估
        if overall_assessment:
            lines.append("## 整体评估")
            lines.append(overall_assessment)
            lines.append("")

        # 分析结果（疑点的事实来源）
        if analysis_results:
            lines.append("## 上游分析结果（疑点事实来源）")
            for i, ar in enumerate(analysis_results, 1):
                lines.append(f"### 分析项 {i}: {ar.get('violation_model', '未命名')}")
                scan = ar.get("scan_summary", {})
                if scan:
                    lines.append(f"- 扫描记录: {scan.get('total_records', '?')}，"
                                 f"命中: {scan.get('hits', '?')}，"
                                 f"命中率: {scan.get('hit_rate', 0)}")
                if ar.get("anomaly_patterns"):
                    lines.append(f"- 异常模式: {'；'.join(ar['anomaly_patterns'])}")
                if ar.get("severity_assessment"):
                    lines.append(f"- 严重程度: {ar['severity_assessment']}")
                for sf in (ar.get("sample_findings") or [])[:5]:
                    # P1-2: 兼容两种格式 {issue_description} 或 {record_id, fields}
                    desc = sf.get("issue_description", "")
                    if not desc and sf.get("fields"):
                        # 从 fields 构造可读描述
                        fd = sf["fields"]
                        parts = []
                        for fk in ("doc_name", "party_a", "party_b", "amount",
                                   "procurement_method", "voucher_no", "sign_date"):
                            if fk in fd and fd[fk]:
                                parts.append(f"{fk}={fd[fk]}")
                        desc = "记录#" + str(sf.get("record_id", "?")) + ": " + ", ".join(parts[:4])
                    elif not desc:
                        desc = "记录#" + str(sf.get("record_id", "?"))
                    amt = sf.get("involved_amount")
                    if not amt and sf.get("fields"):
                        amt = sf["fields"].get("amount") or sf["fields"].get("debit_amount") or "未量化"
                    lines.append(f"  - {desc}（涉及: {amt}）")
                if ar.get("analysis_conclusion"):
                    lines.append(f"- 结论: {ar['analysis_conclusion']}")
                lines.append("")
        else:
            lines.append("## 上游分析结果")
            lines.append("（无分析结果。请如实输出空报告，不得编造疑点。）")
            lines.append("")

        # 已校验的法规清单（LLM 只能引用这些）
        if verified_laws:
            lines.append("## 已校验的可用法规依据（疑点 legal_basis 只能引用此清单）")
            for law in verified_laws:
                lid = law.get("law_id") or law.get("id", "")
                title = law.get("law_title") or law.get("title", "")
                clause = law.get("applicable_clauses") or law.get("clause_no", "")
                if isinstance(clause, list):
                    clause = "；".join(str(c) for c in clause)
                lines.append(f"- law_id={lid} | 《{title}》 | 条款: {clause or '全文'}")
            lines.append("")
        else:
            lines.append("## 已校验的可用法规依据")
            lines.append("（未提供已校验法规。如分析结果涉及违规，请在疑点中标注"
                         "法规待补充，不得编造法规名称。）")
            lines.append("")

        lines.append("## 输出要求")
        lines.append("1. 每条疑点的 legal_basis.law_title 必须来自上方已校验法规清单")
        lines.append("2. 不得编造清单外的法规")
        lines.append("3. 每条疑点标注数据来源（table + record_id），确保可溯源")
        lines.append("4. 法规无法确定的疑点，标注 violation_nature='法规待补充'")

        return "\n".join(lines)

    def _verify_laws(self, primary_laws: list, selected_laws: list) -> list:
        """通过 MCP 工具校验上游法规真实存在，补全条款原文

        Returns:
            [{law_id, law_title, applicable_clauses, clause_text}] 已校验法规清单
        """
        verified = []
        # 合并去重上游法规（selected_laws 是 ID 列表，primary_laws 是结构化对象）
        candidate_ids = []
        candidate_objs = {}

        for law in (primary_laws or []):
            lid = law.get("law_id") or law.get("id")
            if lid:
                candidate_ids.append(str(lid))
                candidate_objs[str(lid)] = law

        for lid in (selected_laws or []):
            sid = str(lid)
            if sid not in candidate_objs:
                candidate_ids.append(sid)

        # 用 MCP 工具逐条校验
        for lid in candidate_ids[:15]:  # 最多校验 15 部，控制调用量
            res = self.invoke_tool("knowledge-mcp.get_law_detail", {"law_id": lid})
            if not res.get("success"):
                continue
            law_detail = res.get("result") or {}
            if law_detail.get("error"):
                continue  # 法规不存在，跳过（不登记）

            # 登记知识来源（溯源）
            self.add_knowledge_source(
                source="audit_law.sys_core_law_allaudit",
                item_type="law",
                item_id=lid,
                snippet=law_detail.get("title", ""),
            )

            obj = candidate_objs.get(lid, {})
            verified.append({
                "law_id": lid,
                "law_title": law_detail.get("title", ""),
                "applicable_clauses": obj.get("applicable_clauses", ""),
                "clause_text": (law_detail.get("content", "") or "")[:500],
            })

        return verified
