# PHASE_4 执行包：文档和字段溯源

> **执行协议**：本文件是 Phase 4 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 3（解析）已完成——`ocr_content`/`external_document_id`/`external_job_id`/`parse_engine`/`ocr_version` 已落 trace，OntoSKU chunks 已拿到但未落库。
> 铁律：不破坏 Phase 1/2/3 已验收行为；`position_anchor` 旧数据双写兼容；无页码不伪造。

---

## 0. 执行者须知（先读）

- **只做本 Phase 的事**：保证「可追踪」——任取一个结构化字段，可定位 data_* 行 → 字段来源 → chunk → 原始文件页码与原文；重解析后旧证据失效。
  - **不做智能分析**（Phase 8）：`audit_source_refs.result_type` 的 `analysis_hit`/`suspicion`/`law_recommendation` 类引用由 AI 推荐产生，归 Phase 7/8 写入；本 Phase 只**建表 + EvidenceService 基础 + 结构化数据→文档 chunk 的引用**。
  - **不做数据工坊查询**（Phase 5）：data_* 只被溯源读取，不做筛选/分页/批量查询接口。
  - **不做事项推荐关联**（`audit_item_refs`，Phase 1/7）、不做引擎规则（`audit_engine_rules`，Phase 7）、不做步骤总结（`audit_step_summaries`，Phase 8）。
- **小功能切片**：按第 4 节 P4-1..P4-10 逐个开发，**每个测试通过后才进入下一个**。
- **数据库变更单独 commit**（M004，三张新表，见第 5 节）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 4 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 3 解析产物 | ✅ | `trace.external_document_id`/`external_job_id`/`parse_engine`/`parse_status`/`ocr_version`/`ocr_content` 已落；OntoSKU `chunks` 已在 `OcrOrchestrator` 产物中（未落库） |
| Phase 3 data_* 写入 | ✅ | data_* 行已含 `document_trace_id`（现状 worker 已写），P4-6 在此基础上确保可溯源 |
| K2 OntoSKU 契约 | ✅ | `chunks.json` → `audit_document_chunks` 逐条落库（快照 §3） |
| K2 chunks.json 原始结构 | ⚠️ 待补 | 影响 P4-5 字段→chunk 匹配精度；按 `ontosku_client.py` 现有 `{chunk_id,page_nums,bbox,type,text}` 解析实现，**联调时校准**，不阻塞开工 |
| K5 字段缺口 | ✅ | 别名命中率 59-85%，未映射字段进 `extra_fields`；P4-5 用 `extra_fields->$.字段名` 覆盖溯源 |
| 决策 6（待人工核实） | ⚠️ 待勾选，按建议 | 无精确条款/页码的来源标记「待人工核实」，禁止进入最终文书；影响 P4-4/P4-5 降级展示 |

## 2. 目标

保证「可追踪」：结构化字段 → data_* 行 → 字段来源（field_source）→ 文档切片（chunk）→ 原始文件页码与原文，五层可双向查询。重解析（`ocr_version+1`）后旧版本证据标记失效（`superseded`），下游标记待复核。降级路径（LiteParse/LLM）无页码时「不伪造」，标待人工核实。

## 3. 溯源核心规则

### 3.1 溯源层级（统一证据契约，方案 §4.3）

```
原始文件(MinIO) → 文档切片(audit_document_chunks) → 结构化字段(audit_field_sources)
                                                          ↓ data_* 行(document_trace_id)
                                            统一证据引用(audit_source_refs) → [Phase 8 AI 结论]
```

- **字段→chunk**：`audit_field_sources`（每个 data_* 字段绑定支撑它的 chunk，含 `extra_fields` 字段）。
- **结论→证据**：`audit_source_refs`（任何 result 统一引用 source；本 Phase 落「结构化数据→文档」类，AI 结论类留 Phase 8）。
- **行→trace**：data_* 行的 `document_trace_id` 打通到文档（现状已有列）。

### 3.2 chunk 落库与双写兼容

- OntoSKU 产物 `chunks`（`{chunk_id, page_nums, bbox, type, text}`）→ 归一化 → `audit_document_chunks` 逐条落库（P4-2/P4-3）。
- **现状 chunks 写在 `trace.position_anchor` JSON 且前端在读**（K1/Phase 4 已知坑）→ 新增 chunk 行时**双写**（`position_anchor` 继续写 + `audit_document_chunks` 落库），灰度切换前端后再移除 `position_anchor`，不破坏现状。

### 3.3 重解析失效（ocr_version 机制）

- Phase 3 重新解析已 `ocr_version+1`。P4-10：新版本解析后，把旧 `ocr_version` 的 `audit_document_chunks.status` 标 `superseded`（方案自带 status 列）。**失效靠 status + ocr_version 推导，不另设状态列**——`audit_field_sources` 取最新 `ocr_version`、`audit_source_refs` 的 document_chunk 类 source join `chunk.status`，superseded 即判定过期，查询时标「待复核」（不删行，留痕，决策 6 精神）。
- 下游（Phase 8 AI 结论）查询溯源链时发现引用了 superseded chunk → 标记待人工核实。

### 3.4 无页码不伪造（降级路径）

- LiteParse/LLM 降级（`parse_engine ∈ {liteparse, local-llm}`）无 `bbox`/`page_nums`（K2 §4）→ chunk 的 `page_nums`/`bbox` 留空，溯源展示「无精确页码，待人工核实」，**不伪造**页码（决策 6）。

## 4. 任务清单（P4-1 .. P4-10，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P4-1 | OntoSKU document/job ID 落库 | Phase 3 已填 trace | `external_document_id`/`external_job_id` 稳定非空（OntoSKU 路径），可作溯源锚点 |
| P4-2 | chunk 归一化 | OntoSKU 产物含 chunks | 各引擎 chunks 统一为最小字段（chunk_id/type/page_nums/bbox/text/section_path） |
| P4-3 | chunk 逐条落库 | 现状写 position_anchor JSON | `audit_document_chunks` 有行；**双写** position_anchor 兼容 |
| P4-4 | 页码和 bbox 保存 | — | `page_nums`(JSON)/`bbox`(JSON) 落库；降级路径留空不伪造 |
| P4-5 | 字段匹配 chunk | 现状无 | `audit_field_sources` 行（含 `extra_fields->$.字段名`）；优先用 OntoSKU 字段溯源，否则文本/位置匹配 |
| P4-6 | 数据行关联文档 trace | data_*.document_trace_id 已有 | 行→trace→文档链路可查；trace 缺失行有告警 |
| P4-7 | 统一证据引用 | — | `audit_source_refs` + `EvidenceService`（写/按 result 查）；落「结构化数据→文档」类引用 |
| P4-8 | trace 查询接口 | — | `GET /api/audit/traces/{result_type}/{result_id}` 返回完整溯源链 |
| P4-9 | 前端真实溯源展示 | 现状溯源按钮占位 | 溯源按钮接 P4-8 真实展示（字段→chunk→原文+页码）；无页码标待核实 |
| P4-10 | 重新解析后旧证据失效 | Phase 3 ocr_version+1 | 旧 chunks 标 `superseded`；field_sources 按版本、source_refs 按 chunk.status 推导过期（查询时标待复核，留痕不删） |

**涉及文件**：`backend/services/evidence_service.py`（新增）、`backend/services/task_worker.py`（chunk 归一化/落库/字段匹配/失效）、`backend/services/ontosku_client.py`（chunks 归一化确认）、`backend/routes/audit_routes.py`（trace 查询接口）、`frontend/js/analysis-wiz.js`/`knowledge.js` 等（溯源展示）、`backend/data/migrations/M004_*`（三张新表）。

## 5. 本 Phase DDL（M004，三张新表，幂等，单独 commit）

```sql
-- ⑤ 文档切片表（方案 DDL ⑤，Phase 4）
CREATE TABLE IF NOT EXISTS tt.audit_document_chunks (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  trace_id      INT NOT NULL COMMENT '关联 audit_document_traces',
  project_id    VARCHAR(32) NOT NULL,
  chunk_id      VARCHAR(100) COMMENT 'OntoSKU chunk_id',
  chunk_type    VARCHAR(20) COMMENT 'text/image/table/page',
  page_nums     JSON COMMENT '页码列表',
  bbox          JSON COMMENT '坐标 [x0,y0,x1,y1]',
  text          LONGTEXT COMMENT '切片原文',
  section_path  VARCHAR(500) COMMENT '章节路径',
  ocr_version   INT DEFAULT 1 COMMENT '所属解析版本（重解析+1）',
  status        VARCHAR(20) DEFAULT 'active' COMMENT 'active/superseded',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_trace (trace_id), INDEX idx_project (project_id), INDEX idx_status (status)
) COMMENT '文档切片—可逐页逐段查询';

-- ⑥ 统一证据引用表（方案 DDL ⑥，Phase 4；AI 结论类引用 Phase 8 写）
CREATE TABLE IF NOT EXISTS tt.audit_source_refs (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  project_id    VARCHAR(32) NOT NULL,
  result_type   VARCHAR(30) COMMENT 'audit_item/law_recommendation/analysis_hit/suspicion/document/data_row',
  result_id     VARCHAR(64) NOT NULL,
  source_type   VARCHAR(30) COMMENT 'document_chunk/data_row/law_clause/violation/case',
  source_id     VARCHAR(64) NOT NULL,
  document_id   INT COMMENT '来源文档 trace_id（如适用）',
  file_name     VARCHAR(500),
  page_number   INT,
  bbox          JSON,
  quote         TEXT COMMENT '支撑结论的原文片段',
  relation      VARCHAR(20) DEFAULT 'supports' COMMENT 'supports/contradicts/derived_from',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_result (result_type, result_id),
  INDEX idx_project (project_id)
) COMMENT '结论证据引用—统一溯源';

-- ⑦ 字段级溯源表（方案 DDL ⑦，Phase 4）
CREATE TABLE IF NOT EXISTS tt.audit_field_sources (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  project_id    VARCHAR(32) NOT NULL,
  table_name    VARCHAR(100) COMMENT 'data_* 表名',
  row_id        INT NOT NULL,
  field_name    VARCHAR(100) NOT NULL COMMENT '列名 或 extra_fields->$.字段名',
  chunk_id      INT COMMENT '关联 audit_document_chunks.id',
  ocr_version   INT COMMENT '所属解析版本',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_row (table_name, row_id), INDEX idx_chunk (chunk_id)
) COMMENT '结构化字段来源';

-- 回滚（开发期用）
-- DROP TABLE IF EXISTS tt.audit_field_sources;
-- DROP TABLE IF EXISTS tt.audit_source_refs;
-- DROP TABLE IF EXISTS tt.audit_document_chunks;
```

> ⑥/⑦ **完全对齐方案原 DDL**（不额外加状态列）。旧证据失效靠 `audit_document_chunks.status`（方案自带 active/superseded）+ 各表 `ocr_version` 推导——field_sources 取最新版本、source_refs join chunk.status，见 §3.3/P4-10。

## 6. 本 Phase 接口契约（完整，直接对照实现）

### 6.1 OntoSKU ID 稳定（P4-1）

- Phase 3 已填 `trace.external_document_id`/`external_job_id`。本 Phase 确保重解析时**保留旧 ID 或记新 ID**（不丢锚点）；溯源查询以 `external_document_id` 为 OntoSKU 侧稳定键。

### 6.2 chunk 归一化（P4-2）

- `OcrOrchestrator` 产物的 `chunks`（OntoSKU `{chunk_id,page_nums,bbox,type,text}`）归一化为统一最小字段；LiteParse/LLM 路径 `page_nums`/`bbox` 留空（不伪造）。联调时用真实 `chunks.json` 校准字段名（K2 §4）。

### 6.3 chunk 逐条落库 + 双写（P4-3/P4-4）

- `task_worker` 解析成功后把 chunks 逐条写 `audit_document_chunks`（带 `page_nums`/`bbox`/`ocr_version`/`status='active'`）。
- **同时继续写 `trace.position_anchor`**（现状前端在读），双写至前端切换完成。
- 降级路径 chunk 的 `page_nums`/`bbox` 为 NULL。

### 6.4 字段匹配 chunk（P4-5）

- data_* 字段写库后，为每个字段建 `audit_field_sources` 行：`field_name`（列名或 `extra_fields->$.字段名`）→ `chunk_id`。
- 匹配优先级：① OntoSKU 字段级溯源 🔍（若 chunks.json 提供 field→chunk 映射）；② 文本相似度/位置匹配兜底。无法匹配的 `chunk_id=NULL` + 标记。
- **覆盖 extra_fields 字段**（K5 对策）：未映射字段同样建来源，用 `extra_fields->$.字段名` 表达式。

### 6.5 数据行关联 trace（P4-6）

- data_* 行的 `document_trace_id` 现状已写（worker）。本 Phase 确保所有 data_* 写入路径都带 `document_trace_id`；缺失行（孤儿数据）有告警不静默。

### 6.6 统一证据引用 + EvidenceService（P4-7）

- 新增 `backend/services/evidence_service.py`：
  - `add_ref(project_id, result_type, result_id, source_type, source_id, quote, page_number, ...)` 写 `audit_source_refs`；
  - `get_refs(result_type, result_id)` 按结果查证据；
  - `link_data_row_to_document(table, row_id, trace_id, chunk_id, quote)` 落「结构化数据→文档 chunk」引用（P4-6/P4-7 衔接）。
- 本 Phase 落 `result_type ∈ {document, data_row}`、`source_type ∈ {document_chunk, data_row}` 的引用；`analysis_hit`/`suspicion`/`law_recommendation` 类由 Phase 7/8 写。

### 6.7 trace 查询接口（P4-8）

```
GET /api/audit/traces/{result_type}/{result_id}
→ { success, refs:[{source_type, source_id, file_name, page_number, bbox, quote}],
    field_sources:[{table_name, row_id, field_name, chunk:{text, page_nums, bbox}}] }
```
- 聚合 `audit_source_refs` + `audit_field_sources` + `audit_document_chunks`，返回完整溯源链。

### 6.8 前端溯源展示（P4-9）

- 现状溯源按钮（占位）接 P4-8：点字段/结论 → 弹溯源链（原文片段 + 页码 + 文件名）；无页码（降级路径）或证据已过期（chunk.status=superseded）→ 标「待人工核实」（决策 6）。
- 涉及 `frontend/js/analysis-wiz.js`、`knowledge.js` 等；本 Phase 以「接口可用 + 至少一处真实展示」为准，全量铺开可后续。

### 6.9 重解析旧证据失效（P4-10）

- 重新解析（`reparse`，ocr_version+1）成功后：
  - 旧 `ocr_version` 的 `audit_document_chunks.status → 'superseded'`；
  - **不批量改写** field_sources/source_refs 状态——查询溯源链（P4-8）时动态推导：field_sources 取最新 `ocr_version`；source_refs 的 document_chunk 类 source join `chunk.status`，superseded 则标「证据已过期，待复核」（不删行，留痕）。
  - 下游引用了过期证据的 AI 结论（Phase 8）查询时感知并标记待人工核实。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| chunks 现写在 `trace.position_anchor` JSON 且前端在读 | P4-3 双写（position_anchor + audit_document_chunks），灰度切换后移除 |
| 别名命中率 59-85%，大量字段在 `extra_fields` | P4-5 `field_name` 用 `extra_fields->$.字段名` 覆盖溯源（K5 对策） |
| OntoSKU 真实 `chunks.json` 结构未核对 | P4-2/P4-5 按 `ontosku_client.py` 现有解析实现，联调校准（不阻塞） |
| LiteParse/LLM 降级无 bbox/page_nums | P4-4 留空不伪造；展示「待人工核实」（决策 6） |
| `audit_source_refs` 的 AI 结论类型本 Phase 无数据源 | 建表 + EvidenceService 基础；AI 引用 Phase 8 写，不越界 |
| 字段→chunk 匹配精度依赖 OntoSKU 字段溯源 | 优先用 OntoSKU 🔍；兜底文本/位置匹配；无法匹配标 chunk_id=NULL |
| 重解析后下游引用未更新 | P4-10 标 chunk.status=superseded 留痕，溯源查询推导「待复核」，Phase 8 下游感知 |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：Phase 3 已完成；某 data_* 行 $ROW（document_trace_id=$TRACE）已存在

# P4-1 ID 稳定
# 断言：trace.external_document_id 非空（OntoSKU 路径）；重解析后锚点可追溯

# P4-2/P4-3/P4-4 chunk 落库 + 页码
# 断言：SELECT * FROM audit_document_chunks WHERE trace_id=$TRACE 有行；page_nums/bbox 有值（OntoSKU 路径）
#       trace.position_anchor 仍非空（双写兼容）

# P4-5 字段匹配 chunk（含 extra_fields）
# 断言：SELECT * FROM audit_field_sources WHERE table_name='data_contracts' AND row_id=$ROW
#       有行；field_name 覆盖列名 + 至少一个 extra_fields->$.字段名

# P4-6 行→trace
# 断言：data_* 行 document_trace_id=$TRACE；链路 row→trace→document 可查

# P4-7 统一证据引用
curl -s "$BASE/traces/data_row/$ROW" | python -m json.tool
# 断言：返回 refs[] 含 document_chunk 类来源 + quote + page_number

# P4-8 trace 查询接口
# 断言：上面接口返回完整溯源链（refs + field_sources + chunk 原文/页码）

# P4-9 前端展示（手工）：点字段→弹原文片段+页码；降级路径标待核实

# P4-10 重解析失效
curl -s -X POST $BASE/documents/reparse -H "Content-Type: application/json" -d '{"document_id":$TRACE}' | python -m json.tool
# 断言：旧 ocr_version 的 audit_document_chunks.status=superseded；
#       查询 traces 接口时该证据标「待复核」（靠 join/version 推导，旧行未删）
```

> 仿 `test_p1_flow.py` 写 `backend/tests/test_p4_trace.py`，覆盖 P4-3/5/7/8/10 的断言；P4-9 前端手工核验。

## 9. 完成标准（汇总）

- [ ] 数据库 `M004` 迁移执行成功（三张新表），可回滚
- [ ] chunk 逐条落 `audit_document_chunks`（双写 `position_anchor` 兼容）
- [ ] 字段→chunk 溯源（`audit_field_sources`，含 `extra_fields` 字段）
- [ ] data_* 行 → trace → 文档链路可查（P4-6）
- [ ] `GET /traces/{result_type}/{result_id}` 返回完整溯源链（P4-8）
- [ ] 前端至少一处真实溯源展示（非占位）；无页码标待核实（P4-9）
- [ ] 重解析后旧 chunks 标 `superseded`；溯源查询靠 status/版本推导过期并标待复核（留痕不删）（P4-10）
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_4.md`）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1/2/3 行为未破坏）
