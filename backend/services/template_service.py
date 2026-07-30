"""Template service — 加载 SKU 提取模板（纯 YAML，不依赖 shared.*）"""
import os
import yaml
from pathlib import Path
from typing import Optional

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "profiles"

_cache: Optional[dict] = None  # {name: template_dict}
_category_tree: Optional[list] = None  # [{domain, categories: [{name, templates: [{name, description, field_count}]}]}]


def _load_all() -> dict:
    """懒加载全部模板到内存"""
    global _cache
    if _cache is not None:
        return _cache

    _cache = {}
    if not TEMPLATES_DIR.exists():
        return _cache

    for yaml_file in TEMPLATES_DIR.rglob("*.yaml"):
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and isinstance(data, dict) and "name" in data:
                _cache[data["name"]] = data
        except Exception:
            pass

    return _cache


def _get_desc(tmpl: dict) -> str:
    """兼容 string / {zh, en} 两种 description 格式"""
    desc = tmpl.get("description", "")
    if isinstance(desc, dict):
        return desc.get("zh", desc.get("en", str(desc)))
    return str(desc) if desc else ""


def _build_category_tree() -> list:
    """构建 domain → category → template 三层树"""
    global _category_tree
    if _category_tree is not None:
        return _category_tree

    all_templates = _load_all()
    domains: dict = {}

    for name, tmpl in all_templates.items():
        domain = tmpl.get("domain", "general")
        tc = tmpl.get("template_class", {})
        category = tc.get("category", "其他")
        desc = _get_desc(tmpl)
        fields = tmpl.get("output", {}).get("fields", [])
        tags = tmpl.get("tags", [])

        if domain not in domains:
            domains[domain] = {}
        if category not in domains[domain]:
            domains[domain][category] = []

        domains[domain][category].append({
            "name": name,
            "description": desc[:80] if desc else "",
            "field_count": len(fields),
            "tags": tags[:5] if tags else [],
        })

    # Sort domains, categories, templates
    _category_tree = []
    for domain in sorted(domains.keys()):
        cats = []
        for cat in sorted(domains[domain].keys()):
            tmpls = sorted(domains[domain][cat], key=lambda t: t["name"])
            cats.append({"name": cat, "templates": tmpls})
        _category_tree.append({"domain": domain, "categories": cats})

    return _category_tree


def list_categories() -> list:
    """获取模板分类树"""
    return _build_category_tree()


def list_templates(domain: str = None, category: str = None) -> list:
    """列出模板（可按领域/类别筛选）"""
    all_templates = _load_all()
    result = []

    for name, tmpl in all_templates.items():
        if domain and tmpl.get("domain") != domain:
            continue
        if category:
            tc = tmpl.get("template_class", {})
            if tc.get("category") != category:
                continue
        fields = tmpl.get("output", {}).get("fields", [])
        result.append({
            "name": name,
            "description": _get_desc(tmpl),
            "domain": tmpl.get("domain", ""),
            "field_count": len(fields),
            "fields": [f["name"] for f in fields[:10]],
        })

    return sorted(result, key=lambda t: t["name"])


def get_template(name: str) -> Optional[dict]:
    """获取单个模板详情"""
    all_templates = _load_all()
    return all_templates.get(name)


def search_templates(query: str, limit: int = 20) -> list:
    """搜索模板 — 匹配 name, description, tags"""
    all_templates = _load_all()
    q = query.lower()
    results = []

    for name, tmpl in all_templates.items():
        score = 0
        if q in name.lower():
            score += 10
        if q in _get_desc(tmpl).lower():
            score += 5
        tags = tmpl.get("tags", [])
        for tag in tags:
            if q in tag.lower():
                score += 3
        if score > 0:
            fields = tmpl.get("output", {}).get("fields", [])
            results.append({
                "name": name,
                "description": _get_desc(tmpl),
                "domain": tmpl.get("domain", ""),
                "field_count": len(fields),
                "score": score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def get_stats() -> dict:
    """模板统计"""
    all_templates = _load_all()
    domains = {}
    total_fields = 0
    total_violations = 0

    for tmpl in all_templates.values():
        d = tmpl.get("domain", "unknown")
        domains[d] = domains.get(d, 0) + 1
        total_fields += len(tmpl.get("output", {}).get("fields", []))
        total_violations += len(tmpl.get("violations", []))

    return {
        "total_templates": len(all_templates),
        "total_fields": total_fields,
        "total_violations": total_violations,
        "by_domain": domains,
    }
