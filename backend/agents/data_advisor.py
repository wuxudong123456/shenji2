"""资料顾问专家 Agent — 方案B 子类

核心原则（违规模型关联优先，资料推荐有据可依）:
  步骤1 retrieve: 接收上游匹配的违规模型
  步骤2 invoke:   通过 MCP 查询违规详情（get_violation_detail），获取其核查所需资料
  步骤3 synthesize: LLM 基于真实违规模型推荐资料清单 + 适用模板

特点:
  - 推荐的资料类型与违规模型的核查需求关联，而非凭空想象
  - 登记引用的违规模型来源，保持溯源链完整
"""
from agents.base import BaseAgent, AgentDefinition


# 审计领域/违规模型 → 资料类型 + 模板 知识库（确定性映射，可扩展）
_MATERIAL_KB = {
    "采购": {
        "materials": ["采购合同及补充协议", "中标通知书及评标报告", "招标公告发布记录",
                      "采购方式审批文件", "供应商营业执照"],
        "templates": ["audit/合同协议类/采购合同", "audit/合同协议类/招投标文件"],
    },
    "补贴": {
        "materials": ["补贴申报表", "土地确权登记表", "银行付款凭证", "现场核查记录"],
        "templates": ["audit/登记台账类/补贴台账", "audit/财务凭证类/付款凭证"],
    },
    "资金": {
        "materials": ["专项资金拨付文件", "银行付款凭证", "资金使用明细账", "预算批复文件"],
        "templates": ["audit/财务凭证类/付款凭证", "audit/登记台账类/资金台账"],
    },
    "预算": {
        "materials": ["部门预算批复文件", "预算调整审批记录", "决算报表", "财务凭证"],
        "templates": ["audit/财务账簿类/明细账", "audit/财务凭证类/会计凭证"],
    },
    "工程": {
        "materials": ["施工合同", "竣工验收报告", "工程结算书", "监理报告"],
        "templates": ["audit/合同协议类/施工合同", "audit/法律文书类/验收证明"],
    },
    "车辆": {
        "materials": ["车辆购置合同及发票", "公务用车编制批复文件", "车辆登记信息"],
        "templates": ["audit/登记台账类/车辆台账"],
    },
}


class DataAdvisorAgent(BaseAgent):
    """资料顾问专家 — 知识优先 + 显式工具调用"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        domain = input_data.get("domain", "")
        item = input_data.get("item", "")
        matches = input_data.get("matches", [])

        # ── 步骤1+2: 查询违规模型详情 + 匹配资料知识库 ──
        violation_titles = []
        for m in (matches or [])[:10]:
            title = m.get("violation_title") or m.get("name") or str(m)
            violation_titles.append(title)
            # 若有 id，查详情登记溯源
            vid = m.get("id") or m.get("violation_id")
            if vid and (isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit())):
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

        # 基于审计事项匹配资料知识库（确定性映射）
        kb_materials = []
        kb_templates = []
        combined = f"{domain} {item} {' '.join(violation_titles)}"
        for keyword, info in _MATERIAL_KB.items():
            if keyword in combined:
                kb_materials.extend(info["materials"])
                kb_templates.extend(info["templates"])

        # ── 步骤3: 把知识库匹配结果交给 LLM 整理 ──
        lines = [
            "## 审计资料推荐任务",
            "",
            "请基于【系统匹配的资料知识库】和【已选违规模型】，推荐本次审计需要收集的资料清单。",
            "资料类型应优先采用系统知识库匹配结果，可在此基础上补充。",
            "严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
        ]

        lines.append("## 审计上下文")
        lines.append(f"- 审计领域: {domain or '未指定'}")
        lines.append(f"- 审计事项: {item or '未指定'}")
        if violation_titles:
            lines.append("## 已匹配违规模型")
            for t in violation_titles:
                lines.append(f"- {t}")
        lines.append("")

        if kb_materials:
            lines.append("## 系统知识库匹配的资料类型（优先采用）")
            for m in dict.fromkeys(kb_materials):  # 去重保序
                lines.append(f"- {m}")
            lines.append("")

        if kb_templates:
            lines.append("## 系统知识库匹配的提取模板")
            for t in dict.fromkeys(kb_templates):
                lines.append(f"- {t}")
            lines.append("")

        lines.append("## 输出要求")
        lines.append("1. materials 中标注 necessity（essential/recommended/optional）")
        lines.append("2. suggested_templates 优先引用上方系统匹配的模板")
        lines.append("3. collection_plan 给出 1-2 句分步收集计划")

        return "\n".join(lines)
