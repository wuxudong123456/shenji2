# PHASE_5 执行包：数据工坊

> **执行协议**：本文件是 Phase 5 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 3（data_* 写入：六表 + `document_trace_id` + `doc_type`）+ Phase 4（行溯源：`traces` 接口 + `audit_field_sources`）已完成。
> 铁律：不破坏 Phase 1-4 已验收行为；智能分析只读结构化 `data_*`，禁止读原始文件全文；服务层强制 `project_id`，不信任 LLM 生成该条件。

---

## 0. 执行者须知（先读）

- **关键认知：data_* 查询接口已有基础**（非占位）：
  - `GET /projects/<id>/data`（6 表行数）、`GET /data/tables`（全库行数）、`GET /data/<table>/rows`（分页，`project_id` 可选）已通；
  - 6 张 data_* 表 + `field_mapper` 六类别名表已存在（Phase 3）。
  - 本 Phase 是**补全/加固/隔离**——扩表到 8 张、强制 `project_id` 双模式、大数据读保护、质量/缺失检查、行溯源接通。
- **只做本 Phase 的事**：结构化数据的**查询/质量/隔离基础**，使智能分析前置就绪。
  - **不做智能分析**（Phase 8）：本 Phase 只保证 data_* 「可被正确、隔离地读」，不实现分析引擎。
  - **不做权限矩阵**（Phase 6）：本 Phase 的 `project_id` 强制是**数据层隔离**（哪个项目的数据），用户—项目—角色**鉴权**归 Phase 6。
  - **不写字段抽取**（Phase 3 已写）：data_* 只读不写。
  - **不重建行溯源底层**（Phase 4 已建）：P5-9 复用 `traces` 接口 + `document_trace_id`。
- **小功能切片**：按第 4 节 P5-1..P5-10 逐个开发，**每个测试通过后才进入下一个**。
- **数据库变更单独 commit**（M005，决策 8 两张新表，见第 5 节）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 5 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 3 data_* 写入 | ✅ | 六表写入 + `document_trace_id` + `doc_type`（P3-9 补写） |
| Phase 4 行溯源 | ✅ | `GET /traces/{result_type}/{result_id}` + `audit_field_sources` + `document_trace_id` 链路（P5-9 复用） |
| 决策 8（采购/访谈表） | ✅ 已确认 | 新增 `data_procurements`（充实字段）/ `data_interviews`（占位） |
| 决策 11（金额单位） | ✅ 已确认 | 全库金额类字段统一按「元」存储（避免阈值比对差万倍）；影响 P5-7 质量检查 |

## 2. 目标

数据工坊全功能 + 跨项目隔离：8 类表映射落地；项目级数据行查询；**强制 `project_id` 双模式**（全局浏览 vs 项目分析）；分页 + 字段筛选；大数据表批量读不超时；数据质量检查（空值/类型/金额单位）；必填字段缺失清单；数据行可溯源回 trace；跨项目隔离通过。**至此智能分析的数据前置才算就绪。**

## 3. 数据工坊核心规则（方案 §4.4 强制）

### 3.1 强制 project_id（服务层，不信任 LLM）

- 新增 `DataService`：所有 data_* 查询经此封装，**项目分析模式自动附加 `WHERE project_id=%s`**，不接受调用方（含 LLM）自由拼接该条件。
- 路由层调 `DataService`，不直接拼 data_* SQL（现状 `audit_routes.py` 直接 SQL，逐步迁移）。

### 3.2 双模式（全局浏览 vs 项目分析）

| 模式 | 触发 | project_id | 行为 |
|---|---|---|---|
| 全局浏览 | `GET /data/tables`、`/data/<table>/rows`（**显式无** project_id） | 可空 | 只读列表/统计，结果**硬 cap**（如 200 行），仅供概览 |
| 项目分析 | `GET /projects/<id>/data/*`、或带 project_id 的分析查询 | **必填** | 空 ID → 400/403（方案 §4.4「项目分析场景空 ID 失败」） |

### 3.3 表清单（8 张 = 六类 + 决策 8）

`data_contracts` / `data_finance` / `data_legal_docs` / `data_registers` / `data_credentials` / `data_general`（现状 6）+ `data_procurements`（决策 8，充实）/ `data_interviews`（决策 8，占位）。

### 3.4 智能分析只读结构化

- 智能分析（Phase 8）**只读当前 project_id 下的 data_* 表**，禁止直接读原始文件全文（方案 §4.4）。本 Phase 保证这一读通道正确、隔离、可批量。

## 4. 任务清单（P5-1 .. P5-10，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P5-1 | 8 类结构化表映射 | 6 表 + field_mapper 六别名 | 决策 8 落地：+`data_procurements`/`data_interviews`；`field_mapper` 补采购/访谈别名；白名单 8 表 |
| P5-2 | 项目数据表统计 | `/projects/<id>/data`+`/data/tables` 已通 | 8 表每表行数（项目级 + 全局级） |
| P5-3 | 项目级数据行查询 | `/data/<table>/rows?project_id=` 已通 | 按 project_id 过滤返回行 |
| P5-4 | 强制 project_id | 现状 project_id 可选（隐式） | 项目分析模式空 ID → 400/403；`DataService` 服务层强制附加 |
| P5-5 | 分页和筛选 | 分页（page/per_page cap 200）已通 | + 字段值筛选（按列等于/范围） |
| P5-6 | 大数据批量读取 | 现状 SELECT * + LIMIT/OFFSET | 字段裁剪（默认不返回 `raw_text`/`transcript` 大字段）+ 游标深翻页 + 超时保护；大表不超时 |
| P5-7 | 数据质量检查 | — | 空值率/类型异常/金额单位（决策 11，元）校验报告 |
| P5-8 | 缺失字段检查 | — | 每表必填字段缺失清单（按表定义 NOT NULL / 关键业务列） |
| P5-9 | 数据行溯源 | Phase 4 traces 接口 | 行→`document_trace_id`→trace→chunk 回查通（复用 Phase 4，不重建） |
| P5-10 | 跨项目隔离测试 | — | 全局浏览 vs 项目分析双模式正确；跨项目数据不串 |

**涉及文件**：`backend/services/data_service.py`（新增）、`backend/routes/audit_routes.py`（data 路由改造：8 表白名单/双模式/筛选/质量接口）、`backend/services/field_mapper.py`（补采购/访谈别名）、`backend/services/task_worker.py`（`_map_category_to_table` 扩采购/访谈）、`backend/data/migrations/M005_*`（决策 8 两表）。

## 5. 本 Phase DDL（M005，决策 8 两表，幂等，单独 commit）

```sql
-- ⑫ 采购数据表（决策 8 确认；采购表先充实字段）
CREATE TABLE IF NOT EXISTS tt.data_procurements (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  project_id         VARCHAR(32) NOT NULL COMMENT '关联项目ID',
  document_trace_id  INT COMMENT '溯源锚点ID',
  template_name      VARCHAR(500) COMMENT 'OntoSKU模板名',
  doc_name           VARCHAR(500) COMMENT '文档名称',
  doc_type           VARCHAR(200) COMMENT '文档类型',
  procurement_method VARCHAR(100) COMMENT '采购方式',
  subject_name       VARCHAR(500) COMMENT '采购项目名称',
  supplier           VARCHAR(500) COMMENT '供应商',
  budget_amount      DECIMAL(20,2) COMMENT '预算金额(元，决策11)',
  contract_amount    DECIMAL(20,2) COMMENT '中标/合同金额(元，决策11)',
  bid_date           DATE COMMENT '招标/开标日期',
  sign_date          DATE COMMENT '合同签订日期',
  extra_fields       JSON COMMENT '扩展字段',
  raw_text           TEXT COMMENT 'OCR原文片段',
  created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_project (project_id), INDEX idx_trace (document_trace_id)
) COMMENT '采购数据表（决策8确认）';

-- ⑬ 访谈数据表（决策 8 确认；访谈表先占位，音频转写接入后充实）
CREATE TABLE IF NOT EXISTS tt.data_interviews (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  project_id         VARCHAR(32) NOT NULL COMMENT '关联项目ID',
  document_trace_id  INT COMMENT '溯源锚点ID',
  doc_name           VARCHAR(500) COMMENT '访谈录音/转写文件名称',
  interviewee        VARCHAR(200) COMMENT '被访谈人',
  interview_date     DATE COMMENT '访谈日期',
  location           VARCHAR(200) COMMENT '访谈地点',
  transcript         LONGTEXT COMMENT '转写全文（音频转写接入后填充，决策7）',
  extra_fields       JSON COMMENT '扩展字段',
  raw_text           TEXT COMMENT '原文片段',
  created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_project (project_id), INDEX idx_trace (document_trace_id)
) COMMENT '访谈数据表（决策8确认，占位）';

-- 回滚（开发期用）
-- DROP TABLE IF EXISTS tt.data_interviews;
-- DROP TABLE IF EXISTS tt.data_procurements;
```

> 六类表现状已存在（schema.sql:127-269），M005 只建决策 8 两张新表。

## 6. 本 Phase 接口契约（完整，直接对照实现）

### 6.1 表清单与映射（P5-1）

- 8 表白名单（路由 `allowed` 集合扩到 8）；`task_worker._map_category_to_table` 增采购类→`data_procurements`；访谈类（音频转写，决策 7 本轮占位）→`data_interviews`。
- `field_mapper.FIELD_ALIAS_MAP` 补 `procurements`（procurement_method/subject_name/supplier/budget_amount/contract_amount/bid_date/sign_date）、`interviews`（interviewee/interview_date/location）别名。

### 6.2 表统计（P5-2）

- `GET /projects/<id>/data`（项目级 8 表行数）、`GET /data/tables`（全局 8 表行数）—— 现状接口，扩白名单到 8 表。

### 6.3 项目级行查询 + 强制 project_id（P5-3/P5-4）

- `GET /data/<table>/rows`：**全局浏览模式**（无 project_id，硬 cap 200）。
- `GET /projects/<project_id>/data/<table>/rows`：**项目分析模式**（project_id 路径参数，强制；经 `DataService` 附加 WHERE）。
- 现状「project_id 可选」改为显式双模式；项目分析空/伪造 project_id → 400/403。

### 6.4 分页和筛选（P5-5）

- 现状分页保留（page/per_page cap 200）；补字段筛选：`?col=value`（等于）、金额/日期范围（`?amount_min=&amount_max=`、`?date_from=&date_to=`），白名单列防注入。

### 6.5 大数据批量读取（P5-6）

- `DataService` 默认**字段裁剪**：列表查询不返回 `raw_text`/`transcript`/`ocr_content` 等大字段（按需 `?fields=` 显式取）。
- 深翻页用**游标**（`?after=<id>`，避免 LIMIT/OFFSET 深翻越慢）；查询超时保护；大表批量导出走流式（Phase 9 压测验证）。

### 6.6 数据质量检查（P5-7）

- `GET /projects/<id>/data/quality` → 每表空值率、类型异常、**金额单位**（决策 11：非「元」或异常量级告警）报告。
- `DataService.quality_check(project_id)` 实现。

### 6.7 缺失字段检查（P5-8）

- `GET /projects/<id>/data/missing` → 每表必填/关键业务列的缺失清单（按表定义 NOT NULL + 业务关键列如 `contract_amount`/`interviewee`）。

### 6.8 数据行溯源（P5-9）

- 复用 Phase 4：`GET /traces/data_row/<row_id>`（或 `?table=&row_id=`）返回行→`document_trace_id`→trace→chunk 链。data_* 行带 `document_trace_id`（Phase 3 已写），不重建。

### 6.9 跨项目隔离（P5-10）

- `DataService` 所有项目分析查询强制 project_id；全局浏览硬 cap 且不暴露明细大字段；跨项目（A 项目查 B 项目数据）被拒。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| 现状 `project_id` 可选（隐式双模式） | §3.2/§6.3 显式双模式 + 项目分析强制；`DataService` 服务层兜底 |
| 路由层直接拼 data_* SQL（现状） | 迁移到 `DataService`，project_id 强制附加，防 LLM 伪造 |
| `SELECT *` 含 `raw_text`/`transcript` 大字段 | §6.5 默认字段裁剪，按需显式取 |
| LIMIT/OFFSET 深翻页性能差 | §6.5 游标分页 |
| `field_mapper` 别名静态、不同步模板 | 本 Phase 只补采购/访谈别名；自动扩展归 Phase 7 |
| `data_interviews` 无数据源（音频转写本轮占位） | 决策 7：表先占位，转写接入后填充；P5-2 统计为 0 属正常 |
| 金额单位不一致（元/万元混存） | 决策 11 统一元；P5-7 质量检查识别异常量级 |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：Phase 3/4 完成；项目 $PID 至少一张 data_* 表有行

# P5-1/P5-2 8 表统计
curl -s "$BASE/projects/$PID/data" | python -m json.tool
curl -s "$BASE/data/tables" | python -m json.tool
# 断言：tables 含 8 张（含 data_procurements/data_interviews）

# P5-3/P5-5 项目级行查询+筛选
curl -s "$BASE/projects/$PID/data/data_contracts/rows?page=1&contract_amount_min=10000" | python -m json.tool
# 断言：返回 project_id=$PID 的行；金额筛选生效

# P5-4 强制 project_id（项目分析空 ID 失败）
curl -s "$BASE/projects//data/data_contracts/rows" | python -m json.tool
# 断言：400/403（项目分析模式 project_id 必填）

# P5-6 大数据读（字段裁剪 + 游标）
curl -s "$BASE/projects/$PID/data/data_contracts/rows?after=100" | python -m json.tool
# 断言：默认不含 raw_text 大字段；游标翻页生效

# P5-7 质量检查
curl -s "$BASE/projects/$PID/data/quality" | python -m json.tool
# 断言：返回各表空值率/类型异常/金额单位告警

# P5-8 缺失字段
curl -s "$BASE/projects/$PID/data/missing" | python -m json.tool
# 断言：返回必填字段缺失清单

# P5-9 行溯源（复用 Phase 4）
curl -s "$BASE/traces/data_row/<row_id>?table=data_contracts" | python -m json.tool
# 断言：返回 行→trace→chunk 链

# P5-10 跨项目隔离
curl -s "$BASE/projects/$PID_B/data/data_contracts/rows" -H "X-User: <A项目成员>" 
# 断言：B 项目数据不串入 A（鉴权 Phase 6 兜底；数据层 project_id 已隔离）
```

> 仿 `test_p1_flow.py` 写 `backend/tests/test_p5_data.py`，覆盖 P5-2/4/5/7/9 的断言。

## 9. 完成标准（汇总）

- [ ] 数据库 `M005` 迁移执行成功（决策 8 两表），可回滚
- [ ] 8 类表映射落地（白名单 + `field_mapper` 别名 + 分类→表）
- [ ] 强制 `project_id` 双模式：全局浏览 vs 项目分析（空 ID 失败）（P5-4）
- [ ] `DataService` 服务层强制 project_id（不信任 LLM）（P5-3/P5-4）
- [ ] 分页 + 字段筛选；大数据读字段裁剪/游标/超时保护（P5-5/P5-6）
- [ ] 数据质量检查（含金额单位 决策 11）+ 缺失字段清单（P5-7/P5-8）
- [ ] 数据行溯源回 trace（复用 Phase 4）（P5-9）
- [ ] 跨项目隔离测试通过（P5-10）
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_5.md`）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1-4 行为未破坏）
