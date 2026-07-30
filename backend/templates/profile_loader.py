"""Load YAML extraction templates into typed Python configs.

The template format originates from Hyper-Extract (yifanfeng97/Hyper-Extract).
We parse a subset of fields relevant to SKU extraction, keeping the loader
lightweight and independent of Hyper-Extract's runtime.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from shared.core.config import settings

# ── In-memory cache for list_profiles() ──
# Templates don't change at runtime (custom templates are rare), so we
# cache the full profile list in memory after the first read.  A lock
# guards the lazy initialisation.
_profiles_cache: list[dict] | None = None
_profiles_cache_lock = threading.Lock()


def invalidate_profiles_cache() -> None:
    """Clear the cached profile list (call after adding/removing custom templates)."""
    global _profiles_cache
    with _profiles_cache_lock:
        _profiles_cache = None


def _get_all_profile_dirs() -> list[Path]:
    """Collect all template directories: bundled + custom.

    Custom templates (SKU_CUSTOM_PROFILES_DIR) take precedence over
    bundled ones when a profile name conflicts.
    """
    dirs = []
    bundled = Path(__file__).parent / "profiles"
    if bundled.exists():
        dirs.append(bundled)

    custom_dir = getattr(settings, "SKU_CUSTOM_PROFILES_DIR", None) or ""
    if not custom_dir:
        # Fall back to the same default as the API's _get_custom_dir()
        # so templates saved via the web UI are automatically picked up.
        import os as _os
        # profile_loader.py → ontology → sku → services → shared → shared-python → packages → repo_root
        custom_dir = _os.path.join(
            _os.path.dirname(__file__), "..", "..", "..", "..", "..", "..",
            "apps", "api", "data", "sku_templates",
        )
    custom_path = Path(custom_dir).resolve()
    if custom_path.exists():
        dirs.insert(0, custom_path)  # custom first = higher priority

    return dirs


@dataclass
class SkuTemplate:
    """A domain-specific knowledge extraction template loaded from YAML."""

    name: str  # e.g. "earnings_summary"
    domain: str  # e.g. "finance"
    type: str  # model / graph / set / list / hypergraph / temporal_graph / ...
    language: str  # "zh" or "en"
    description: str  # localized description
    tags: list[str] = field(default_factory=list)

    # Prompt components (localized to target language)
    role: str = ""  # LLM role description (guideline.target)
    rules: str = ""  # Extraction rules (guideline.rules or .rules_for_entities + .rules_for_relations)

    # Output schema hints (for models that don't support json_schema)
    output_fields: list[dict] = field(default_factory=list)
    # For graph types: entities and relations have separate field lists
    entity_fields: list[dict] = field(default_factory=list)
    relation_fields: list[dict] = field(default_factory=list)

    # Identifiers (how to generate entity/relation IDs)
    entity_id_template: str = "{name}"  # e.g. "{name}" or "{name}|{type}"
    relation_id_template: str = "{source}|{type}|{target}"

    # Options
    extraction_mode: str = "single_pass"  # single_pass or two_stage

    @property
    def full_name(self) -> str:
        return f"{self.domain}/{self.name}"

    @property
    def is_graph_type(self) -> bool:
        return self.type in (
            "graph", "hypergraph", "temporal_graph",
            "spatial_graph", "spatio_temporal_graph",
        )

    @property
    def is_collection_type(self) -> bool:
        return self.type in ("list", "set")

    @property
    def is_model_type(self) -> bool:
        return self.type == "model"


def _localize(value, language: str) -> str:
    """Extract localized string from multilingual YAML value."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(f"{i+1}. {item}" for i, item in enumerate(value))
    if isinstance(value, dict):
        v = value.get(language)
        if isinstance(v, list):
            return "\n".join(f"{i+1}. {item}" for i, item in enumerate(v))
        return v or ""
    return str(value)


def _extract_fields(output_section: dict, language: str) -> list[dict]:
    """Extract field definitions from output section."""
    fields = output_section.get("fields", [])
    result = []
    for f in fields:
        result.append({
            "name": f.get("name", ""),
            "type": f.get("type", "str"),
            "description": _localize(f.get("description", ""), language),
            "required": f.get("required", True),
        })
    return result


def load_template(file_path: str | Path, language: str = "zh") -> SkuTemplate:
    """Load a single template YAML file.

    Args:
        file_path: Path to .yaml template file
        language: Target language for localization ("zh" or "en")

    Returns:
        SkuTemplate with all fields localized to the target language
    """
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Derive domain from parent directory name
    domain = path.parent.name

    t = SkuTemplate(
        name=raw.get("name", path.stem),
        domain=domain,
        type=raw.get("type", "model"),
        language=language,
        description=_localize(raw.get("description", ""), language),
        tags=raw.get("tags", []),
    )

    # Parse guideline (prompt components)
    guideline = raw.get("guideline", {})
    # Generated templates often have guideline as a plain string (extraction rules).
    # Treat it as rules with a sensible default role.
    if isinstance(guideline, str):
        t.role = "你是一个专业的信息提取助手。从给定文本中提取结构化字段，只返回精确值，不做归纳或概括。"
        t.rules = guideline
    elif isinstance(guideline, dict):
        t.role = _localize(guideline.get("target", ""), language)

        if t.is_graph_type:
            rules_parts = []
            entity_rules = _localize(guideline.get("rules_for_entities", ""), language)
            relation_rules = _localize(guideline.get("rules_for_relations", ""), language)
            if entity_rules:
                rules_parts.append(f"[实体抽取规则]\n{entity_rules}")
            if relation_rules:
                rules_parts.append(f"[关系抽取规则]\n{relation_rules}")
            # Time/location rules for temporal/spatial graphs
            for key, label in [("rules_for_time", "时间规则"), ("rules_for_location", "位置规则")]:
                extra = _localize(guideline.get(key, ""), language)
                if extra:
                    rules_parts.append(f"[{label}]\n{extra}")
            t.rules = "\n\n".join(rules_parts)
        else:
            rules = guideline.get("rules", guideline.get("rules_for_entities", ""))
            t.rules = _localize(rules, language)

    # Parse output schema
    output = raw.get("output", {})
    if t.is_graph_type:
        entities = output.get("entities", {})
        relations = output.get("relations", {})
        t.entity_fields = _extract_fields(entities, language)
        t.relation_fields = _extract_fields(relations, language)
    else:
        t.output_fields = _extract_fields(output, language)

    # Parse identifiers (entity/relation ID generation)
    identifiers = raw.get("identifiers", {})
    if identifiers:
        t.entity_id_template = identifiers.get("entity_id", "{name}")
        t.relation_id_template = identifiers.get("relation_id", "{source}|{type}|{target}")

    # Parse options
    options = raw.get("options", {})
    t.extraction_mode = options.get("extraction_mode", "single_pass")

    return t


def list_profiles(base_dir: str | Path | None = None) -> list[dict]:
    """List all available template profiles with metadata.

    Results are cached in memory after first call because the 1500+ template
    YAML files don't change at runtime (custom templates added via the API
    call ``invalidate_profiles_cache()``).

    Args:
        base_dir: Root of profiles directory. If None, scans both bundled
                  profiles/ and SKU_CUSTOM_PROFILES_DIR.

    Returns:
        Sorted list of dicts with keys: name (domain/name), domain, description, tags.
    """
    global _profiles_cache

    # Custom base_dir — don't cache (rarely used)
    if base_dir is not None:
        return _scan_profiles([Path(base_dir)])

    # Fast path: return cached list
    if _profiles_cache is not None:
        return _profiles_cache

    with _profiles_cache_lock:
        if _profiles_cache is not None:
            return _profiles_cache
        _profiles_cache = _scan_profiles(_get_all_profile_dirs())
        return _profiles_cache


def _scan_profiles(dirs: list[Path]) -> list[dict]:
    """Scan profile directories and return metadata for every template."""
    seen: set[str] = set()
    profiles: list[dict] = []

    for base in dirs:
        if not base.exists():
            continue
        for yaml_file in sorted(base.rglob("*.yaml")):
            rel = yaml_file.relative_to(base)
            domain_path = str(rel.parent).replace("\\", "/")
            name = yaml_file.stem
            domain = yaml_file.parent.name
            full_name = f"{domain_path}/{name}" if domain_path != "." else name
            if full_name in seen:
                continue
            seen.add(full_name)

            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
            except Exception:
                raw = {}

            description = raw.get("description", "")
            if isinstance(description, dict):
                description = description.get("zh", description.get("en", ""))
            tags = raw.get("tags", [])

            profiles.append({
                "name": full_name,
                "domain": domain,
                "description": str(description or ""),
                "tags": list(tags) if tags else [],
            })

    return profiles


def load_profile(profile_name: str, language: str = "zh",
                 base_dir: str | Path | None = None) -> SkuTemplate:
    """Load a template by profile name like 'finance/earnings_summary'.

    Args:
        profile_name: e.g. "finance/earnings_summary" or "general/graph"
        language: "zh" or "en"
        base_dir: Root of profiles directory. If None, searches both
                  bundled profiles/ and SKU_CUSTOM_PROFILES_DIR.

    Returns:
        Loaded and localized SkuTemplate

    Raises:
        FileNotFoundError: if profile not found
    """
    if "/" in profile_name:
        parts = profile_name.rsplit("/", 1)
        domain_path = parts[0]
        name = parts[1]
    else:
        domain_path = "general"
        name = profile_name

    # Search in all profile dirs (custom first)
    if base_dir is not None:
        dirs = [Path(base_dir)]
    else:
        dirs = _get_all_profile_dirs()

    for base in dirs:
        yaml_path = base / domain_path / f"{name}.yaml"
        if yaml_path.exists():
            return load_template(yaml_path, language)

    # Not found — provide helpful error
    available = [p["name"] for p in list_profiles(base_dir)]
    raise FileNotFoundError(
        f"Profile '{profile_name}' not found. "
        f"Available: {available}"
    )
