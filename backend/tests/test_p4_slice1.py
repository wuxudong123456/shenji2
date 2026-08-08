r"""Phase4 切片1 验收：P4-2 chunk 归一化（纯函数，不需 backend）

测 _normalize_chunks(raw_chunks, engine)：
  - 正常 OntoSKU chunks（{chunk_id,page_nums,bbox,type,text}）→ 全字段归一
  - alt 键名（id/pages/bounding_box/content/heading）→ 兼容映射
  - 缺字段（只 text）→ 默认值（chunk_id=chunk-N/chunk_type=text/page_nums=[]/bbox=None）
  - 降级 engine∈{liteparse,local-llm} → []（§3.4 不伪造）
  - raw_chunks 空 → []
  - page_nums 多形态：int / list / "3-5" / 字符串单页
  - bbox 多形态：4元组 / {x0,y0,x1,y1} / {left,top,...} / {x,y,w,h} / 垃圾→None

用法：cd backend && .venv\Scripts\python.exe tests\test_p4_slice1.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.task_worker import _normalize_chunks  # noqa: E402

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


def main():
    print("[test] Phase4 切片1：P4-2 chunk 归一化\n")

    # ① 正常 OntoSKU chunks
    print("── ① 正常 OntoSKU chunks ──")
    raw = [
        {"chunk_id": "c1", "type": "text", "page_nums": [1, 2],
         "bbox": [10.0, 20.0, 100.0, 200.0], "text": "甲方：某单位", "section_path": "一、当事人"},
        {"chunk_id": "c2", "type": "table", "page_nums": [3],
         "bbox": [0.0, 0.0, 500.0, 600.0], "text": "金额 100000 元"},
    ]
    out = _normalize_chunks(raw, "ontosku")
    check("归一后 2 条", len(out) == 2, str(out)[:120])
    check("① chunk_id 透传", out[0]["chunk_id"] == "c1", str(out[0]))
    check("① chunk_type 透传", out[0]["chunk_type"] == "text", str(out[0]))
    check("① page_nums=list", out[0]["page_nums"] == [1, 2], str(out[0]))
    check("① bbox=[x0,y0,x1,y1]", out[0]["bbox"] == [10.0, 20.0, 100.0, 200.0], str(out[0]))
    check("① text 透传", out[0]["text"] == "甲方：某单位", str(out[0]))
    check("① section_path 透传", out[0]["section_path"] == "一、当事人", str(out[0]))

    # ② alt 键名兼容（K2 §4 待校准 → 防御性多键名）
    print("\n── ② alt 键名兼容 ──")
    raw2 = [
        {"id": "x1", "chunk_type": "image", "pages": [5], "bounding_box": [1, 2, 3, 4],
         "content": "图1", "section": "附图"},
    ]
    out2 = _normalize_chunks(raw2, "ontosku")
    check("② id→chunk_id", out2[0]["chunk_id"] == "x1", str(out2[0]))
    check("② chunk_type 透传", out2[0]["chunk_type"] == "image", str(out2[0]))
    check("② pages→page_nums", out2[0]["page_nums"] == [5], str(out2[0]))
    check("② bounding_box→bbox", out2[0]["bbox"] == [1.0, 2.0, 3.0, 4.0], str(out2[0]))
    check("② content→text", out2[0]["text"] == "图1", str(out2[0]))
    check("② section→section_path", out2[0]["section_path"] == "附图", str(out2[0]))

    # ③ 缺字段 → 默认值
    print("\n── ③ 缺字段默认值 ──")
    out3 = _normalize_chunks([{"text": "只有文本"}], "ontosku")
    check("③ 缺 chunk_id 默认 chunk-0", out3[0]["chunk_id"] == "chunk-0", str(out3[0]))
    check("③ 缺 chunk_type 默认 text", out3[0]["chunk_type"] == "text", str(out3[0]))
    check("③ 缺 page_nums 默认 []", out3[0]["page_nums"] == [], str(out3[0]))
    check("③ 缺 bbox 默认 None", out3[0]["bbox"] is None, str(out3[0]))
    check("③ 缺 section_path 默认 None", out3[0]["section_path"] is None, str(out3[0]))

    # ④ 降级路径不伪造
    print("\n── ④ 降级路径 → []（不伪造）──")
    check("④ engine=liteparse → []",
          _normalize_chunks([{"text": "x", "page_nums": [1]}], "liteparse") == [])
    check("④ engine=local-llm → []",
          _normalize_chunks([{"text": "x", "page_nums": [1]}], "local-llm") == [])
    check("④ raw_chunks=[] → []",
          _normalize_chunks([], "ontosku") == [])
    check("④ raw_chunks=None → []",
          _normalize_chunks(None, "ontosku") == [])
    check("④ raw_chunks 非 list → []",
          _normalize_chunks("notalist", "ontosku") == [])

    # ⑤ page_nums 多形态
    print("\n── ⑤ page_nums 多形态 ──")
    p = _normalize_chunks([{"text": "a", "page": 3}], "ontosku")[0]["page_nums"]
    check("⑤ page int → [3]", p == [3], str(p))
    p = _normalize_chunks([{"text": "a", "page": "3-5"}], "ontosku")[0]["page_nums"]
    check("⑤ page '3-5' → [3,4,5]", p == [3, 4, 5], str(p))
    p = _normalize_chunks([{"text": "a", "page": "7"}], "ontosku")[0]["page_nums"]
    check("⑤ page '7' → [7]", p == [7], str(p))
    p = _normalize_chunks([{"text": "a", "page": [1, "2", 3]}], "ontosku")[0]["page_nums"]
    check("⑤ page list 混型 → [1,2,3]", p == [1, 2, 3], str(p))
    p = _normalize_chunks([{"text": "a", "page": "abc"}], "ontosku")[0]["page_nums"]
    check("⑤ page 垃圾 → []（不伪造）", p == [], str(p))

    # ⑥ bbox 多形态
    print("\n── ⑥ bbox 多形态 ──")
    b = _normalize_chunks([{"text": "a", "bbox": {"x0": 1, "y0": 2, "x1": 3, "y1": 4}}], "ontosku")[0]["bbox"]
    check("⑥ bbox {x0..} → [1,2,3,4]", b == [1.0, 2.0, 3.0, 4.0], str(b))
    b = _normalize_chunks([{"text": "a", "bbox": {"left": 1, "top": 2, "right": 3, "bottom": 4}}], "ontosku")[0]["bbox"]
    check("⑥ bbox {left..} → [1,2,3,4]", b == [1.0, 2.0, 3.0, 4.0], str(b))
    b = _normalize_chunks([{"text": "a", "bbox": {"x": 0, "y": 0, "w": 10, "h": 20}}], "ontosku")[0]["bbox"]
    check("⑥ bbox {x,y,w,h} → [0,0,10,20]", b == [0.0, 0.0, 10.0, 20.0], str(b))
    b = _normalize_chunks([{"text": "a", "bbox": "garbage"}], "ontosku")[0]["bbox"]
    check("⑥ bbox 垃圾 → None（不伪造）", b is None, str(b))

    # ⑦ K2 §4 联调校准：真实 OntoSKU 结构 metadata 嵌套（chunk_id/type/content/path/metadata）
    print("\n── ⑦ metadata 嵌套（真实 OntoSKU 结构）──")
    real = _normalize_chunks([
        {"chunk_id": "r1", "type": "text", "content": "采购合同明细表",
         "path": "tmp.pdf", "metadata": {"page_nums": [2], "bbox": [1, 2, 3, 4]}},
        {"chunk_id": "r2", "type": "text", "content": "无页码切片",
         "metadata": {"page_nums": []}},
    ], "ontosku")
    check("⑦ content→text", real[0]["text"] == "采购合同明细表", str(real[0]))
    check("⑦ metadata.page_nums=[2] 下钻命中", real[0]["page_nums"] == [2], str(real[0]))
    check("⑦ metadata.bbox 下钻命中", real[0]["bbox"] == [1.0, 2.0, 3.0, 4.0], str(real[0]))
    check("⑦ path 不映射 section_path（是文件名）", real[0]["section_path"] is None, str(real[0]))
    check("⑦ metadata.page_nums=[] → []（源端空，不伪造）",
          real[1]["page_nums"] == [], str(real[1]))

    print(f"\n{'='*48}")
    print(f"切片1 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
