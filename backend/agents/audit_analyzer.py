"""审计分析专家 Agent — 方案B 子类

核心原则（数据优先，杜绝扫描结果幻觉）:
  步骤1 retrieve: 通过 MCP 工具查询违规详情（get_violation_detail）
  步骤2 invoke:   对每个违规模型执行表达式扫描（expression-mcp.execute_expression）
                  + 查询法规详情（get_law_detail）
  步骤3 synthesize: LLM 基于真实扫描结果做异常判定与结论格式化

与通用 BaseAgent 的区别:
  - build_prompt 会先行查询违规详情、执行表达式扫描（真实数据，非 LLM 幻觉）
  - 自动识别数据工坊的目标表
  - 所有外部调用统一走 self.invoke_tool()，纳入溯源链
"""
import re
import json
from agents.base import BaseAgent, AgentDefinition
from services import evidence_service


class AuditAnalyzerAgent(BaseAgent):
    """审计分析专家 — 知识优先 + 显式工具调用"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        """构建分析 Prompt — 包含真实表达式扫描结果"""
        # P9-T4: 溯源接线上下文（task_id 来自 graph context，project_id 来自 input）
        # 命中行证据按 vid 暂存，validate_output 时注入各 analysis_result.source_refs
        self._task_id = (context or {}).get("task_id")
        self._project_id = input_data.get("project_id", "")
        self._evidence_by_vid = {}      # {vid: [evidence_entry, ...]}
        self._vid_by_title = {}         # {violation_title: vid}（LLM 输出按 title 反查 vid）

        lines = [
            "## 审计分析任务",
            "",
            "请基于以下违规表达式【真实扫描结果】，逐模型进行比对分析，",
            "生成结构化的审计分析报告。严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        domain = input_data.get("domain", "")
        audit_item = input_data.get("item", "")
        project_id = input_data.get("project_id", "")
        selected_violations = input_data.get("selected_violations", [])

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain or '未指定'}")
        lines.append(f"- 审计事项: {audit_item or '未指定'}")
        lines.append(f"- 项目ID: {project_id or '未指定'}")
        lines.append("")

        # 已确认的法规依据（通过 MCP 查详情，登记溯源）
        selected_laws = input_data.get("selected_laws", [])
        if selected_laws:
            lines.append("## 已确认的法规依据")
            for law_id in selected_laws[:10]:
                res = self.invoke_tool("knowledge-mcp.get_law_detail", {"law_id": str(law_id)})
                if res.get("success"):
                    law = res.get("result") or {}
                    title = law.get("title", law_id)
                    lines.append(f"- 《{title}》（{law.get('potency_level', '')}）")
            lines.append("")

        uploaded_files = input_data.get("uploaded_files", [])
        if uploaded_files:
            lines.append("## 已上传审计资料")
            for f in uploaded_files[:20]:
                lines.append(f"- {f.get('name', f.get('file_name', '未知文件'))}")
            lines.append("")

        # ── 核心：逐违规模型查询详情 + 执行表达式扫描 ──
        lines.append("## 违规表达式扫描结果")
        lines.append("")

        violation_ids = selected_violations if selected_violations else []
        if not violation_ids and input_data.get("matches"):
            violation_ids = [m.get("id") for m in input_data.get("matches", []) if m.get("id")]

        scan_count = 0
        for vid in violation_ids[:20]:
            v = self._get_violation(vid)
            if not v:
                continue

            scan_count += 1
            expr = v.get("expression_text", "")
            violation_title = v.get("violation_title", str(vid))
            severity = v.get("severity", "medium")

            lines.append(f"### 违规模型 {scan_count}: {violation_title}")
            lines.append(f"- 严重程度: {severity}")
            lines.append(f"- 表达式: {expr}" if expr else "- 表达式: 无（手动判断）")
            lines.append("")

            if expr and project_id:
                scan = self._scan_expression(expr, project_id, v, self._task_id)
                if scan.get("success"):
                    lines.append(f"**扫描结果**（表: {scan.get('table', '?')}）:")
                    lines.append(f"- 扫描记录数: {scan.get('total', 0)}")
                    lines.append(f"- 命中记录数: {scan.get('hits', 0)}")
                    lines.append(f"- 命中率: {scan.get('hit_rate', 0)}")
                    hit_rows = scan.get("rows", [])[:5]
                    if hit_rows:
                        lines.append("")
                        lines.append("**命中记录示例**:")
                        for hr in hit_rows:
                            fields = hr.get("fields", {})
                            row_id = hr.get("row_id", "?")
                            key_info = []
                            for k in ["doc_name", "party_a", "party_b", "amount",
                                      "sign_date", "account_name", "voucher_no",
                                      "item_name", "cert_type"]:
                                if k in fields:
                                    key_info.append(f"{k}={fields[k]}")
                            lines.append(f"  - 记录#{row_id}: {', '.join(key_info[:4])}")
                    lines.append("")
                else:
                    lines.append(f"**扫描失败**: {scan.get('error', '未知错误')}")
                    lines.append("")
            else:
                lines.append("**注意**: 缺少表达式或项目ID，无法执行自动扫描。")
                lines.append("")

        if scan_count == 0:
            lines.append("（未提供违规模型ID或未找到匹配的违规定义）")
            lines.append("")

        lines.append("## 分析要求")
        lines.append("1. 对上述每个违规模型的扫描结果进行独立分析")
        lines.append("2. 命中率超过 30% 的模型标注为高风险")
        lines.append("3. 涉及金额大或涉及刑事责任的事项优先标注")
        lines.append("4. 对零命中的模型，说明可能的合理原因")
        lines.append("5. 给出整体评估和后续建议")

        return "\n".join(lines)

    def validate_output(self, output: dict) -> dict:
        """P9-T4: 在基类校验后，把扫描期暂存的证据注入各 analysis_result.source_refs。

        确定性后处理（不依赖 LLM）：按 violation_model(title)→vid 反查 _evidence_by_vid，
        命中即挂 source_refs；未命中的挂全量（兜底，保证 §0「结论必带 source_refs」）。
        幂等：已有 source_refs 不重挂。
        """
        validation = super().validate_output(output)
        results = output.get("analysis_results")
        if not isinstance(results, list) or not results:
            return validation
        all_ev = []
        for evs in self._evidence_by_vid.values():
            all_ev.extend(evs)
        for r in results:
            if not isinstance(r, dict) or r.get("source_refs"):
                continue
            title = r.get("violation_model") or ""
            vid = self._vid_by_title.get(title)
            ev = self._evidence_by_vid.get(vid) if vid is not None else None
            r["source_refs"] = ev if ev else all_ev
        return validation

    def _get_violation(self, vid) -> dict | None:
        """通过 MCP 查询违规详情（支持 ID 或标题）"""
        # 通过 MCP 工具查详情
        if isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit()):
            res = self.invoke_tool("knowledge-mcp.get_violation_detail",
                                   {"violation_id": int(vid)})
            if res.get("success"):
                v = res.get("result") or {}
                if not v.get("error"):
                    self.add_knowledge_source(
                        source="tt.audit_violations",
                        item_type="violation",
                        item_id=v.get("id", vid),
                        snippet=v.get("violation_title", ""),
                    )
                    self._vid_by_title[v.get("violation_title", "")] = v.get("id") or vid
                    return v

        # 通过标题查找
        res = self.invoke_tool("knowledge-mcp.search_violations",
                               {"query": str(vid), "limit": 1})
        if res.get("success"):
            violations = (res.get("result") or {}).get("violations", [])
            if violations:
                first = violations[0]
                detail_res = self.invoke_tool("knowledge-mcp.get_violation_detail",
                                              {"violation_id": first.get("id")})
                if detail_res.get("success"):
                    v = detail_res.get("result") or {}
                    if not v.get("error"):
                        self.add_knowledge_source(
                            source="tt.audit_violations",
                            item_type="violation",
                            item_id=v.get("id"),
                            snippet=v.get("violation_title", ""),
                        )
                        self._vid_by_title[v.get("violation_title", "")] = v.get("id") or vid
                        return v
        return None

    def _scan_expression(self, expression: str, project_id: str,
                         violation: dict | None = None, task_id: str | None = None) -> dict:
        """通过 MCP 执行表达式扫描 + 落结论级溯源（P9-T4）

        命中行逐条连 field_sources→document_chunk，写 audit_source_refs
        (result_type=analysis_hit, result_id=``{task_id}:{violation_id}``)，
        并按 vid 暂存证据，供 validate_output 注入各 analysis_result.source_refs。
        """
        target_table = self._detect_target_table(expression, project_id)
        if not target_table:
            return {"success": False, "error": "未找到匹配的数据表"}

        res = self.invoke_tool("expression-mcp.execute_expression", {
            "expression": expression,
            "table": target_table,
            "project_id": project_id,
        })
        if not res.get("success"):
            return {"success": False, "error": res.get("error")}

        scan = res.get("result") or {}
        # 纳入扫描数据溯源
        if scan.get("hits"):
            self.add_knowledge_source(
                source=f"tt.{target_table}",
                item_type="data_rows",
                item_id=f"{project_id}:{target_table}",
                snippet=f"命中{scan.get('hits', 0)}/{scan.get('total', 0)}条",
            )

        # P9-T4: 命中行 → field_sources → chunk 证据链 + 写 audit_source_refs
        vid = None
        if violation:
            vid = violation.get("id") or violation.get("violation_id")
        rid_key = f"{task_id}:{vid}" if (task_id and vid) else None
        ev_stash = []
        seen_chunks = set()
        for hr in (scan.get("rows") or []):
            rid = hr.get("row_id") or hr.get("id")
            if rid is None:
                continue
            try:
                row_ev = evidence_service.build_field_sources_evidence(
                    project_id, target_table, rid)
            except Exception:
                row_ev = []
            for e in row_ev:
                cid = e.get("chunk_id")
                ev_stash.append(e)
                # 写结论级引用（去重同 chunk；source_of_truth = audit_source_refs）
                if rid_key and cid and cid not in seen_chunks:
                    seen_chunks.add(cid)
                    pages = e.get("page_nums") or []
                    pno = pages[0] if isinstance(pages, list) and pages else None
                    try:
                        evidence_service.add_ref(
                            project_id=project_id,
                            result_type="analysis_hit",
                            result_id=rid_key,
                            source_type="document_chunk",
                            source_id=cid,
                            file_name=e.get("file_name"),
                            page_number=pno,
                            bbox=e.get("bbox"),
                            quote=(e.get("text") or "")[:200],
                            relation="supports",
                        )
                    except Exception:
                        pass
        if vid is not None:
            self._evidence_by_vid.setdefault(vid, [])
            # 暂存紧凑版（供注入 analysis_result.source_refs）
            self._evidence_by_vid[vid].extend([
                {"chunk_id": e.get("chunk_id"), "file_name": e.get("file_name"),
                 "page_nums": e.get("page_nums"), "quote": (e.get("text") or "")[:160]}
                for e in ev_stash
            ])

        return {
            "success": True,
            "table": target_table,
            "total": scan.get("total", 0),
            "hits": scan.get("hits", 0),
            "hit_rate": scan.get("hit_rate", 0),
            "rows": scan.get("rows", []),
        }

    def _detect_target_table(self, expression: str, project_id: str) -> str | None:
        """根据表达式字段自动探测目标数据表"""
        TABLE_SIGNATURES = {
            "data_contracts": ["contract_no", "party_a", "party_b", "procurement_method", "sign_date"],
            "data_finance": ["account_no", "debit_amount", "credit_amount", "voucher_no", "bank_name"],
            "data_legal_docs": ["case_no", "issuing_body", "legal_basis", "verdict"],
            "data_registers": ["register_type", "item_name", "quantity", "responsible_person"],
            "data_credentials": ["cert_type", "cert_no", "holder", "expire_date"],
            "data_procurements": ["procurement_method", "contract_amount", "budget_amount",
                                  "bid_date", "sign_date", "supplier", "doc_name"],
        }

        field_pattern = re.compile(r'([a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)')
        fields_in_expr = set()
        for match in field_pattern.finditer(expression):
            field = match.group(1)
            if field.upper() not in ("AND", "OR", "BETWEEN", "NULL", "TRUE", "FALSE", "LIKE", "IN"):
                fields_in_expr.add(field.lower())

        if not fields_in_expr:
            return "data_contracts"

        best_table = None
        best_score = 0
        for table, signatures in TABLE_SIGNATURES.items():
            score = sum(1 for s in signatures if s.lower() in fields_in_expr)
            if score > best_score:
                best_score = score
                best_table = table

        if best_score >= 1:
            return best_table

        # 回退：检查哪些表在当前项目中实际有数据
        from services.db import query_one
        for table in TABLE_SIGNATURES:
            row = query_one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = %s",
                (project_id,), database="tt",
            )
            if row and row.get("n", 0) > 0:
                return table

        return "data_contracts"
