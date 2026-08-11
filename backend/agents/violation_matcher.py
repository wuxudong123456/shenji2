"""违规匹配专家 Agent — 方案B 子类

核心原则（违规库优先，杜绝编造违规模型）:
  步骤1 retrieve: 通过 MCP 工具检索真实违规行为库（search_violations）
  步骤2 invoke:   多关键词召回 + 去重，返回真实存在的违规模型
  步骤3 synthesize: LLM 基于真实违规模型清单做匹配度评分与格式化

关键差异（对比纯 LLM）:
  - 违规模型名称、表达式全部来自 tt.audit_violations，不由 LLM 编造
  - 每个匹配项登记 knowledge_source，可在下游溯源
"""
from agents.base import BaseAgent, AgentDefinition, fmt_list


class ViolationMatcherAgent(BaseAgent):
    """违规匹配专家 — 知识优先 + 显式工具调用"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        domain = input_data.get("domain", "")
        item = input_data.get("item", "")
        target_level = input_data.get("target_level", "")
        target_unit = input_data.get("target_unit", "")
        concerns = input_data.get("concerns", [])
        objective = input_data.get("objective", "")  # P9-立项匹配: 审计目标
        scope = input_data.get("scope", "")          # P9-立项匹配: 审计范围

        # 事项级指导（ContextBuilder 装配的 focus_item）——附录A §2 事项级上下文
        focus = input_data.get("focus_item") or {}
        item_violations = focus.get("common_violations") or []
        item_legal_bases = focus.get("legal_bases") or []
        item_problems = focus.get("common_problems") or []

        # ── 步骤1+2: 通过 MCP 工具检索真实违规库 ──
        # common_violations 是最精准的检索种子（事项自带"我常见哪些违规"，直接拿违规名搜违规库）
        search_terms = self._build_search_terms(domain, item, concerns, objective, scope,
                                                item_violations=item_violations)
        retrieved = []
        seen_ids = set()
        for term in search_terms[:4]:
            res = self.invoke_tool("knowledge-mcp.search_violations",
                                   {"query": term, "limit": 15})
            if not res.get("success"):
                continue
            for v in (res.get("result") or {}).get("violations", []):
                vid = v.get("id")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    retrieved.append(v)
                    self.add_knowledge_source(
                        source="tt.audit_violations",
                        item_type="violation",
                        item_id=vid,
                        snippet=v.get("violation_title", ""),
                    )

        # ── 步骤3: 把真实违规模型交给 LLM 做匹配评分 ──
        lines = [
            "## 违规模型匹配任务",
            "",
            "请基于【系统已检索到的真实违规模型清单】，为本次审计匹配最相关的违规行为并评分。",
            "你只能从下列真实存在的违规模型中选择和评分，不得编造清单外的违规模型。",
            "严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain or '未指定'}")
        lines.append(f"- 审计事项: {item or '未指定'}")
        lines.append(f"- 被审计对象: {target_unit or '未指定'} ({target_level or '未指定层级'})")
        if objective:
            lines.append(f"- 审计目标: {objective}")
        if scope:
            lines.append(f"- 审计范围: {scope}")
        if concerns:
            lines.append(f"- 关注点: {'；'.join(concerns) if isinstance(concerns, list) else concerns}")
        # 事项自带的常见违规/法规/问题——匹配评分时优先对照（事项级指导，非 LLM 臆造）
        if item_problems:
            lines.append(f"- 事项常见问题: {fmt_list(item_problems)}")
        if item_violations:
            lines.append(f"- 事项常见违规（优先匹配）: {fmt_list(item_violations)}")
        if item_legal_bases:
            lines.append(f"- 事项法规依据: {fmt_list(item_legal_bases)}")
        lines.append("")

        if retrieved:
            lines.append("## 系统检索到的真实违规模型清单（仅可从此选择）")
            for v in retrieved[:25]:
                vid = v.get("id", "")
                title = v.get("violation_title", "")
                severity = v.get("severity", "")
                expr = (v.get("expression_text") or "")[:80]
                lines.append(f"- id={vid} | {title} | 严重度:{severity}")
                if expr:
                    lines.append(f"    表达式: {expr}")
            lines.append("")
        else:
            lines.append("## 系统检索到的真实违规模型清单")
            lines.append("（未检索到匹配违规模型。请如实说明，不得编造。）")
            lines.append("")

        lines.append("## 输出要求")
        lines.append("1. matches 中的违规模型必须来自上方清单（可引用 violation_title）")
        lines.append("2. 不得编造清单外的违规行为")
        lines.append("3. relevance_score 为 0-1 之间的匹配度")
        lines.append("4. 考虑被审计对象行政层级（地方审计优先匹配地方性违规模式）")

        return "\n".join(lines)

    # 审计业务关键词 → 违规库检索词映射（命中即用聚焦短词，避免长句检索失效）
    _KEYWORD_MAP = {
        "采购": "采购", "招标": "招标", "投标": "招标", "竞标": "招标",
        "化整为零": "化整为零", "拆分": "拆分", "规避招标": "规避招标",
        "补贴": "补贴", "惠农": "补贴", "农业": "补贴",
        "资金": "资金", "专项资金": "专项资金", "财政资金": "财政资金",
        "挪用": "挪用", "截留": "截留", "套取": "套取",
        "预算": "预算", "决算": "决算", "预算执行": "预算",
        "税收": "税收", "征税": "税收", "减免税": "税收",
        "社保": "社会保险", "医保": "医疗保险", "养老金": "养老保险",
        "工程": "工程", "建设": "建设工程", "施工": "工程",
        "车辆": "公务用车", "公车": "公务用车",
        "津补贴": "津贴", "奖金": "奖金", "绩效": "绩效",
        "合同": "合同", "协议": "合同",
        "发票": "发票", "票据": "发票", "报销": "报销",
        "固定资产": "固定资产", "资产": "资产处置", "国有资产": "国有资产",
        "土地": "土地", "矿产": "矿产", "环保": "环保",
    }

    def _build_search_terms(self, domain: str, item: str, concerns,
                            objective: str = "", scope: str = "",
                            item_violations=None) -> list:
        """构建违规库检索关键词 — 从长句抽取聚焦短词，避免整句检索失效"""
        terms = []
        # 0. 事项自带的常见违规名（最精准种子——直接拿违规名搜违规库，命中率最高）
        if item_violations:
            from agents.base import elt_text
            for v in item_violations[:6]:
                s = elt_text(v)
                if s and s not in terms:
                    terms.append(s)
        # 合并所有文本来源用于关键词扫描（P9-立项匹配: 纳入 objective/scope，扩大相关候选召回）
        text_parts = []
        if item:
            text_parts.append(item)
        if objective:
            text_parts.append(objective)
        if scope:
            text_parts.append(scope)
        if isinstance(concerns, list):
            text_parts.extend(str(c) for c in concerns if c)
        elif concerns:
            text_parts.append(str(concerns))
        combined = " ".join(text_parts)

        # 1. 关键词词典命中 → 用聚焦短词（最高优先级）
        for kw, search_term in self._KEYWORD_MAP.items():
            if kw in combined and search_term not in terms:
                terms.append(search_term)

        # 2. concerns 本身若较短（≤8字），直接作为检索词
        if isinstance(concerns, list):
            for c in concerns:
                c = str(c).strip()
                if c and len(c) <= 8 and c not in terms:
                    terms.append(c)

        # 3. 领域核心词
        if domain:
            core = domain.replace("审计", "").replace("业务", "").strip()
            if core and core not in terms:
                terms.append(core)

        # 4. 兜底：若以上都没命中，取 item 前 6 字作为短检索词（而非整句）
        if not terms and item:
            short = item.strip()[:6]
            if short:
                terms.append(short)

        return terms[:6]  # 最多 6 个检索词，控制调用量
        return terms
