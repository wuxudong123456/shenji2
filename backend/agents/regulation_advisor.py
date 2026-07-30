"""法规顾问专家 Agent — 方案B 子类

核心原则（知识优先，杜绝法规幻觉）:
  步骤1 retrieve: 通过 MCP 工具检索真实法规库（search_laws）
  步骤2 invoke:   展开法规关系链（get_regulation_graph）+ 依据审计事项查定性/处罚依据
  步骤3 synthesize: LLM 仅基于已检索到的真实法规做层级建议与格式化

关键差异（对比纯 LLM）:
  - 法规名称、文号、条款全部来自 sys_core_law_allaudit，绝不由 LLM 编造
  - 每条推荐法规都登记 knowledge_source，可在疑点报告中溯源
  - 依据被审计对象行政层级（target_level）筛选法规地域类型
"""
from agents.base import BaseAgent, AgentDefinition


# 行政层级 → 法规地域类型映射（region_type: 0=国家, 1=地方）
_LEVEL_TO_REGION = {
    "国家级": None,   # 国家级审计对象：国家 + 地方都查
    "省级": 1,
    "市级": 1,
    "县级": 1,
    "乡级": 1,
}


class RegulationAdvisorAgent(BaseAgent):
    """法规顾问专家 — 知识优先 + 显式工具调用"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        domain = input_data.get("domain", "")
        item = input_data.get("item", "")
        target_level = input_data.get("target_level", "")
        target_unit = input_data.get("target_unit", "")
        selected_violations = input_data.get("selected_violations") or input_data.get("matches") or []

        # ── 步骤1+2: 通过 MCP 工具检索真实法规（不是让 LLM 凭记忆推荐）──

        # 1a. 从审计领域/事项提取检索关键词
        search_terms = self._extract_search_terms(domain, item, target_unit)
        retrieved_laws = []
        for term in search_terms[:3]:  # 最多 3 个关键词，控制检索量
            res = self.invoke_tool("knowledge-mcp.search_laws",
                                   {"query": term, "limit": 8})
            if res.get("success"):
                for law in (res.get("result") or {}).get("laws", []):
                    retrieved_laws.append(law)
                    # 登记知识来源（溯源）
                    self.add_knowledge_source(
                        source="audit_law.sys_core_law_allaudit",
                        item_type="law",
                        item_id=law.get("id"),
                        snippet=law.get("title", ""),
                    )

        # 1b. 去重（按 law id）
        seen_ids = set()
        unique_laws = []
        for law in retrieved_laws:
            lid = law.get("id")
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                unique_laws.append(law)

        # 2. 对排名靠前的主法展开法规关系链
        regulation_graphs = []
        for law in unique_laws[:3]:
            law_id = law.get("id")
            graph_res = self.invoke_tool("knowledge-mcp.get_regulation_graph",
                                         {"law_id": str(law_id)})
            if graph_res.get("success"):
                graph = graph_res.get("result") or {}
                graph["source_law_title"] = law.get("title", "")
                regulation_graphs.append(graph)

        # ── 步骤3: 把真实检索结果交给 LLM 做层级建议与格式化 ──
        lines = [
            "## 审计法规推荐任务",
            "",
            "请基于【系统已检索到的真实法规库结果】，为本次审计推荐适用法规并给出层级建议。",
            "你只能引用下列真实存在的法规，不得编造任何不在此列表中的法规名称、文号或条款。",
            "严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain or '未指定'}")
        lines.append(f"- 审计事项: {item or '未指定'}")
        lines.append(f"- 被审计对象: {target_unit or '未指定'} ({target_level or '未指定层级'})")
        lines.append("")

        # 已选违规模型（若有，用于关联法规）
        if selected_violations:
            lines.append("## 已选违规模型（用于法规关联参考）")
            for v in selected_violations[:10]:
                title = v.get("violation_title") or v.get("name") or str(v)
                lines.append(f"- {title}")
            lines.append("")

        # 真实法规候选清单（LLM 只能从这里选）
        if unique_laws:
            lines.append("## 系统检索到的真实法规候选（仅可引用下列法规）")
            for law in unique_laws[:20]:
                lid = law.get("id", "")
                title = law.get("title", "")
                potency = law.get("potency_level", "")
                timeliness = law.get("timeliness", "")
                region = "国家" if law.get("region_type") == 0 else "地方"
                lines.append(
                    f"- id={lid} | 《{title}》 | {potency} | {region} | {timeliness}"
                )
            lines.append("")
        else:
            lines.append("## 系统检索到的真实法规候选")
            lines.append("（系统未检索到匹配法规。请在输出中如实说明，不得编造法规。）")
            lines.append("")

        # 法规关系链（层级建议的依据）
        if regulation_graphs:
            lines.append("## 法规关系链（用于层级适用建议）")
            for g in regulation_graphs:
                center = g.get("center", {})
                lines.append(f"### 主法: 《{center.get('title', g.get('source_law_title', ''))}》")
                inferior = g.get("inferior", [])
                if inferior:
                    lines.append("下位法/实施细则:")
                    for inf in inferior[:8]:
                        lines.append(f"  - 《{inf.get('title', inf.get('name', ''))}》 "
                                     f"({inf.get('potency_level', '')})")
                superior = g.get("superior_chain", [])
                if superior:
                    lines.append("上位法:")
                    for sup in superior[:5]:
                        lines.append(f"  - 《{sup.get('title', sup.get('name', ''))}》")
                lines.append("")

        # 层级提示
        lines.append("## 层级适用原则提醒")
        if target_level in ("省级", "市级", "县级", "乡级"):
            lines.append(f"被审计对象为【{target_level}】单位，应优先适用同级及上级地方性法规，"
                         "国家法律作为上位法依据。请重点标注地方性法规。")
        else:
            lines.append("适用国家法律法规及部门规章。")

        lines.append("")
        lines.append("## 输出要求")
        lines.append("1. primary_laws 中的 law_id 必须来自上方真实法规候选列表")
        lines.append("2. 不得编造候选列表中不存在的法规")
        lines.append("3. 给出针对当前行政层级的层级适用建议")

        return "\n".join(lines)

    def _extract_search_terms(self, domain: str, item: str, target_unit: str) -> list[str]:
        """从审计上下文提取法规检索关键词"""
        terms = []
        # 审计事项是最精准的检索词
        if item:
            terms.append(item.strip())
        # 领域
        if domain:
            terms.append(domain.strip())
        # 从事项中提取业务关键词（如"采购"→"招标"、"补贴"→"补贴"）
        keyword_map = {
            "采购": "招标投标法",
            "招标": "招标投标法",
            "补贴": "补贴",
            "资金": "财政资金",
            "预算": "预算法",
            "决算": "预算法",
            "税收": "税收征收管理法",
            "社保": "社会保险",
            "工程": "建设工程",
        }
        for kw, term in keyword_map.items():
            if kw in (item or "") or kw in (domain or ""):
                if term not in terms:
                    terms.append(term)
        return terms
