# Phase 5 验收报告：数据工坊（可隔离地读）

> 执行包：[docs/phase-exec/PHASE_5.md](phase-exec/PHASE_5.md)（唯一执行依据）
> 前置：Phase 4（文档与字段溯源）已完成验收
> 验收日期：2026-08-08

## 1. 目标回顾

Phase 3 把结构化数据**写进**了 6 张 `data_*` 表，Phase 4 把行溯源链建好——但 data_*「**读出来**」这一侧未加固。Phase 5 建数据工坊，让结构化数据「可被正确、隔离地读」，为 Phase 8 智能分析做数据前置：

- 8 类表映射落地（扩 2 表：采购/访谈）；
- `DataService` 统一查询层（全迁入）；
- 强制 `project_id` 双模式（全局浏览 vs 项目分析，空 ID→400）；
- 分页 + 字段筛选（白名单列防注入）；大数据读字段裁剪/游标/超时保护；
- 数据质量检查（空值率/金额单位 决策11）+ 必填字段缺失清单；
- 数据行可溯源回 trace（复用 Phase 4）；跨项目隔离。

### 已确认的两个决策（用户拍板）

1. **P5-4 空/伪造 project_id → `400 Bad Request`**（非 403；鉴权留 Phase 6，现在无用户-项目-角色体系，403 会误读为鉴权失败）。
2. **DataService 激进全迁入**：现有 3 路由（表统计×2/行查询）逻辑全部下沉 `DataService`，路由变薄包装。忠实执行包 §3.1「所有 data_* 查询经此封装」。

## 2. 落地内容（按切片）

| 切片 | 任务 | 产物 | commit |
|---|---|---|---|
| 0 | M005 DDL | `data_procurements`/`data_interviews` 迁移 `migrate_phase5_data_tables()` | 5ce5fb2 |
| 1 | P5-1 八类表映射 | task_worker 分类（采购/访谈拆出）+ field_mapper 别名 + 8 表白名单 | 3bece3c |
| 2 | P5-2/3/4 DataService 核心 | `data_service.py`（双模式/强制 project_id）+ 路由薄包装 + 新增项目级行查询 | 02d5a74 |
| 3 | P5-5 字段筛选 | `list_rows` filters + `parse_query_filters`（白名单列防注入） | 1c28b12 |
| 4 | P5-6 裁剪/游标/超时 | 默认剥 LARGE_FIELDS + `?after=` 游标 + MAX_EXECUTION_TIME hint | 6c09888 |
| 5 | P5-7/8 质量/缺失 | `quality_check`/`missing_check` + `/quality` `/missing` 路由 | 95ec719 |
| 6 | P5-9/10 溯源/隔离 | 纯测试（P5-9 复用 Phase4 `/traces/data_row`，P5-10 复用 DataService 隔离） | 91441b4 |
| 7 | e2e + 验收 | `test_p5_data.py` 全链 + 回归 + 基线 + 本报告 | （本切片） |

## 3. §9 完成标准逐项

- [x] **数据库 M005 迁移执行成功（决策 8 两表），可回滚** —— `data_procurements`/`data_interviews`，`_table_exists` 逐表预检幂等，跑两次第二次全跳过。`data_interviews` 补回 `template_name`/`doc_type`（执行包 §5 DDL 漏列，但 `_insert_into_data_table` 硬编码 7 公共列必写，已在 migrate docstring + schema 注释标注该校正）。回滚 DDL 见执行包 §5。
- [x] **8 类表映射落地（白名单 + field_mapper 别名 + 分类→表）** —— `DATA_TABLES`（8 表权威）替代 inline 6 表×3；`_classify_for_table` 拆「采购」出合同类、「访谈」新增（关键词顺序敏感，采购合同仍→合同类）；`_map_category_to_table` 加采购/访谈；field_mapper 补两表别名 + NUMERIC/DATE 扩列。
- [x] **强制 project_id 双模式：全局浏览 vs 项目分析（空 ID 失败）（P5-4）** —— `require_project=False` 全局浏览（project_id 可空，硬 cap 200）；`require_project=True` 项目分析（project_id 必填，空→`ProjectIDRequiredError`→400）。
- [x] **DataService 服务层强制 project_id（不信任 LLM）（P5-3/P5-4）** —— WHERE 由 DataService 内部附加 `project_id=%s`，调用方/LLM 无法绕过；新增 `/projects/<id>/data/<table>/rows` 项目分析路由（路径参数强制）。
- [x] **分页 + 字段筛选；大数据读字段裁剪/游标/超时保护（P5-5/P5-6）** —— 等于/金额范围/日期范围筛选（白名单列防注入，非白名单静默忽略）；默认剥 `raw_text`/`transcript`，`?fields=` 取回；`?after=<id>` 游标（避开 OFFSET 深翻越慢）；SELECT 带 `MAX_EXECUTION_TIME(10s)` hint。
- [x] **数据质量检查（含金额单位 决策 11）+ 缺失字段清单（P5-7/P5-8）** —— `quality_check`：每表空值率 + 金额列 min/max + 单位软告警（max>1e9「疑似万元/亿元混入」/ max<10「疑似应为万元单位」）；`missing_check`：KEY_COLS（应用层关键业务列，DB 仅 project_id NOT NULL）缺失清单。
- [x] **数据行溯源回 trace（复用 Phase 4）（P5-9）** —— data_* 行带 `document_trace_id`（Phase 3 已写）；`GET /traces/data_row/<row_id>?table=` 行→trace→chunk 链复用，零代码，本 Phase 加测试覆盖。
- [x] **跨项目隔离测试通过（P5-10）** —— DataService 项目分析查询强制 `WHERE project_id`，重叠筛选值下仍隔离（两项目同 `party_a` 不串）；全局浏览不暴露 raw_text。
- [x] **§8 验收脚本全部通过** —— 见下表测试统计（150/150）。
- [x] **`05-regression-baseline.md` 回归通过（Phase 1-4 行为未破坏）** —— §3 数据接口基线已更新（标注双模式 + 新增 /quality /missing）；p1/p2/p3/p4 全量回归全绿。

## 4. 测试统计

| 测试 | 结果 |
|---|---|
| `test_p5_slice1.py`（P5-1 八类表映射纯函数） | PASS=27 FAIL=0 |
| `test_p5_slice2.py`（DataService 双模式 + 强制 project_id） | PASS=28 FAIL=0 |
| `test_p5_slice3.py`（P5-5 字段筛选 + 防注入） | PASS=18 FAIL=0 |
| `test_p5_slice4.py`（P5-6 裁剪 + 游标 + 超时） | PASS=15 FAIL=0 |
| `test_p5_slice5.py`（P5-7 质量 + P5-8 缺失） | PASS=23 FAIL=0 |
| `test_p5_slice6.py`（P5-9 溯源 + P5-10 隔离） | PASS=16 FAIL=0 |
| `test_p5_data.py`（端到端全链） | PASS=23 FAIL=0 |
| **Phase 5 小计** | **150/150** |
| `test_p1_flow.py` | 通过 7 失败 0 |
| `test_p2_*`（7 文件） | 通过 86 失败 0 |
| `test_p3_*`（slice3/456/7/9 + ocr） | PASS=94 FAIL=0 |
| `test_p4_*`（slice1-5 + trace） | PASS=141 FAIL=0 |

Phase 1-4 行为零破坏（328 + p1 共 335 全绿），Phase 5 新增 150 全绿，全量 **478/478**。

## 5. 行为变更说明（基线已同步）

- **数据路由 3→5（契约兼容）**：
  - `/projects/<id>/data`、`/data/tables`：返回结构不变（`{success,tables/rows}`），逻辑下沉 DataService，白名单 6→8 表。
  - `/data/<table>/rows`：原 project_id 可选→现**全局浏览模式**（无 project_id，硬 cap 200，跨项目）。无 project_id 调用仍可用（不破坏现有）。
  - **新增** `/projects/<id>/data/<table>/rows`（项目分析，路径参数强制隔离）。
  - **新增** `/projects/<id>/data/quality`、`/projects/<id>/data/missing`。
- **行查询默认裁剪 `raw_text`**：列表响应不再含大字段（`?fields=raw_text` 显式取回）。原 `SELECT *` 内联清洗漏 Decimal，现 DataService `_clean_row` 统一（Decimal→float，行为更对，非破坏）。
- **NL2SQL `/data/query` 未触碰**（Phase 8 智能分析闭环）。
- 基线 [05-regression-baseline.md](dev-specs/05-regression-baseline.md) §3 已更新（8 表 + 双模式 + 新端点），符合 §7「行为变更须显式标注并更新基线」。

## 6. 关键技术发现（联调校准）

- **`data_interviews` 漏列校正**：执行包 §5 DDL 未列 `template_name`/`doc_type`，但 `_insert_into_data_table`（task_worker）硬编码 7 公共列（project_id/document_trace_id/template_name/doc_name/doc_type/extra_fields/raw_text）逐表写入。新表缺列会写崩。已补两列（migrate docstring + schema 注释标注该校正依据）。属现状核对、非臆造。
- **空值统计的 DATE/DECIMAL 类型强转**：`SUM(CASE WHEN col='' ...)` 对 DATE/DECIMAL 列触发 `Incorrect DATE value: ''`。改用 `CAST(col AS CHAR)=''` 统一类型安全（适用所有列类型）。
- **游标 `next_cursor` 始终返回**：首页（`after=None`，OFFSET 模式）若不返回 `next_cursor`，游标链无法从首页切入。改为始终返回（满页取末行 id，到尾 None），OFFSET 与游标仅差是否用 `after`/OFFSET。
- **金额范围 OR 语义**：表有多金额列时（finance 借/贷、procurements 预算/合同），范围条件按「各列各自落在 [min,max] → OR 任一命中」拼接（单列即 AND 闭区间），避免 `>=min OR <=max` 误匹配全表。

## 7. 不做（边界，归属后续 Phase）

- **NL2SQL `/data/query` 闭环（执行伪 SQL）** → Phase 8 智能分析；
- 用户-项目-角色**鉴权**（403 体系）→ Phase 6（本 Phase 只做数据层 project_id 隔离）；
- 流式批量导出 + 压测 → Phase 9；
- field_mapper 别名自动扩展 → Phase 7；
- 音频转写填 data_interviews → 决策7 后续（本 Phase 表占位，统计为 0 属正常）。

## 8. 验收结论

Phase 5「数据工坊：可隔离地读」目标达成：8 类表映射落地，所有 data_* 查询经 DataService 统一封装，强制 project_id 双模式（项目分析空 ID→400），字段筛选/裁剪/游标/超时保护齐备，质量与缺失检查就绪，行溯源复用 Phase 4 通畅，跨项目隔离经测试验证。§9 完成标准全部满足；Phase 1-4 行为零破坏（基线已同步行为变更）。至此智能分析的数据前置就绪。
