# -*- coding: utf-8 -*-
"""改动① — LLM 语义判定:规则引擎降级兜底

当 execute_expression 返回语法错或 0 命中时(且非聚合 needs_review 门禁),
按规则语义用 LLM 逐批判定数据行,解决"规则语法对但值对不上"的语义病(病B)。

架构定位(探索钉死):
  - 只被 build_and_execute(execution_planner.py:85 之后)调用,不污染 execute_expression 引擎层
    (test_p9_t6_llm_down.py 单测 execute_expression,不触达此处,零 LLM 依赖红线保住)
  - 不复用 sql_generator.get_or_generate_sql(它会无脑写 audit_expression_sql 缓存污染人工审批列表)
  - 用 call_llm_json(吞错返 {error},不 raise),LLM 不可用时 health() 为假直接返 judged=False 零副作用

成本/雪崩防护(用户已确认前300行上限):
  - BATCH_SIZE=30 / MAX_BATCHES=10 → 单违规最多判前 300 行,超出诚实标注"余X行未判"
  - FAILURE_CIRCUIT=3 → 连续 3 批失败熔断停止(避免雪崩)
  - LLM_TIMEOUT=30(不用默认 300)
"""
import json

from services.llm_client import call_llm_json, health as llm_health
from services.db import query

# ── 成本/熔断参数(用户已确认)──
BATCH_SIZE = 30          # 每批喂 LLM 的行数(平衡 token 与延迟)
MAX_BATCHES = 10         # 单违规最多判多少批 → 最多判前 300 行
FAILURE_CIRCUIT = 3      # 连续失败 N 次熔断
LLM_TIMEOUT = 30         # 秒(非默认 300,避免长尾拖垮扫描)
MAX_ROWS_FETCH = 2000    # 取数上限(对齐 execute_expression LIMIT)


def judge_violation_via_llm(expression: str, table: str, project_id: str,
                            violation_name: str = "",
                            required_data: str = None) -> dict:
    """LLM 语义判定入口

    Args:
        expression: 违规表达式原文(可能语法错,LLM 按语义理解)
        table: 目标表(data_*)
        project_id: 项目ID
        violation_name: 违规名称(给 LLM 上下文)
        required_data: 规则的所需数据 JSON(可选,提升准确度)

    Returns:
        {judged, hits, rows, judged_count, error, batches, circuit_tripped}
        judged=False 表示未判定(LLM不可用/无数据/熔断),调用方应保持原 scan 不变
    """
    # 1. 取数据(无数据无需判定)
    # 注:不在入口做 llm_health() 硬门禁——health() 用5s探测 /models 易因网络抖动返False,
    #    导致 LLM 实际可用却放弃降级(时灵时不灵)。改为让 call_llm_json 实际调用结果决定成败:
    #    真不可用时首批返{error}→fail_streak累积→熔断或全失败返judged=False(零副作用不变)。
    rows = _fetch_rows(table, project_id)
    if not rows:
        return {"judged": False, "error": "无数据", "hits": 0, "rows": [],
                "judged_count": 0, "batches": 0, "circuit_tripped": False}

    # 3. 动态 schema(弃用 sql_generator 静态 hint,只覆盖6表不全)
    schema = _get_schema_dynamic(table)

    # 4. 分批判定(最多 MAX_BATCHES 批 = 前 300 行)
    max_judge = MAX_BATCHES * BATCH_SIZE
    rows_to_judge = rows[:max_judge]
    truncated = len(rows) - len(rows_to_judge)

    all_hits = []
    fail_streak = 0
    batches_done = 0
    circuit_tripped = False

    for i in range(0, len(rows_to_judge), BATCH_SIZE):
        batch = rows_to_judge[i:i + BATCH_SIZE]
        result = _judge_batch(expression, violation_name, batch, schema, required_data)

        if result.get("error"):
            fail_streak += 1
            if fail_streak >= FAILURE_CIRCUIT:
                circuit_tripped = True
                break
            continue
        fail_streak = 0
        all_hits.extend(result.get("violated_rows", []))
        batches_done += 1

    # 5. 汇总
    note_parts = []
    if truncated > 0:
        note_parts.append(f"已判前 {len(rows_to_judge)} 行,余 {truncated} 行未判")
    if circuit_tripped:
        note_parts.append(f"熔断(连续 {FAILURE_CIRCUIT} 批失败)")
    if batches_done == 0 and not all_hits:
        # 一批都没成功 → 未判定,保持原 scan
        return {"judged": False, "error": "；".join(note_parts) or "全部批次失败",
                "hits": 0, "rows": [], "judged_count": 0,
                "batches": 0, "circuit_tripped": circuit_tripped}

    return {
        "judged": True,
        "hits": len(all_hits),
        "rows": all_hits,
        "judged_count": len(rows_to_judge),
        "batches": batches_done,
        "circuit_tripped": circuit_tripped,
        "note": "；".join(note_parts),
    }


def _fetch_rows(table: str, project_id: str) -> list:
    """取目标表该项目的行(对齐 execute_expression 的取数方式)"""
    if not table or not table.startswith("data_"):
        return []
    if not project_id:
        return []  # 降级判定必须有项目上下文,空项目不判
    try:
        return query(
            f"SELECT * FROM {table} WHERE project_id = %s LIMIT %s",
            (project_id, MAX_ROWS_FETCH), database="tt",
        )
    except Exception:
        return []


def _get_schema_dynamic(table: str) -> str:
    """从 information_schema 动态取列名(sql_generator 静态 hint 只覆盖6表且手写子集)

    注意：information_schema 返回的列名是大写 COLUMN_NAME/COLUMN_COMMENT（MySQL 元数据特性，
    与业务表的小写列名不同），必须大小写不敏感取值，否则 KeyError 被 except 吞掉返"未知表结构"→
    LLM 完全不知列含义→误判 0 命中。
    """
    try:
        rows = query(
            "SELECT column_name, column_comment FROM information_schema.columns "
            "WHERE table_schema = 'tt' AND table_name = %s ORDER BY ordinal_position",
            (table,), database="tt",
        )
        if rows:
            parts = []
            for r in rows:
                # 大小写不敏感取值（information_schema 返大写 key）
                name = r.get("column_name") or r.get("COLUMN_NAME") or ""
                comment = r.get("column_comment") or r.get("COLUMN_COMMENT") or ""
                parts.append(f"{name}({comment})" if comment else name)
            return ", ".join(parts)
    except Exception:
        pass
    return "(未知表结构)"


def _judge_batch(expression: str, violation_name: str, batch: list,
                 schema: str, required_data: str) -> dict:
    """单批 LLM 判定

    Returns:
        {violated_rows: [{row_id, reason}], note} 或 {error}
    """
    # 清洗行数据:截断长字段防 token 爆炸,每行带 _rid 供回溯
    # 注意:Decimal/date 等 json.dumps 不原生支持的类型,若保留原值会让 default=str
    # 产出 "Decimal('4400000.00')" 误导 LLM 判定。这里统一 str() 转纯文本值。
    from decimal import Decimal
    from datetime import date, datetime
    rows_json = []
    for r in batch:
        clean = {}
        for k, v in r.items():
            if isinstance(v, (bytes,)):
                continue
            # Decimal/date 等非 JSON 原生类型 → 转字符串（防 default=str 产出 "Decimal('...')"）
            if isinstance(v, (Decimal, date, datetime)):
                v = str(v)
            sv = str(v) if v is not None else ""
            clean[k] = sv[:200] if len(sv) > 200 else v
        rows_json.append({"_rid": r.get("id"), **clean})

    rd_hint = ""
    if required_data:
        rd_hint = f"\n## 规则期望数据(JSON)\n{required_data}"

    prompt = f"""你是严谨的审计违规判定助手。判断以下数据行是否违反给定规则。

## 违规规则
- 名称: {violation_name or '(未提供)'}  ← **首要依据:违规名称表达的业务本意**
- 表达式: {expression}  ← 参考用(可能写法偏窄或有瑕疵,不可机械套用字面值)
{rd_hint}

## 表结构({schema})

## 待判定数据(JSON数组,_rid 为行ID用于回溯)
{json.dumps(rows_json, ensure_ascii=False, default=str)}

## 判定要求
1. **以违规名称的业务本意为准**判断每行是否违规。表达式常写得偏窄(如只写某一种具体方式),
   但规则本意通常覆盖一类情形。例如名称含"未公开招标/未招标"的规则,本意是查"应招标却走了
   任何非招标方式"(询价/单一来源/竞争性谈判等都算),不能因表达式只写"单一来源"就放过"询价"
2. 语义等价归类:同类业务概念应归并判断(非招标采购方式=公开招标以外所有方式;大额=超限额;
   应批未批=未经规定审批环节)。当数据值与表达式字面不同类但业务语义同类时,按规则本意判断
3. **审计原则:宁可多报待人工核实,不可漏报**。存疑的行判为违规并在reason说明疑点,由人工复核
4. 只返回违规/存疑行,明确合规的行不返回
5. reason 用一句话说明为什么这行违规(引用具体数据值)

## 输出格式(严格JSON,不要markdown代码块)
{{"violated_rows": [{{"row_id": <_rid整数>, "reason": "<一句话理由>"}}], "note": "<可选说明>"}}
"""
    result = call_llm_json(
        prompt=prompt,
        system_prompt="你是严谨的审计助手,只返回JSON,不要多余解释。",
        max_tokens=2048,
        temperature=0.1,
        timeout=LLM_TIMEOUT,
    )
    if result.get("error"):
        return {"error": result["error"]}

    # 校验结构
    violated = result.get("violated_rows", [])
    if not isinstance(violated, list):
        return {"error": "LLM 返回 violated_rows 非数组"}

    # 清理 row_id(确保是 int)
    cleaned = []
    for v in violated:
        if not isinstance(v, dict):
            continue
        rid = v.get("row_id")
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            continue
        cleaned.append({"row_id": rid, "reason": str(v.get("reason", ""))[:200]})

    return {"violated_rows": cleaned, "note": result.get("note", "")}
