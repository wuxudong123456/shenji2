r"""Phase3 切片7 验收：P3-4 三档降级（OntoSKU 主→LiteParse→本地 LLM 兜底）

直接测 task_worker._fallback_local_extract 的档位判定（OntoSKU 已在调用方失败，
本函数负责后两档），LiteParseClient / auto_classify_and_extract 打桩，不经网络/OCR：
  ① LiteParse 成功且文本非空(≥10) → engine='liteparse'
  ② LiteParse 失败 / 空白(扫描件) / 异常 → engine='local-llm'
  - liteparse 档文本=LiteParse产物；local-llm 档文本回落 existing_ocr
  - LLM 抽取异常 → {success:False, engine, error}

用法：cd backend && .venv\Scripts\python.exe tests\test_p3_slice7.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.ocr_client as oc  # noqa: E402
import services.extraction_service as es  # noqa: E402
import services.task_worker as tw  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def _set_lp(ret=None, exc=None):
    """打桩 LiteParseClient：LiteParseClient().parse(path) → ret / raise exc"""
    class _C:
        def parse(self, path):
            if exc:
                raise exc
            return ret
    oc.LiteParseClient = _C


def _set_aca(ret=None, exc=None):
    """打桩 auto_classify_and_extract(text) → ret / raise exc"""
    def _f(text):
        if exc:
            raise exc
        return ret
    es.auto_classify_and_extract = _f


def main():
    print("[test] Phase3 切片7：P3-4 三档降级 _fallback_local_extract\n")
    saved_lp, saved_aca = oc.LiteParseClient, es.auto_classify_and_extract
    LONG = "合同金额100万元 采购方式公开招标 供应商甲公司"  # >10 字符
    try:
        # ① LiteParse 命中实质文本 → liteparse 档
        print("── ① LiteParse 实质文本 → liteparse ──")
        _set_lp({"success": True, "text": LONG})
        _set_aca({"success": True, "fields": [{"name": "金额", "value": "100万"}]})
        r = tw._fallback_local_extract("/x.pdf", "")
        check("engine='liteparse'", r["engine"] == "liteparse", str(r))
        check("success=True", r.get("success") is True, str(r))
        check("text=LiteParse 产物", r["text"] == LONG, str(r)[:120])
        check("fields 经 LLM 抽取（含金额）", r["fields"].get("金额") == "100万", str(r))
        check("返回 shape 含 document_id/chunks", "document_id" in r and "chunks" in r, str(r))

        # ② LiteParse 空白（扫描件）→ local-llm，文本回落 existing_ocr
        print("\n── ② LiteParse 空白（扫描件）→ local-llm ──")
        _set_lp({"success": True, "text": "   \n  "})  # <10
        _set_aca({"success": True, "fields": []})
        r = tw._fallback_local_extract("/x.pdf", "历史ocr内容")
        check("engine='local-llm'", r["engine"] == "local-llm", str(r))
        check("文本回落 existing_ocr", r["text"] == "历史ocr内容", str(r)[:120])

        # ②' LiteParse success=False → local-llm
        print("\n── ②' LiteParse success=False → local-llm ──")
        _set_lp({"success": False})
        _set_aca({"success": True, "fields": []})
        r = tw._fallback_local_extract("/x.pdf", "")
        check("engine='local-llm'", r["engine"] == "local-llm", str(r))

        # ②'' LiteParse 异常 → local-llm
        print("\n── ②'' LiteParse 异常 → local-llm ──")
        _set_lp(exc=RuntimeError("LiteParse 不可达"))
        _set_aca({"success": True, "fields": []})
        r = tw._fallback_local_extract("/x.pdf", "")
        check("engine='local-llm'", r["engine"] == "local-llm", str(r))

        # ②''' LiteParse 短文本(<10非空)：仍 local-llm，但保留短文本
        print("\n── ②''' LiteParse 短文本(<10)→ local-llm，保留短文本 ──")
        _set_lp({"success": True, "text": "ab"})  # 非空但 <10
        _set_aca({"success": True, "fields": []})
        r = tw._fallback_local_extract("/x.pdf", "")
        check("engine='local-llm'", r["engine"] == "local-llm", str(r))
        check("保留 LiteParse 短文本", r["text"] == "ab", str(r))

        # ③ LLM 抽取异常 → success=False（engine 仍带档位标签）
        print("\n── ③ LLM 抽取异常 → success=False ──")
        _set_lp({"success": True, "text": LONG})  # 本应 liteparse 档
        _set_aca(exc=RuntimeError("LLM 宕机"))
        r = tw._fallback_local_extract("/x.pdf", "")
        check("success=False", r.get("success") is False, str(r))
        check("engine 仍='liteparse'（档位先定）", r["engine"] == "liteparse", str(r))
        check("error 含兜底说明", "LLM兜底抽取失败" in r.get("error", ""), str(r)[:120])
    finally:
        oc.LiteParseClient, es.auto_classify_and_extract = saved_lp, saved_aca

    print(f"\n{'='*48}")
    print(f"切片7 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
