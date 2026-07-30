"""审计分析 Agent — Agent 子类示范

为什么需要子类而非纯 YAML 配置:
  build_prompt 需要从 MySQL 查询数据工坊表的实际数据行，
  并对每个违规模型执行表达式扫描。这是动态数据查询逻辑，
  纯 YAML 配置无法表达。

这是 6 个 Agent 中唯一需要子类的（其他 5 个纯 YAML 配置已足够）。
其他 Agent 如需自定义 build_prompt，参照此文件创建子类即可。

用法:
    from agents.registry import AgentRegistry
    agent = AgentRegistry().create_agent("audit_analyzer")
    # 自动返回 AuditAnalyzerAgent 实例（而非通用 BaseAgent）
"""
import json
from agents.base import BaseAgent, AgentDefinition
from services.db import query
from services.knowledge_service import get_violation_detail


class AuditAnalyzerAgent(BaseAgent):
    """审计分析专家 — 子类示范

    与通用 BaseAgent 的区别:
      - build_prompt 会先行查询违规详情、执行表达式扫描
      - 将真实的扫描结果（而非 LLM 幻觉）组装到 Prompt 中
      - 支持自动识别数据工坊的目标表
    """

    def build_prompt(self, input_data: dict) -> str:
        """构建分析 Prompt — 包含真实表达式扫描结果

        步骤:
          1. 根据 selected_violations 查询违规详情
          2. 对每个违规模型执行表达式扫描（查询数据工坊表）
          3. 将扫描结果和法规依据组装为结构化 Prompt
        """
        lines = [
            "## 审计分析任务",
            "",
            "请基于以下违规表达式扫描结果，逐模型进行比对分析，",
            "生成结构化的审计分析报告。严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        # 获取审计上下文
        domain = input_data.get("domain", "")
        audit_item = input_data.get("item", "")
        project_id = input_data.get("project_id", "")
        selected_violations = input_data.get("selected_violations", [])

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain}" if domain else "- 审计领域: 未指定")
        lines.append(f"- 审计事项: {audit_item}" if audit_item else "- 审计事项: 未指定")
        lines.append(f"- 项目ID: {project_id}" if project_id else "- 项目ID: 未指定")
        lines.append("")

        # 获取已确认的法规依据
        selected_laws = input_data.get("selected_laws", [])
        if selected_laws:
            lines.append("## 已确认的法规依据")
            for law_id in selected_laws[:10]:
                from services.knowledge_service import get_law_detail
                law = get_law_detail(law_id)
                if law:
                    lines.append(f"- 《{law.get('title', law_id)}》（{law.get('potency_level', '')}）")
            lines.append("")

        # 获取已上传文件信息
        uploaded_files = input_data.get("uploaded_files", [])
        if uploaded_files:
            lines.append("## 已上传审计资料")
            for f in uploaded_files[:20]:
                lines.append(f"- {f.get('name', f.get('file_name', '未知文件'))}")
            lines.append("")

        # ── 核心：逐违规模型执行表达式扫描 ──
        lines.append("## 违规表达式扫描结果")
        lines.append("")

        violation_ids = selected_violations if selected_violations else []
        if not violation_ids and input_data.get("matches"):
            # 如果没传 selected_violations，尝试从 matches 中提取
            violation_ids = [
                m.get("id") for m in input_data.get("matches", [])
                if m.get("id")
            ]

        scan_count = 0
        for vid in violation_ids[:20]:  # 最多处理 20 个违规模型
            v = get_violation_detail(vid) if isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit()) else None
            if not v:
                # 可能是 violation_title，尝试通过标题查找
                from services.knowledge_service import search_violations
                search_results = search_violations(str(vid), limit=1)
                if search_results:
                    v = get_violation_detail(search_results[0]["id"])

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
                # 执行表达式扫描
                try:
                    from services.expression_engine import execute_expression
                    # 自动探测目标数据表
                    target_table = self._detect_target_table(expr, project_id)
                    if target_table:
                        scan = execute_expression(expr, target_table, project_id)
                        if scan.get("success"):
                            lines.append(f"**扫描结果**（表: {target_table}）:")
                            lines.append(f"- 扫描记录数: {scan.get('total', 0)}")
                            lines.append(f"- 命中记录数: {scan.get('hits', 0)}")
                            lines.append(f"- 命中率: {scan.get('hit_rate', 0):.1%}")

                            # 列出命中记录的摘要
                            hit_rows = scan.get("rows", [])[:5]
                            if hit_rows:
                                lines.append("")
                                lines.append("**命中记录示例**:")
                                for hr in hit_rows:
                                    fields = hr.get("fields", {})
                                    row_id = hr.get("row_id", "?")
                                    # 提取关键字段
                                    key_info = []
                                    for k in ["doc_name", "party_a", "party_b", "amount", "sign_date",
                                              "account_name", "voucher_no", "item_name", "cert_type"]:
                                        if k in fields:
                                            key_info.append(f"{k}={fields[k]}")
                                    lines.append(f"  - 记录#{row_id}: {', '.join(key_info[:4])}")
                            lines.append("")
                        else:
                            lines.append(f"**扫描失败**: {scan.get('error', '未知错误')}")
                            lines.append("")
                    else:
                        lines.append("**注意**: 未找到匹配的数据表，请确认项目已上传资料。")
                        lines.append("")
                except Exception as e:
                    lines.append(f"**扫描异常**: {e}")
                    lines.append("")
            else:
                lines.append("**注意**: 缺少表达式或项目ID，无法执行自动扫描。")
                lines.append("")

        if scan_count == 0:
            lines.append("（未提供违规模型ID或未找到匹配的违规定义）")
            lines.append("")

        # 整体分析指令
        lines.append("## 分析要求")
        lines.append("1. 对上述每个违规模型的扫描结果进行独立分析")
        lines.append("2. 命中率超过 30% 的模型标注为高风险")
        lines.append("3. 涉及金额大或涉及刑事责任的事项优先标注")
        lines.append("4. 对零命中的模型，说明可能的合理原因")
        lines.append("5. 给出整体评估和后续建议")

        return "\n".join(lines)

    def _detect_target_table(self, expression: str, project_id: str) -> str | None:
        """根据表达式字段自动探测目标数据表

        策略: 检查表达式中引用的字段名存在于哪张数据工坊表。
        """
        # 表 → 特征字段映射
        TABLE_SIGNATURES = {
            "data_contracts": ["contract_no", "party_a", "party_b", "procurement_method", "sign_date"],
            "data_finance": ["account_no", "debit_amount", "credit_amount", "voucher_no", "bank_name"],
            "data_legal_docs": ["case_no", "issuing_body", "legal_basis", "verdict"],
            "data_registers": ["register_type", "item_name", "quantity", "responsible_person"],
            "data_credentials": ["cert_type", "cert_no", "holder", "expire_date"],
        }

        # 提取表达式中的字段名（去掉操作符和值后的裸字段名）
        import re
        field_pattern = re.compile(r'([a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)')
        fields_in_expr = set()
        for match in field_pattern.finditer(expression):
            field = match.group(1)
            # 过滤掉 SQL 关键字和操作符
            if field.upper() not in ("AND", "OR", "BETWEEN", "NULL", "TRUE", "FALSE", "LIKE", "IN"):
                fields_in_expr.add(field.lower())

        if not fields_in_expr:
            # 默认查合同表（最常见）
            return "data_contracts"

        # 按签名匹配度选择最佳表
        best_table = None
        best_score = 0
        for table, signatures in TABLE_SIGNATURES.items():
            score = sum(1 for s in signatures if s.lower() in fields_in_expr)
            if score > best_score:
                best_score = score
                best_table = table

        # 至少匹配 1 个特征字段才返回
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

        return "data_contracts"  # 最终默认
