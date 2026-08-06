"""审计工坊冒烟测试（Phase 0 P0-9 建立）

用途：确认后端服务 + 依赖（MySQL/MinIO/OCR/LLM）基本可用。
方式：HTTP 调用运行中的 Flask 服务（默认 http://127.0.0.1:5000），不直连数据库。
用法：
    # 先启动后端：cd backend && python app.py
    python backend/tests/smoke_test.py [BASE_URL]

退出码：全部通过 0；任一失败 1。
"""
import sys
import json
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"

PASS = 0
FAIL = 0


def check(name: str, url: str, expect_ok: bool = True, extra_assert=None, timeout: int = 10):
    """发起 GET 并断言 JSON 响应。extra_assert: dict -> bool"""
    global PASS, FAIL
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            ok = expect_ok and r.status == 200
            if extra_assert and data:
                ok = ok and extra_assert(data)
            if ok:
                PASS += 1
                print(f"  ✅ {name}")
            else:
                FAIL += 1
                print(f"  ❌ {name}: 断言失败 {json.dumps(data, ensure_ascii=False)[:200]}")
    except urllib.error.HTTPError as e:
        FAIL += 1
        print(f"  ❌ {name}: HTTP {e.code}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")


def main():
    print(f"[smoke] 目标 {BASE}\n")

    print("[1/4] 基础健康检查")
    check("GET /api/health", f"{BASE}/api/health")
    check("GET /api/ocr/health", f"{BASE}/api/ocr/health")
    check("GET /api/llm/health", f"{BASE}/api/llm/health")

    print("\n[2/4] 项目接口（audit 路由）")
    def has_projects(d):
        return isinstance(d.get("projects"), list)
    check("GET /api/audit/projects", f"{BASE}/api/audit/projects", extra_assert=has_projects)

    print("\n[3/4] 知识接口（法规/违规库）")
    def has_regs(d):
        return isinstance(d.get("regulations"), list)
    check("GET /api/audit/knowledge/regulations?q=招标&per_page=5",
          f"{BASE}/api/audit/knowledge/regulations?q=%E6%8B%9B%E6%A0%87&per_page=5",
          extra_assert=has_regs)
    def has_violations(d):
        return isinstance(d.get("violations"), list)
    check("GET /api/audit/knowledge/violations?q=化整为零&per_page=5",
          f"{BASE}/api/audit/knowledge/violations?q=%E5%8C%96%E6%95%B4%E4%B8%BA%E9%9B%B6&per_page=5",
          extra_assert=has_violations)

    print("\n[4/4] 模板接口")
    def has_templates(d):
        return isinstance(d.get("templates"), list)
    # 模板接口冷加载 1000+ YAML，已知延迟 >10s，放宽到 30s（见 05-regression-baseline §7）
    check("GET /api/audit/templates?limit=5", f"{BASE}/api/audit/templates?limit=5",
          extra_assert=has_templates, timeout=30)

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
