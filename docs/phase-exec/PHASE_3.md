# PHASE_3 执行包：OCR 基础链路

> **执行协议**：本文件是 Phase 3 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 1（流程控制）+ Phase 2（资料空间：upload 落年度/分类前缀 + manifest + trace 空间列）已完成。
> 铁律：不破坏 Phase 1/2 已验收行为；OntoSKU 探活失败不开工（K2 契约已固化）；不越界做溯源（Phase 4）。

---

## 0. 执行者须知（先读）

- **关键认知：解析链路已端到端打通并真实运行**（非占位）：
  - `task_worker.py` 进程内线程池（max_workers=5）真在跑，按 task_type 分派；
  - 主引擎 OntoSKU（`ontosku_client.py` 完整 presigned-URL 流程对 `192.168.3.189:5005`），失败降级 LiteParse；
  - 分类（关键词 `_classify_for_table`）+ 抽取（OntoSKU `fields` + `field_mapper`）+ 写 data_*（`_insert_into_data_table`）已通；
  - `recover_stuck_tasks()`（重启恢复）、`reparse`（重新解析）已存在。
  - **本 Phase 是「加固 / 对齐 / 补齐」已通链路，不是从零建。** 每个任务的「现状基础」见 §4/§6。
- **只做本 Phase 的事**：保证「解析正确」——上传→trace→OCR→Markdown→分类→字段→data_* 行 + 任务可靠（重试/恢复/重解析）。
  - **不做溯源**（Phase 4）：不建 `audit_document_chunks`、不建 `audit_field_sources`、不做字段匹配 chunk、不做数据行→trace 溯源展示。OntoSKU 返回的 chunks 本 Phase **拿到但不落库**（Phase 4 逐条落）。
  - **不做数据工坊查询**（Phase 5）：data_* 表只写不查；采购/访谈新表（决策 8）归 Phase 5。
  - **不做别名表自动扩展**（Phase 7）：`field_mapper` 保持静态，落 `extra_fields` 的字段原样保留。
  - **不碰智能分析**（Phase 8）。
- **小功能切片**：按第 4 节 P3-1..P3-12 逐个开发，**每个测试通过后才进入下一个**。
- **数据库变更单独 commit**（M003，见第 5 节）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 3 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 1 流程控制 | ✅ | `setup_stage=workspace` + `allowed_actions` 含 `upload` 才允许上传 |
| Phase 2 资料空间 | ✅ | upload 已落年度/分类前缀 + 写 manifest + trace 空间列；task payload 的 `minio_bucket`/`minio_path` 已是 Phase 2 新值，`task_worker` 透传使用 |
| K2 OntoSKU 契约快照 | ✅ | [06-ontosku-api-snapshot.md](../dev-specs/06-ontosku-api-snapshot.md)：`POST /v1/jobs`→轮询→`documents/{id}`+`/chunks`；产物 `full.md`/`sku_results.json`/`chunks.json`；`document_id`/`job_id` 落 trace |
| OntoSKU 探活（192.168.3.189:5005） | ⚠️ 联调前必备 | K2 样例已固化；真实 `chunks.json` 原始结构待补（快照 §4，**不阻塞 Phase 3 开工**，联调时校准） |
| 决策 9（文档语义检索） | ⚠️ 待勾选，按建议 | 本轮只走结构化+关键词，向量检索入 backlog |
| 决策 10（LLM 故障降级） | ⚠️ 待勾选，按建议 | 允许纯规则/LLM 降级，结果强制标记 `parse_engine`，不混入正式结论 |

## 2. 目标

保证「解析正确」：任意上传文件 → trace → OCR 完成 → Markdown 落库 → 文档分类 → 字段提取 → 写入对应 data_* 表（有行）。引擎策略统一（OntoSKU 主 / LiteParse 降级 / LLM 兜底，全程标记 `parse_engine`）；任务失败可重试（上限 3 + 退避）；进程重启可恢复卡住任务；支持重新解析（`ocr_version+1`）。

**本 Phase 不保证「可溯源」**——字段→chunk→原文的溯源链归 Phase 4。

## 3. 解析链路核心规则

### 3.1 引擎策略（OntoSKU 主 / LiteParse 降级 / LLM 兜底）

```
上传(workspace 阶段) → trace(解析列 pending) → OCR 任务入队
  → OcrOrchestrator.parse():
       ① OntoSKU（主）  192.168.3.189:5005，presigned-URL 全流程
       ② OntoSKU 失败 → LiteParse 降级（127.0.0.1:5006，仅原生数字 PDF）
       ③ LiteParse 失败/空文本 → 本地 LLM 抽取兜底（call_llm_json）
  → 全程写 trace.parse_engine ∈ {ontosku, liteparse, local-llm}
  → 产物：full.md → ocr_content；fields → field_mapper → data_*；document_id/job_id → trace
```

要点：
- **选路统一（已定）**：现状 `OCREngine.get_engine()` 默认 liteparse、worker 却 OntoSKU 优先直连，存在选路脱节。**决定**：以 `task_worker._run_ocr_task` 为**唯一选路点**（OntoSKU 主→LiteParse 降级→LLM 兜底），`OCREngine` 保留为"取客户端"并在注释标注不再承担选路，**不新建 `OcrOrchestrator`**。属加固性质，**放在 P3-12 末尾做**（链路稳定后再收口，避免重构引入回归）。
- LiteParse 降级**显式用 LiteParseClient**（避免 `OCR_ENGINE=mineru` 时回调 OntoSKU 死循环——现状已正确，保留）。
- LLM 兜底结果强制 `parse_engine=local-llm`（决策 10），不混充 OntoSKU 结论。

### 3.2 任务状态机（现状已完备，本 Phase 加固）

```
pending → processing → completed
              ↘ failed（retry_count < max_retries → 回 pending；否则终止）
              ↘ cancelled
```

- 表：`tt.audit_task_queue`（`migrate_task_queue.sql`，**非** schema.sql）。`audit_task_operations` 表**不存在**（历史遗留命名），本 Phase **不建**（监控/流水归 Phase 6）。
- worker：Flask 进程内 `ThreadPoolExecutor(max_workers=5)`；进程被杀则 in-flight 任务丢失，靠 `recover_stuck_tasks()` 下次启动恢复。
- P3-11 `recover_stuck_tasks()`：重启时 `processing→pending`（现状已做，方案 P3-11 要求，**本 Phase 唯一必做项**）。超时回收（`processing` 僵死自动回收）**挪到 Phase 6**（运行保障范畴，阈值需借 Phase 6 任务监控看板的 OntoSKU 真实耗时数据联调定）。

### 3.3 解析产物落点

| OntoSKU 产物 | 落点 | Phase |
|---|---|---|
| `full.md` | `audit_document_traces.ocr_content` | P3-6（本 Phase） |
| `document_id` / `job_id` | `trace.external_document_id` / `external_job_id` | P3-3（本 Phase 填，Phase 4 溯源用） |
| `sku_results.json` 的 `fields` | `field_mapper.map_extracted_fields()` → data_* 列 + `extra_fields` | P3-8/P3-9（本 Phase） |
| `chunks.json` | **本 Phase 不落库**；Phase 4 逐条落 `audit_document_chunks` | Phase 4 |
| 模板匹配 `audit/历史档案类/卷宗` | `trace.ontosku_template` + data_*.doc_type | P3-7（本 Phase） |

### 3.4 字段映射边界（与 Phase 4/7 划清）

- `field_mapper.FIELD_ALIAS_MAP`（六类静态别名表）**本 Phase 不改**：命中别名的进 data_* 列，未命中的进 `extra_fields` JSON 原样保留。
- `extra_fields` 字段的**溯源**（`extra_fields->'$.字段名'`）归 Phase 4；别名表**按模板自动扩展**归 Phase 7。

## 4. 任务清单（P3-1 .. P3-12，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P3-1 | 上传文件并生成 trace | upload+trace 已通（Phase 2 改前缀/manifest） | trace 落库含解析列（pending）；OCR 任务正确触发（Phase 2 框架上） |
| P3-2 | 创建 OCR 任务 | create_task+submit_task 已通 | task 参数走独立 `payload`（不再与 `result` 双向复用）；异步入队 |
| P3-3 | OntoSKU 调用 | ontosku_client 全流程已通 | 真实解析（K2 契约）；`document_id`/`job_id`/`parse_engine=ontosku` 落 trace |
| P3-4 | LiteParse 降级 | _fallback_local_extract 已通 | OntoSKU 失败自动降级 LiteParse，标 `parse_engine=liteparse`；再失败 LLM 兜底标 `local-llm` |
| P3-5 | OCR 状态更新 | task_manager 状态机已完备 | `parse_status` pending/running/done/failed 随任务状态同步落 trace |
| P3-6 | Markdown 结果落库 | worker 落 ocr_content 已通 | `ocr_content` 持久化 full.md；可被 doc-viewer 读取 |
| P3-7 | 文档分类 | _classify_for_table 关键词已通 | 模板匹配落 `trace.ontosku_template` + data_*.`doc_type`（现状从不写 doc_type，补写） |
| P3-8 | 字段提取 | OntoSKU fields + field_mapper 已通 | 中文字段抽取；命中别名列、未命中进 extra_fields（原样保留） |
| P3-9 | 写入对应 data_* 表 | _map_category_to_table + _insert_into_data_table 已通 | 分类→表→行；行含 `document_trace_id` + `doc_type`；六表之一有行 |
| P3-10 | 失败重试 | fail_task（retry<max 回 pending）已有 | 上限 3 次 + 指数退避；超限标 failed 终止 |
| P3-11 | 任务恢复 | recover_stuck_tasks（重启 processing→pending）已有 | 重启恢复（方案 P3-11 必做）；超时回收挪 Phase 6 |
| P3-12 | 重新解析 | reparse 接口已有 | 触发重新 OCR+提取；`ocr_version+1`；旧版本保留（Phase 4 标 superseded） |

**涉及文件**：`backend/services/task_worker.py`（编排重构/分类落库/重试退避/超时回收）、`backend/services/task_manager.py`（payload/状态）、`backend/services/ontosku_client.py`、`backend/services/ocr_client.py`、`backend/services/extraction_service.py`、`backend/services/field_mapper.py`（不改别名，仅确认）、`backend/routes/audit_routes.py`（upload/reparse）、`backend/data/migrations/M003_*`（trace 解析列 + task payload 列）。

## 5. 本 Phase DDL（M003，幂等，单独 commit）

```sql
-- ① audit_document_traces 解析技术标识列（方案 DDL ④，Phase 3）
--    幂等：存储过程判列存在再 ADD。M002 的空间管理列与本批解析列互不冲突。
ALTER TABLE tt.audit_document_traces
  ADD COLUMN external_document_id VARCHAR(100) NULL COMMENT 'OntoSKU document_id',
  ADD COLUMN external_job_id      VARCHAR(100) NULL COMMENT 'OntoSKU job_id',
  ADD COLUMN parse_engine         VARCHAR(50)  NULL COMMENT 'ontosku/liteparse/local-llm',
  ADD COLUMN parse_status         VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT 'pending/running/done/failed',
  ADD COLUMN parsed_at            DATETIME     NULL COMMENT '解析完成时间',
  ADD INDEX idx_parse_status (parse_status),
  ADD INDEX idx_external_doc (external_document_id);

-- ② audit_task_queue payload 列（已确认加列；方案 DDL ④ 之外的增量，经确认纳入 M003）
--    现状 task_queue.result 双向复用（入参+结果混用）；加 payload 列分离输入与结果。
ALTER TABLE tt.audit_task_queue
  ADD COLUMN payload JSON NULL COMMENT '任务输入参数（trace_id/minio_bucket/minio_path/sku_profile 等）';

-- 回滚（单独文件，开发期用）
-- ALTER TABLE tt.audit_document_traces
--   DROP INDEX idx_external_doc, DROP INDEX idx_parse_status,
--   DROP COLUMN parsed_at, DROP COLUMN parse_status,
--   DROP COLUMN parse_engine, DROP COLUMN external_job_id, DROP COLUMN external_document_id;
-- ALTER TABLE tt.audit_task_queue DROP COLUMN payload;
```

> **不建表**：`audit_document_chunks`（Phase 4）、`audit_field_sources`/`audit_source_refs`（Phase 4）、`audit_task_operations`（Phase 6）均不在本 Phase。

## 6. 本 Phase 接口契约（完整，直接对照实现）

**统一约定**：OCR 类操作均要求项目 `setup_stage=workspace`；task 参数走 `payload` 列（P3-2）；解析状态/引擎全程落 trace。

### 6.1 `POST /api/audit/projects/{id}/upload`（P3-1，Phase 2 框架上叠加）

- Phase 2 已完成：落年度/分类前缀 + manifest + trace 空间列 + 删二次建桶。
- **本 Phase 叠加**：建 trace 时初始化解析列（`parse_status='pending'`、`parse_engine=NULL`）；create_task 的参数走 `payload` 列（不再塞 `result`）；OCR 触发链路不变。
- 响应不变（`{success, file_id, trace_id, task_id, ocr_status:'pending'}`）。

### 6.2 任务参数规范化（P3-2）

- `task_queue.result` 现状双向复用（入参+结果混用）。**已确认加 `payload` 列**（M003 ②）：`payload` 存入参（trace_id/minio_bucket/minio_path/filename/project_id/sku_profile），`result` 只存最终结果（成功/失败摘要 + 耗时 + 产物指针）。
- `task_worker` 从 `payload` 读入参。

### 6.3 OntoSKU 调用（P3-3，现状加固）

- `ontosku_client.get_client().extract()` 全流程不变（POST /v1/jobs → PUT upload → confirm → 轮询 → 下载 ZIP → 归一化 `{document_id, markdown, fields, chunks}`）。
- 成功后落 trace：`external_document_id`/`external_job_id`/`parse_engine='ontosku'`。
- **选路统一（已定，P3-12 末尾做）**：以 `_run_ocr_task` 为唯一选路点，`OCREngine` 降为"取客户端"，**不新建 `OcrOrchestrator`**（见 §3.1）；放链路稳定后做，避免回归。硬指标：OntoSKU 主→LiteParse 降级→LLM 兜底，全程标 `parse_engine`。

### 6.4 LiteParse / LLM 降级（P3-4）

- OntoSKU 失败 → LiteParse（`LiteParseClient`，显式避免 mineru 回调死循环）→ 标 `parse_engine='liteparse'`。
- LiteParse 失败或空文本（扫描件）→ 本地 LLM 抽取兜底（`extraction_service` + `call_llm_json`）→ 标 `parse_engine='local-llm'`（决策 10）。
- 三档引擎产物统一归一化为 `{markdown, fields}` 后走同一落库路径。

### 6.5 状态更新（P3-5）

- `parse_status` 随任务状态同步：`pending`（入队）→`running`（worker 取走）→`done`/`failed`（完成/终态）；`parsed_at` 在 `done` 时写入。

### 6.6 Markdown 落库（P3-6）

- `full.md` → `trace.ocr_content`（LONGTEXT）；doc-viewer 经 `/api/ocr/md` 反向代理可读（现状已通）。

### 6.7 文档分类（P3-7）

- 主路径：OntoSKU 服务端模板匹配（`ontosku_template`，如 `audit/历史档案类/卷宗`）+ 关键词 `_classify_for_table` 兜底。
- **落 trace.ontosku_template + data_*.doc_type**（现状 `doc_type` 从不写，本 Phase 补）。
- LLM 分类仅降级路径用，结果同样落 trace。

### 6.8 字段提取（P3-8）

- OntoSKU `fields` → `field_mapper.map_extracted_fields()` → `(row_dict, extra_fields)`；命中别名列、未命中进 `extra_fields`（原样保留，不溯源——Phase 4）。
- `field_mapper.enrich_fields_from_text`（regex 补抽）保留。

### 6.9 写 data_* 表（P3-9）

- `task_worker._map_category_to_table`（多对一：18 类→6 表）+ `_insert_into_data_table` 现状已通。
- **补写 `doc_type`**；行含 `project_id`/`document_trace_id`/`template_name`/`doc_name`/`doc_type`/`extra_fields`/`raw_text` + 命中映射列。
- 六表：`data_contracts`/`data_finance`/`data_legal_docs`/`data_registers`/`data_credentials`/`data_general`。

### 6.10 失败重试（P3-10）

- `fail_task` 现状：`retry_count < max_retries` → 回 `pending`。本 Phase 设 `max_retries=3` + **指数退避**（重试前延迟，避免雪崩）；超限 → `failed` 终态 + trace.`parse_status='failed'`。

### 6.11 任务恢复（P3-11）

- `recover_stuck_tasks()` 现状：启动时 `processing→pending`（方案 P3-11 要求，**本 Phase 唯一必做项**）。
- 超时回收（`processing` 僵死→回 `pending`）**挪到 Phase 6**（P6-8 任务监控保障范畴）：阈值需借 Phase 6 监控看板的 OntoSKU 真实耗时数据联调定，本轮不实现。

### 6.12 重新解析（P3-12）

- `POST /api/audit/documents/reparse`（现状已通）：触发同 trace 重新 OCR+提取；`ocr_version+1`；旧 `ocr_content`/extracted_fields 保留（Phase 4 P4-10 标旧证据 superseded，本 Phase 只保版本号）。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| `OCREngine` 抽象默认 liteparse、worker 却 OntoSKU 优先直连（脱节） | P3-12 末尾以 `_run_ocr_task` 为唯一选路点统一，`OCREngine` 降为"取客户端"，不新建类 |
| `task_queue.result` 双向复用（入参+结果混用） | 已确认加 `payload` 列分离（M003 ②，方案外增量经确认纳入） |
| worker 进程内线程池，进程被杀丢任务 | 保持进程内（独立 worker 进程/Redis 超 Phase 3 范围）；§6.11 `recover_stuck_tasks` + 超时回收兜底 |
| 分类/抽取双轨（主路径关键词+OntoSKU 字段；降级 LLM）行为不一致 | §3.1 统一编排，产物归一化后走同一落库路径；分类结果落 trace |
| `data_*.doc_type` 现状从不写 | §6.9 补写；分类落 `ontosku_template` + `doc_type` |
| LiteParse 只对原生数字 PDF 有效（扫描件空文本） | §6.4 三档降级，扫描件走 LLM 兜底标 `local-llm` |
| `field_mapper` 别名静态硬编码（缺口率 59-85%） | 本 Phase 不改（Phase 7 按模板自动扩展）；未命中字段进 `extra_fields` 原样保留 |
| `extra_fields` 字段无法溯源 | 本 Phase 不处理（Phase 4 建 `extra_fields->'$.字段名'` 来源） |
| OntoSKU 真实 `chunks.json` 结构未核对 | 快照 §4 待补；按 `ontosku_client.py` 现有解析实现，联调时校准（不阻塞开工） |
| chunks 已在 trace.`position_anchor`/前端在读 | 本 Phase 不动 chunks 落库（Phase 4 双写兼容） |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：已 finalize 的 workspace 项目 $PID；OntoSKU 192.168.3.189:5005 可达；LiteParse 127.0.0.1:5006 可达

# P3-1/P3-2 上传+trace+入队（payload 列）
curl -s -X POST $BASE/projects/$PID/upload -F "file=@采购合同.pdf" | python -m json.tool
# 断言：返回 trace_id+task_id；trace.parse_status=pending；task.payload 非空、result 为空（未完成）

# P3-3/P3-5/P3-6 OntoSKU 解析→Markdown 落库（轮询 task 到 completed）
TASK=<上一步 task_id>
# 轮询：GET $BASE/tasks/$TASK 直到 status=completed
# 断言：trace.parse_engine=ontosku；trace.external_document_id 非空；trace.ocr_content 非空（含 Markdown）；parse_status=done；parsed_at 非空

# P3-4 降级（OntoSKU 不可达时）：构造 OntoSKU 失败 → 期望 parse_engine=liteparse（或 local-llm）
# 断言：trace.parse_engine ∈ {liteparse, local-llm}；ocr_content 非空或 parse_status=failed（重试耗尽）

# P3-7/P3-8/P3-9 分类→字段→data_* 有行
curl -s "$BASE/projects/$PID/files" | python -m json.tool   # 看分类
# 断言：trace.ontosku_template 非空；某 data_* 表 SELECT WHERE document_trace_id=$TRACE 有行；行 doc_type 非空

# P3-10 失败重试：构造解析失败 → 期望 retry_count 递增、指数退避、上限 3 后 failed

# P3-11 任务恢复：上传后立即杀 backend 进程（processing 中）→ 重启 → 期望该任务回 pending 并重新跑完
# 断言：重启后 recover_stuck_tasks 把 processing→pending；最终 completed

# P3-12 重新解析
curl -s -X POST $BASE/documents/reparse -H "Content-Type: application/json" \
  -d '{"document_id":$TRACE}' | python -m json.tool
# 断言：ocr_version 递增（旧+1）；新 ocr_content 落库；旧版本保留
```

> 仿 `test_p1_flow.py` 写 `backend/tests/test_p3_ocr.py`，覆盖 P3-1/3/5/6/7/9/12 的断言；P3-4/10/11 需构造故障/重启，可手工 + 日志核验。

## 9. 完成标准（汇总）

- [ ] 数据库 `M003` 迁移执行成功，可回滚
- [ ] 上传 → trace → OCR（OntoSKU）→ Markdown 落库 → data_* 表有行（端到端）
- [ ] OntoSKU 失败自动降级 LiteParse / LLM，全程 `parse_engine` 标记正确
- [ ] `parse_status` 随任务状态同步（pending/running/done/failed）
- [ ] 失败重试上限 3 + 退避；超限终止
- [ ] 进程重启恢复卡住任务（processing→pending）（超时回收挪 Phase 6）
- [ ] 重新解析 `ocr_version+1`，旧版本保留
- [ ] 引擎选路统一（`_run_ocr_task` 唯一选路点，`OCREngine` 降为取客户端，P3-12 末尾做）；task payload/result 分离（加 `payload` 列，M003 ②）
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_3.md`）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1/2 行为未破坏）
