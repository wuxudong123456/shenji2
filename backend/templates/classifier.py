"""Auto-classify document content to matching SKU extraction templates.

Uses a lightweight LLM call to match document content against the
catalog of available templates (descriptions, domains, tags).
"""

from __future__ import annotations

import json

from loguru import logger

from shared.core.config import settings
from shared.services.ai.openai_compatible_client_sync import get_openai_client
from shared.services.sku.ontology.profile_loader import list_profiles

# Max input chars for classification (use the beginning of the document)
CLASSIFY_MAX_CHARS = 4000

# Max tokens for classification response
CLASSIFY_MAX_TOKENS = 512


def _call_llm_json(messages: list[dict]) -> dict:
    """Lightweight LLM JSON call for classification.

    Args:
        messages: Chat message list.

    Returns:
        Parsed JSON dict.
    """
    client = get_openai_client()
    content, _usage = client.chat_completion_with_usage(
        messages=messages,
        model=settings.SKU_EXTRACTION_MODEL,
        temperature=0,
        max_tokens=CLASSIFY_MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    content = content.strip()
    if not content:
        logger.warning("Classifier LLM returned empty response, using defaults")
        return {"profiles": ["general/base_model"], "reasoning": "empty response"}

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Salvage: find outermost {}
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning(f"Classifier LLM returned invalid JSON: {content[:200]}")
        return {"profiles": ["general/base_model"], "reasoning": "parse error"}


def classify_document(
    content_sample: str,
    profiles: list[dict] | None = None,
    *,
    max_profiles: int = 3,
    filename_hint: str = "",
) -> list[str]:
    """Classify document content to matching SKU templates.

    When the template catalog is large (> TWO_STAGE_THRESHOLD), a two-stage
    approach is used: first pick the domain(s), then pick templates within
    those domains.  This keeps the LLM prompt within context limits.

    Args:
        content_sample: First N chars of document content for matching.
        profiles: Available profile catalog. If None, auto-loads from bundled dirs.
        max_profiles: Maximum number of matching profiles to return.
        filename_hint: Optional original file name for better classification
                       (often contains strong type signals like "会议纪要").

    Returns:
        List of profile names like ['审计/合同协议类/买卖合同', 'general/base_model'].
        Always includes at least one profile.
    """
    TWO_STAGE_THRESHOLD = getattr(settings, "SKU_CLASSIFY_TWO_STAGE_THRESHOLD", 50)

    if profiles is None:
        profiles = list_profiles()

    if not profiles:
        logger.warning("No SKU profiles available for classification")
        return ["general/base_model"]

    if len(profiles) <= max_profiles:
        return [p["name"] for p in profiles]

    # ── Two-stage classification for large catalogs ──
    if len(profiles) > TWO_STAGE_THRESHOLD:
        return _classify_two_stage(content_sample, profiles, max_profiles, filename_hint)

    return _classify_single_stage(content_sample, profiles, max_profiles, filename_hint)


def _classify_single_stage(
    content_sample: str,
    profiles: list[dict],
    max_profiles: int,
    filename_hint: str = "",
) -> list[str]:
    """Single-shot classification (small catalog — safe prompt size)."""
    try:
        messages = _build_catalog_prompt(content_sample, profiles, detail=False, filename_hint=filename_hint)
        result = _call_llm_json(messages)
        matched = result.get("profiles", [])
        reasoning = result.get("reasoning", "")
        available = {p["name"] for p in profiles}
        valid = [p for p in matched if p in available]
        if valid:
            logger.info(f"Auto-classified: {valid} (reasoning: {reasoning})")
            return valid[:max_profiles]
        logger.warning(f"Invalid profiles returned: {matched}")
    except Exception as e:
        logger.warning(f"Classification failed: {e}")
    return _fallback(profiles)


def _build_domain_summary(profiles: list[dict]) -> list[dict]:
    """Build a compact domain summary with diverse template type names.

    For each domain we collect all distinct type names and select the most
    classification-useful ones: we always include types that contain common
    document-category keywords (会议, 合同, 报告, etc.) plus a spread across
    the alphabet to cover the rest.
    """
    # Types whose names signal clear document categories — always include these.
    _KEY_TYPE_TOKENS = frozenset({
        "会议", "合同", "协议", "报告", "发票", "凭证", "账簿",
        "报表", "预算", "决算", "审计", "评估", "通知", "批复",
        "申请", "登记", "证明", "证书", "执照", "名单", "清单",
        "台账", "方案", "计划", "总结", "纪要", "记录", "笔录",
    })

    domain_info: dict[str, dict] = {}  # domain -> {types, count, sample_desc}
    for p in profiles:
        domain = p.get("domain", "")
        if not domain:
            continue
        name = p.get("name", "")
        type_name = name.rsplit("/", 1)[-1] if "/" in name else name
        if domain not in domain_info:
            domain_info[domain] = {
                "types": [type_name],
                "count": 1,
                "sample_desc": p.get("description", "")[:80],
            }
        else:
            info = domain_info[domain]
            info["count"] += 1
            if type_name not in info["types"]:
                info["types"].append(type_name)

    result: list[dict] = []
    for domain, info in sorted(domain_info.items()):
        types = info["types"]
        n = len(types)

        # Prioritise key types (always shown) + spread for the rest
        key = [t for t in types if any(kw in t for kw in _KEY_TYPE_TOKENS)]
        other = [t for t in types if t not in key]

        if len(key) + len(other) <= 18:
            display = key + other
        else:
            # Show all key types, then evenly sample the remaining
            budget = 18 - len(key)
            if budget > 0 and other:
                step = max(1, len(other) // budget)
                sample = [other[i] for i in range(0, len(other), step)][:budget]
                display = key + sample
            else:
                display = key[:18]

        types_str = "、".join(display[:20])
        result.append({
            "domain": domain,
            "desc": f"含{types_str}等{info['count']}个模板。{info['sample_desc']}",
        })
    return result


def _classify_two_stage(
    content_sample: str,
    profiles: list[dict],
    max_profiles: int,
    filename_hint: str = "",
) -> list[str]:
    """Stage 1: pick domain(s). Stage 2: pick templates within those domains."""
    # ── Stage 1: domain-level classification ──
    domain_list = _build_domain_summary(profiles)

    if len(domain_list) <= 1:
        return _classify_single_stage(content_sample, profiles, max_profiles, filename_hint)

    try:
        messages = _build_domain_prompt(content_sample, domain_list, filename_hint)
        result = _call_llm_json(messages)
        picked_domains = result.get("domains", [domain_list[0]["domain"]])
        logger.info(f"Stage-1: picked domains {picked_domains}")
    except Exception as e:
        logger.warning(f"Stage-1 classification failed: {e}")
        return _fallback(profiles)

    # ── Stage 2: classify within selected domains ──
    pool = [p for p in profiles if p.get("domain", "") in picked_domains]
    if not pool:
        pool = profiles  # shouldn't happen

    return _classify_single_stage(content_sample, pool, max_profiles, filename_hint)


def _build_domain_prompt(content_sample: str, domain_list: list[dict], filename_hint: str = "") -> list[dict]:
    """Build prompt for domain-level classification."""
    catalog_lines = []
    for d in domain_list:
        catalog_lines.append(
            f"  - {d['domain']}：{d['desc']}"
        )
    catalog = "\n".join(catalog_lines)

    file_info = f"文件名：{filename_hint}\n\n" if filename_hint else ""
    user = (
        f"## 可用领域\n\n{catalog}\n\n"
        f"{file_info}"
        f"## 文档内容（开头）\n\n{content_sample[:CLASSIFY_MAX_CHARS]}\n\n"
        f'请根据文档的实际内容（标题、关键词、字段）判断所属领域，'
        f'不要仅凭文档中偶然出现的某个词就匹配。'
        f'以json格式返回最匹配的1-3个领域，'
        f'格式：{{"domains": ["领域1", ...], "reasoning": "理由"}}'
    )
    return [
        {"role": "system", "content": "你是一个文档分类专家。根据文档内容选出最匹配的领域。优先根据文档类型（如会议纪要、合同、发票等）判断，而不是根据文档中偶然提及的词语。请以json格式输出。"},
        {"role": "user", "content": user},
    ]


def _build_catalog_prompt(
    content_sample: str,
    profiles: list[dict],
    *,
    detail: bool = True,
    filename_hint: str = "",
) -> list[dict]:
    """Build template-level catalog prompt."""
    catalog_lines = []
    per_line = (120 if detail else 60)
    for p in profiles:
        name = p["name"]
        domain = p.get("domain", "")
        desc = p.get("description", "")[:per_line]
        tags = ", ".join(p.get("tags", [])[:5])
        catalog_lines.append(
            f"  - {name} (领域: {domain}, 标签: {tags})\n"
            f"    描述: {desc}"
        )
    catalog = "\n".join(catalog_lines)

    system = (
        "你是一个文档分类专家。根据文档内容，从模板目录中选出最匹配的1-3个信息提取模板。"
        "选择标准：优先根据文档的实际类型（会议纪要、合同、发票、报告等）匹配模板，"
        "而不是仅靠文档中偶然出现的某个词。"
        "模板名是抽取目标分类，不是搜索关键词——例如名为「报告」的模板适用于审计报告类正式文件，"
        "而不是任何提到了「报告」二字的文档。以json格式输出。"
    )
    file_info = f"文件名：{filename_hint}\n\n" if filename_hint else ""
    user = (
        f"## 可用模板目录\n\n{catalog}\n\n"
        f"{file_info}"
        f"## 文档内容（开头片段）\n\n{content_sample[:CLASSIFY_MAX_CHARS]}\n\n"
        f'请返回匹配的模板名列表，格式：{{"profiles": ["domain/name", ...], '
        f'"reasoning": "简短说明理由"}}\n'
        f"最多{3}个，按相关性排序。如果文档内容不足以判断，返回通用模板。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _fallback(profiles: list[dict]) -> list[str]:
    general = [p["name"] for p in profiles if p["name"].startswith("general/")]
    if general:
        return general[:1]
    return [profiles[0]["name"]] if profiles else ["general/base_model"]
