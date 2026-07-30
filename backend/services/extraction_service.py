"""SKU 字段提取服务 — 模板 + Markdown → LLM → 结构化字段

借鉴自 OntoSKU prompt_builder.py 和知识工坊 llm_pool.py
"""
import json
from typing import Optional
from services.template_service import get_template
from services.llm_client import call_llm_json


def _build_extraction_prompt(template: dict, markdown: str) -> tuple[str, str]:
    """构建提取用的 system_prompt 和 user_prompt

    Args:
        template: 模板 dict（含 guideline, fields 等）
        markdown: LiteParse 解析出的 Markdown 文本

    Returns:
        (system_prompt, user_prompt)
    """
    fields = template.get("output", {}).get("fields", [])
    guideline = template.get("guideline", "")

    # System prompt — 从 prompts/extraction/extract_fields.txt 加载模板
    # 模板含 {guideline} 和 {fields_json} 占位符
    field_lines = []
    for f in fields:
        req = "必填" if f.get("required", True) else "可选"
        ftype = f.get("type", "string")
        desc = f.get("description", f["name"])
        field_lines.append(f'    "{f["name"]}": "{desc} ({ftype}, {req})"')
    fields_json = "{\n" + ",\n".join(field_lines) + "\n}"

    try:
        from prompts import load_prompt
        tmpl = load_prompt("extraction/extract_fields")
        system_prompt = tmpl.format(guideline=guideline, fields_json=fields_json)
    except FileNotFoundError:
        # 回退: 原内联逻辑（与 prompts/extraction/extract_fields.txt 等价）
        system_parts = [
            "【严格要求——禁止虚构】",
            "你必须只提取原文中精确出现的文字，不得虚构、推测、归纳或概括任何内容。",
            "如果原文中没有某个字段的值，必须返回「未提供」，绝对不能编造。",
            "你提取的每一个值，必须能在原文中找到对应的文字片段。",
        ]
        if guideline:
            system_parts.append(f"\n提取规则：\n{guideline}")
        system_parts.append(f'\n输出格式（以json格式输出）：\n{fields_json}')
        system_prompt = "\n\n".join(system_parts)

    # User prompt
    user_prompt = f"请从以下文档中逐字提取信息，不得虚构：\n\n{markdown[:12000]}"

    return system_prompt, user_prompt


def extract_fields(
    template_name: str,
    markdown: str,
    model: Optional[str] = None,
) -> dict:
    """根据模板从 Markdown 中提取结构化字段

    Args:
        template_name: 模板名称，如 "audit/合同协议类/合同"
        markdown: 待提取的 Markdown 文本
        model: LLM 模型名（可选，默认用环境变量）

    Returns:
        {
            "success": True/False,
            "template": "...",
            "fields": [{"name": "...", "value": "...", "type": "..."}, ...],
            "raw_response": {...},
        }
    """
    tmpl = get_template(template_name)
    if not tmpl:
        return {"success": False, "error": f"模板不存在: {template_name}"}

    if not markdown or not markdown.strip():
        return {"success": False, "error": "Markdown 内容为空，请先上传文档"}

    system_prompt, user_prompt = _build_extraction_prompt(tmpl, markdown)

    result = call_llm_json(
        prompt=user_prompt,
        system_prompt=system_prompt,
        model=model,
        max_tokens=4096,
        temperature=0.1,
    )

    if "error" in result:
        return {"success": False, "error": result.get("error", "LLM 调用失败"), "raw": result}

    # Map LLM response to structured fields
    template_fields = tmpl.get("output", {}).get("fields", [])
    fields_out = []
    for f in template_fields:
        name = f["name"]
        value = result.get(name, "未提供")
        if value is None:
            value = "未提供"
        fields_out.append({
            "name": name,
            "value": str(value),
            "type": f.get("type", "string"),
        })

    return {
        "success": True,
        "template": template_name,
        "fields": fields_out,
        "raw_response": result,
    }


def classify_document(markdown: str) -> dict:
    """自动分类文档 → 返回匹配的模板建议（不提取字段）

    Returns:
        {"success": True, "classification": {...}, "matches": [{"name":..., "score":..., "description":...}, ...]}
    """
    from services.llm_client import call_llm
    import json as _json

    classify_prompt = ""
    try:
        from prompts import load_prompt
        classify_prompt = load_prompt("extraction/classify").format(markdown=markdown[:3000])
    except FileNotFoundError:
        classify_prompt = f"""请判断以下文档的类型。严格返回 JSON 格式（不要加任何额外文字）：

类别必须是以下之一：合同协议类、业务单据类、财务凭证类、财务票据类、财务账簿类、数据表格类、数据信息类、政策文件类、法律文书类、审查报告类、登记台账类、规章制度类、影像图件类、清单名册类、资料材料类、记录留痕类、资质证照类、历史档案类、其他杂项类

注意：
- 包含"合同"字样的文档，优先归类为"合同协议类"
- 采购合同、销售合同、服务合同等都属于"合同协议类"
- 单据类是指入库单、出库单、交接单、保险单等单张凭证

{{"domain": "审计领域", "category": "文档类别（必须从上述列表选择）", "doc_type": "具体文档类型（如 采购合同、发票、入库单 等，2-6个字）", "reasoning": "判断依据（一句话）"}}

文档内容：
{markdown[:3000]}
"""
    try:
        text = call_llm(prompt=classify_prompt, max_tokens=512, temperature=0)
        # Extract JSON from response (may have markdown wrapping)
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            classify_result = _json.loads(text[start:end + 1])
        else:
            classify_result = _json.loads(text)
    except Exception as e:
        classify_result = {"error": str(e)}

    if "error" in classify_result:
        return {"success": False, "error": "文档分类失败: " + classify_result.get("error", "")}

    # 用分类结果搜索匹配模板
    category = classify_result.get("category", "")
    doc_type = classify_result.get("doc_type", "")

    from services.template_service import search_templates, list_templates

    # 策略：多路召回 — 类别精确匹配 + 文档类型搜索 + 关键词拆分搜索
    candidates = []
    seen = set()

    def add(ts):
        for t in ts:
            if t["name"] not in seen:
                candidates.append(t)
                seen.add(t["name"])

    if category:
        add(list_templates(category=category)[:20])

    if doc_type:
        add(search_templates(doc_type, limit=10))

    # 关键词 → 类别直接映射（不依赖 LLM 分类准确性）
    KEYWORD_CATEGORY_MAP = {
        "合同": "合同协议类", "采购": "合同协议类",
        "发票": "财务票据类", "凭证": "财务凭证类", "账簿": "财务账簿类",
        "工资": "数据信息类", "报表": "数据表格类",
        "招标": "合同协议类", "投标": "合同协议类",
        "验收": "资料材料类", "入库": "业务单据类", "出库": "业务单据类",
        "登记": "登记台账类", "台账": "登记台账类",
        "审批": "审查报告类",
    }
    for kw, cat in KEYWORD_CATEGORY_MAP.items():
        if kw in markdown[:2000]:
            add(list_templates(category=cat)[:10])

    # 也搜索关键词
    for kw in list(KEYWORD_CATEGORY_MAP.keys())[:5]:
        if kw in markdown[:2000]:
            add(search_templates(kw, limit=10))

    # 兜底：用 domain 搜索
    if len(candidates) < 3:
        domain = classify_result.get("domain", "")
        add(search_templates(domain, limit=10))

    # 确保通用模板始终在前（如 合同协议类/合同、业务单据类/单据）
    CATEGORY_DEFAULT = {
        "合同协议类": "audit/合同协议类/合同",
        "业务单据类": "audit/业务单据类/单据",
        "财务凭证类": "audit/财务凭证类/凭证",
        "财务票据类": "audit/财务票据类/票据",
        "数据表格类": "audit/数据表格类/表格",
    }
    default_name = CATEGORY_DEFAULT.get(category)
    if default_name:
        from services.template_service import get_template, _get_desc
        default_tmpl = get_template(default_name)
        if default_tmpl:
            fields = default_tmpl.get("output", {}).get("fields", [])
            default_entry = {
                "name": default_name,
                "description": _get_desc(default_tmpl),
                "domain": default_tmpl.get("domain", ""),
                "field_count": len(fields),
                "fields": [f["name"] for f in fields[:10]],
            }
            # 插入到第一位
            candidates = [t for t in candidates if t["name"] != default_name]
            candidates.insert(0, default_entry)

    matches = candidates[:10]

    return {
        "success": True,
        "classification": classify_result,
        "matches": matches,
    }


def auto_classify_and_extract(markdown: str) -> dict:
    """自动分类文档 → 匹配最佳模板 → 提取字段

    先用 LLM 分类文档类型，再匹配模板进行提取。
    """
    # Step 1: 用轻量 prompt 分类（从 prompts/extraction/classify_light.txt 加载）
    classify_prompt = ""
    try:
        from prompts import load_prompt
        classify_prompt = load_prompt("extraction/classify_light").format(markdown=markdown[:3000])
    except FileNotFoundError:
        classify_prompt = f"""请判断以下文档的类型。返回 JSON 格式：
{{"domain": "审计领域（如 audit）", "category": "文档类别（如 合同协议类、业务单据类、财务凭证类）", "doc_type": "具体文档类型（如 采购合同、发票、入库单）", "reasoning": "判断依据"}}

文档内容：
{markdown[:3000]}
"""
    classify_result = call_llm_json(prompt=classify_prompt, max_tokens=512)

    if "error" in classify_result:
        return {"success": False, "error": "文档分类失败: " + classify_result.get("error", "")}

    # Step 2: 用分类结果搜索匹配模板
    category = classify_result.get("category", "")
    from services.template_service import search_templates, list_templates
    from services.template_service import _get_desc

    # 先按类别精确匹配
    candidates = list_templates(category=category)
    if not candidates:
        # 按文档类型关键词搜索
        doc_type = classify_result.get("doc_type", "")
        candidates = search_templates(doc_type, limit=10)
    if not candidates:
        # 最后的兜底
        candidates = search_templates(category, limit=10)

    if not candidates:
        return {"success": False, "error": "未找到匹配的模板"}

    # Step 3: 用最佳匹配模板提取
    best_template = candidates[0]["name"]
    return extract_fields(best_template, markdown)
