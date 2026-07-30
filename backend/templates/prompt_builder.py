"""Build DeepSeek-optimized extraction prompts from SkuTemplate configs.

DeepSeek requirements:
  1. json_object mode: prompt MUST contain the word "json" (case-insensitive)
  2. No json_schema support: field names must be injected into the prompt text
"""

from __future__ import annotations

import json

from shared.services.sku.ontology.profile_loader import SkuTemplate


def _build_output_schema_text(template: SkuTemplate) -> str:
    """Build a human-readable output schema description for the prompt.

    Since DeepSeek doesn't support json_schema (strict), we embed the expected
    field names and types directly in the prompt text.
    """
    if template.is_graph_type:
        parts = ["输出格式（以json格式输出，Output in json format）："]
        parts.append("{")
        parts.append('  "entities": [')
        field_examples = []
        for f in template.entity_fields:
            req = "(必填)" if f.get("required", True) else "(可选)"
            field_examples.append(f'    {{"{f["name"]}": "{f.get("description","")} {req}"}}')
        parts.extend(field_examples)
        parts.append("  ],")
        parts.append('  "relations": [')
        rel_examples = []
        for f in template.relation_fields:
            req = "(必填)" if f.get("required", True) else "(可选)"
            rel_examples.append(f'    {{"{f["name"]}": "{f.get("description","")} {req}"}}')
        parts.extend(rel_examples)
        parts.append("  ]")
        parts.append("}")
        return "\n".join(parts)
    else:
        parts = ["输出格式（以json格式输出，Output in json format）："]
        parts.append("{")
        for f in template.output_fields:
            req = "(必填)" if f.get("required", True) else "(可选)"
            ftype = f.get("type", "str")
            parts.append(f'  "{f["name"]}": "{f.get("description","")} ({ftype}, {req})",')
        parts.append("}")
        return "\n".join(parts)


def build_system_prompt(template: SkuTemplate) -> str:
    """Build the system prompt for extraction.

    Args:
        template: Loaded and localized extraction template

    Returns:
        Complete system prompt string with role, rules, and output schema
    """
    parts = []

    # 1. Role + anti-hallucination (hard requirement, not per-template)
    anti_fab = (
        "【严格要求——禁止虚构】\n"
        "你必须只提取原文中精确出现的文字，不得虚构、推测、归纳或概括任何内容。\n"
        "如果原文中没有某个字段的值，必须返回「未提供」，绝对不能编造。\n"
        "例如：原文是「增值税电子普通发票」，就不能写成「增值税专用发票」或「行程单」。\n"
        "又例如：原文只有「720.00」，就不能写成「720元」或「720.00元」。\n"
        "你提取的每一个值，必须能在原文中找到对应的文字片段。\n"
    )
    if template.role:
        parts.append(anti_fab)
        parts.append(template.role)

    # 2. Extraction rules
    if template.rules:
        parts.append(f"\n提取规则：\n{template.rules}")

    # 3. Output schema (with DeepSeek "json" keyword built-in)
    schema_text = _build_output_schema_text(template)
    parts.append(f"\n{schema_text}")

    # 4. DeepSeek: ensure "json" appears in prompt
    if "json" not in " ".join(parts).lower():
        parts.append("\n请以JSON格式输出。Output in json format.")

    return "\n\n".join(parts)


def build_extraction_messages(
    template: SkuTemplate,
    chunk_text: str,
    extra_context: dict | None = None,
) -> list[dict]:
    """Build the full message list for an extraction API call.

    Args:
        template: Extraction template
        chunk_text: The text chunk to extract from
        extra_context: Optional extra context (e.g. document metadata)

    Returns:
        List of message dicts ready for OpenAI-compatible chat API
    """
    system_prompt = build_system_prompt(template)

    user_parts = ["请从以下文本中逐字提取信息，不得虚构：\n\n"]
    if extra_context:
        user_parts.append(f"上下文：{json.dumps(extra_context, ensure_ascii=False)}\n\n")
    user_parts.append(chunk_text)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "".join(user_parts)},
    ]
