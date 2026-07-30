"""提示词统一加载器

从 backend/prompts/ 下的 .txt 文件加载提示词文本。
- 首次加载后内存缓存，后续读取零磁盘开销
- 支持热重载（开发模式）
- 文件不存在时报清晰错误，不静默失败

约定:
  load_prompt("agents/intent_analyzer")  → prompts/agents/intent_analyzer.txt
  load_prompt("extraction/classify")     → prompts/extraction/classify.txt
"""
from pathlib import Path
from typing import Optional

PROMPTS_DIR = Path(__file__).resolve().parent
_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """加载提示词文本

    Args:
        name: 相对于 prompts/ 的路径（不含 .txt 后缀）
              如 "agents/intent_analyzer"、"extraction/classify"

    Returns:
        提示词文本（原始内容，不做任何修改）

    Raises:
        FileNotFoundError: 提示词文件不存在
    """
    if name in _cache:
        return _cache[name]

    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"提示词文件不存在: {path}\n"
            f"请检查 prompts/ 目录结构，或创建该文件。"
        )

    text = path.read_text(encoding="utf-8")
    _cache[name] = text
    return text


def clear_cache():
    """清除缓存（用于热重载或测试）"""
    _cache.clear()


def list_prompts() -> list[str]:
    """列出所有已注册的提示词名称"""
    result = []
    for txt in PROMPTS_DIR.rglob("*.txt"):
        rel = txt.relative_to(PROMPTS_DIR).with_suffix("")
        result.append(str(rel).replace("\\", "/"))
    return sorted(result)
