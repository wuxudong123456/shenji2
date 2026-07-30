"""LLM 客户端 — OpenAI 兼容接口，支持本地代理池 / DeepSeek / Ollama"""
import os
import json
import requests
from config import Config


def call_llm(
    prompt: str,
    system_prompt: str = None,
    model: str = None,
    max_tokens: int = 8192,
    temperature: float = 0.1,
    response_format: dict = None,
    timeout: int = 300,
) -> str:
    """调用 OpenAI 兼容的 LLM，返回响应文本。

    Args:
        prompt: 用户消息
        system_prompt: 系统提示词
        model: 模型名（默认 deepseek-v4-flash）
        max_tokens: 最大输出 token
        temperature: 温度
        response_format: 如 {"type": "json_object"} 启用 JSON 模式
        timeout: 请求超时秒数

    Returns:
        str: 模型回复文本

    Raises:
        Exception: 非 200 响应
    """
    api_base = (os.environ.get("LLM_API_BASE", "") or
                getattr(Config, "LLM_API_BASE", None) or
                "http://127.0.0.1:8765/v1").rstrip("/")
    model = model or os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    api_key = os.environ.get("LLM_API_KEY", "any")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format

    resp = requests.post(
        f"{api_base}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )

    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise Exception(f"LLM 调用失败 HTTP {resp.status_code}: {resp.text[:300]}")


def call_llm_json(
    prompt: str,
    system_prompt: str = None,
    model: str = None,
    max_tokens: int = 8192,
    temperature: float = 0.1,
    timeout: int = 300,
) -> dict:
    """调用 LLM 并解析 JSON 响应。

    Returns:
        dict: 解析后的 JSON 对象；失败返回 {"error": "..."}
    """
    try:
        text = call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
            timeout=timeout,
        )
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取 JSON 片段
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "JSON 解析失败", "raw": text[:500]}
    except Exception as e:
        return {"error": str(e)}


def health() -> bool:
    """检查 LLM 服务可达性"""
    try:
        api_base = (os.environ.get("LLM_API_BASE", "") or "http://127.0.0.1:8765/v1").rstrip("/")
        resp = requests.get(f"{api_base}/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False
