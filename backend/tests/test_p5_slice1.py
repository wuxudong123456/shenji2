r"""Phase5 切片1 验收：P5-1 八类表映射（纯函数，不需 backend）

测三块：
  ① _classify_for_table 关键词分类：采购/招标/中标→采购类，访谈/谈话→访谈类，
     合同→合同协议类（采购合同仍命中「合同」优先→合同类，业务正确）
  ② _map_category_to_table：采购类→data_procurements，访谈类→data_interviews
  ③ field_mapper.map_extracted_fields：
     - data_procurements 别名（采购方式/供应商/预算金额/合同金额/招标日期/项目名称）
     - data_interviews 别名（被访谈人/访谈日期/地点）
     - NUMERIC_COLS 扩 budget_amount/contract_amount（万元换算）
     - DATE_COLS 扩 bid_date/interview_date（日期校验+归一化）
     - 未映射字段进 extra_fields

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice1.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.task_worker import _classify_for_table, _map_category_to_table  # noqa: E402
from services.field_mapper import map_extracted_fields  # noqa: E402

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
    print("[test] Phase5 切片1：P5-1 八类表映射\n")

    # ── ① _classify_for_table 关键词分类 ──
    print("── ① 分类关键词 ──")
    check("① 采购合同 → 合同协议类（合同优先命中）",
          _classify_for_table("", "采购合同.pdf") == "合同协议类",
          _classify_for_table("", "采购合同.pdf"))
    check("① 采购计划 → 采购类",
          _classify_for_table("", "采购计划.pdf") == "采购类",
          _classify_for_table("", "采购计划.pdf"))
    check("① 招标公告 → 采购类",
          _classify_for_table("", "招标公告.pdf") == "采购类",
          _classify_for_table("", "招标公告.pdf"))
    check("① 中标通知书 → 采购类",
          _classify_for_table("", "中标通知书.pdf") == "采购类",
          _classify_for_table("", "中标通知书.pdf"))
    check("① 访谈记录 → 访谈类",
          _classify_for_table("", "访谈记录.pdf") == "访谈类",
          _classify_for_table("", "访谈记录.pdf"))
    check("① 谈话笔录 → 访谈类",
          _classify_for_table("", "谈话笔录.pdf") == "访谈类",
          _classify_for_table("", "谈话笔录.pdf"))
    check("① 发票 → 财务票据类（不受影响）",
          _classify_for_table("", "发票.pdf") == "财务票据类")
    check("① 无关键词 → 其他杂项类",
          _classify_for_table("", "杂项.pdf") == "其他杂项类")
    # OCR 文本关键词也生效（不只是 filename）
    check("① 文本含「招标」→ 采购类",
          _classify_for_table("本项目招标方式为公开招标", "xxx.pdf") == "采购类")

    # ── 三阶段加固（方案A）：文件名弱信号词拦截，治 id5/6 错分类 ──
    print("── ① 三阶段：弱词拦截 / 强词优先 / 正文降级 ──")
    check("① 卷宗目录 → 资料材料类（弱词拦截，正文含合同也不误判）",
          _classify_for_table("本卷宗收录采购合同XX号", "001_项目资料卷宗目录.pdf") == "资料材料类",
          _classify_for_table("本卷宗收录采购合同XX号", "001_项目资料卷宗目录.pdf"))
    check("① 情况说明 → 资料材料类（弱词拦截）",
          _classify_for_table("项目引用了施工合同条款", "002_单位及项目基本情况说明.pdf") == "资料材料类",
          _classify_for_table("项目引用了施工合同条款", "002_单位及项目基本情况说明.pdf"))
    check("① 采购需求申请 → 采购类（不回归，trace152）",
          _classify_for_table("", "005_第一批采购需求申请.pdf") == "采购类",
          _classify_for_table("", "005_第一批采购需求申请.pdf"))
    check("① 采购目录 → 采购类（强词优先于弱词，不误伤）",
          _classify_for_table("", "采购目录.pdf") == "采购类",
          _classify_for_table("", "采购目录.pdf"))
    check("① 采购方案 → 采购类（强词优先于弱词）",
          _classify_for_table("", "采购方案.pdf") == "采购类",
          _classify_for_table("", "采购方案.pdf"))
    # 阶段③降级：纯编号文件名无强/弱信号 → 正文关键词仍生效
    check("① 纯编号文件名+正文合同 → 合同协议类（阶段③降级正文）",
          _classify_for_table("本合同由甲乙双方签订", "DHJY-2025-001.pdf") == "合同协议类",
          _classify_for_table("本合同由甲乙双方签订", "DHJY-2025-001.pdf"))
    # 扩充弱词：会议纪要/实施方案 → 资料材料类
    check("① 会议纪要 → 资料材料类（扩充弱词）",
          _classify_for_table("", "会议纪要.pdf") == "资料材料类",
          _classify_for_table("", "会议纪要.pdf"))
    check("① 实施方案 → 资料材料类（扩充弱词）",
          _classify_for_table("", "实施方案.pdf") == "资料材料类",
          _classify_for_table("", "实施方案.pdf"))

    # ── ② _map_category_to_table ──
    print("\n── ② 类别→表映射 ──")
    check("② 采购类 → data_procurements",
          _map_category_to_table("采购类") == "data_procurements")
    check("② 访谈类 → data_interviews",
          _map_category_to_table("访谈类") == "data_interviews")
    check("② 合同协议类 → data_contracts（不变）",
          _map_category_to_table("合同协议类") == "data_contracts")
    check("② 财务凭证类 → data_finance（不变）",
          _map_category_to_table("财务凭证类") == "data_finance")
    check("② 未知类 → data_general（兜底）",
          _map_category_to_table("不存在的类") == "data_general")

    # ── ③ data_procurements 别名 + 类型转换 ──
    print("\n── ③ data_procurements 字段映射 ──")
    row, extra = map_extracted_fields("data_procurements", {
        "采购方式": "公开招标",
        "供应商": "甲公司",
        "预算金额": "100万元",
        "合同金额": "200万",
        "招标日期": "2025-03-01",
        "项目名称": "办公电脑采购",
        "未映射字段": "xxx",
    })
    check("③ procurement_method", row.get("procurement_method") == "公开招标", str(row))
    check("③ supplier", row.get("supplier") == "甲公司", str(row))
    check("③ budget_amount 100万元 → 1000000.0",
          row.get("budget_amount") == 1000000.0, str(row))
    check("③ contract_amount 200万 → 2000000.0",
          row.get("contract_amount") == 2000000.0, str(row))
    check("③ bid_date 2025-03-01", row.get("bid_date") == "2025-03-01", str(row))
    check("③ subject_name", row.get("subject_name") == "办公电脑采购", str(row))
    check("③ 未映射字段进 extra_fields", "未映射字段" in extra, str(extra))

    # ── ④ data_interviews 别名 + 日期归一化 ──
    print("\n── ④ data_interviews 字段映射 ──")
    row2, extra2 = map_extracted_fields("data_interviews", {
        "被访谈人": "张三",
        "访谈日期": "2025/3/5",
        "地点": "三楼会议室",
    })
    check("④ interviewee", row2.get("interviewee") == "张三", str(row2))
    check("④ interview_date 2025/3/5 → 2025-03-05",
          row2.get("interview_date") == "2025-03-05", str(row2))
    check("④ location", row2.get("location") == "三楼会议室", str(row2))

    # ── ⑤ 类型校验：无效金额/日期 → None ──
    print("\n── ⑤ 类型校验（NUMERIC/DATE 扩列）──")
    row3, _ = map_extracted_fields("data_procurements", {
        "预算金额": "未提供",      # → None（空值词）
        "合同金额": "面议",        # → None（无数字）
        "招标日期": "2025年13月",  # → None（非法日期）
    })
    check("⑤ 预算金额「未提供」→ None", row3.get("budget_amount") is None, str(row3))
    check("⑤ 合同金额「面议」→ None", row3.get("contract_amount") is None, str(row3))
    check("⑤ 招标日期非法 → None", row3.get("bid_date") is None, str(row3))

    # ── ⑥ Phase3 加固：OntoSKU 信封键别名扩展（涉及金额/文档日期/谈话内容/strip）──
    print("\n── ⑥ OntoSKU 信封键别名扩展 ──")
    # data_procurements：涉及金额→contract_amount（trace152 真实场景）
    row6a, extra6a = map_extracted_fields("data_procurements", {
        "涉及金额": "1462800.0",
        "文档编号": "清政服采申〔2025〕11号",
        "文档日期": "2025-03-03",
        "涉及单位": "信息技术科",
    })
    check("⑥ 涉及金额→contract_amount", row6a.get("contract_amount") == 1462800.0, str(row6a))
    check("⑥ 文档编号无规范列→留 extra", "文档编号" in extra6a, str(extra6a))
    check("⑥ 文档日期无规范列→留 extra（procurements）", "文档日期" in extra6a, str(extra6a))
    check("⑥ 涉及单位无规范列→留 extra", "涉及单位" in extra6a, str(extra6a))
    # data_contracts：涉及金额→amount
    row6b, _ = map_extracted_fields("data_contracts", {"涉及金额": "50万"})
    check("⑥ contracts 涉及金额→amount 500000.0", row6b.get("amount") == 500000.0, str(row6b))
    # data_general：信封键 name/description/文档日期
    row6c, _ = map_extracted_fields("data_general", {
        "name": "某项目", "description": "内容摘要", "文档日期": "2025-01-01",
    })
    check("⑥ general name→title", row6c.get("title") == "某项目", str(row6c))
    check("⑥ general description→summary", row6c.get("summary") == "内容摘要", str(row6c))
    check("⑥ general 文档日期→doc_date", row6c.get("doc_date") == "2025-01-01", str(row6c))
    # data_interviews：谈话内容→transcript
    row6d, _ = map_extracted_fields("data_interviews", {"谈话内容": "笔录正文..."})
    check("⑥ interviews 谈话内容→transcript", row6d.get("transcript") == "笔录正文...", str(row6d))
    # data_legal_docs：文档日期→doc_date
    row6e, _ = map_extracted_fields("data_legal_docs", {"文档日期": "2025-06-06"})
    check("⑥ legal_docs 文档日期→doc_date", row6e.get("doc_date") == "2025-06-06", str(row6e))
    # strip 防御：带首尾空白的键仍命中
    row6f, _ = map_extracted_fields("data_procurements", {" 涉及金额 ": "123"})
    check("⑥ 带空白键 strip 后仍命中", row6f.get("contract_amount") == 123.0, str(row6f))

    print(f"\n{'='*48}")
    print(f"切片1 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
