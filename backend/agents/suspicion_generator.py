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
                    lines.append(f"  - {sf.get('issue_description', '')}"
                                 f"（涉及: {sf.get('involved_amount', '未量化')}）")
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
