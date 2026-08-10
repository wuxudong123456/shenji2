# PHASE_2 执行包：资料空间管理

> **执行协议**：本文件是 Phase 2 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 1 已完成（四阶段状态机 + finalize 建桶 + `allowed_actions` 矩阵已验收，见 [TEST_REPORT_PHASE_1.md](../TEST_REPORT_PHASE_1.md)）。
> 铁律：不改变现有前端视觉；不破坏 Phase 1 已验收的流程控制；旧接口路径保持兼容（灰度）。

---

## 0. 执行者须知（先读）

- **只做本 Phase 的事**：只做「资料空间管理」——年度派生、对象前缀、workspace manifest、年度项目树、文件落位/列表/下载/软删/隔离。
  - **不做 OCR 解析**（Phase 3）：本 Phase 只做 `upload` 的**空间落位层**（前缀 / bucket / manifest / trace 空间管理列）；现有异步 OCR 触发**原样保留**。Phase 3 的 P3-1「上传+trace」在本 Phase 提供的 upload 框架上叠加**OCR 解析触发 + 解析技术标识列**（`external_document_id` 等，方案 DDL ④），不在本 Phase 实现。不建 chunk 表、不写字段抽取。
  - **不做溯源**（Phase 4）：不建 `audit_source_refs`、不打 `document_trace_id` 关联。
  - **不碰智能分析**（Phase 8）。
- **小功能切片**：按第 4 节 P2-1..P2-10 逐个开发，**每个小功能测试通过后才进入下一个**。
- **数据库变更单独 commit**，配回滚语句（见第 5 节，走 `backend/data/migrations/M002_*` 幂等模板，与 M001 同款）。
- **不破坏 Phase 1 接口**：`POST /projects`、`PUT /basic|target-scope|items`、`workspace/finalize` 行为不变；本 Phase 只**叠加**资料空间能力。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 2 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 1 四阶段状态机 | ✅ | `setup_stage` / `allowed_actions` / `check_stage` 已验收；`workspace/finalize` 已建 `audit-project-{project_id}` 桶并落 `workspace_created_at` |
| Phase 1 finalize 建桶 | ✅ | 真实 `make_bucket` 只在 finalize（[audit_routes.py:363](../../backend/routes/audit_routes.py#L363)）；**upload 现状的二次 make_bucket 是遗留，本 Phase 删除**（P2-2） |
| 决策 2（年度口径） | ✅ 已确认 | 取审计期间起始年份 |
| 决策 7（音频转写） | ✅ 已确认 | 本轮只规划 `audio/{original,transcript}/` 目录，不建音频转写算法 |
| 决策 12（年度派生） | ✅ 已确认 | 统一取 `audit_period` 第一个年份，解析失败兜底用 `created_at` 年份并标记来源 |
| K3（Phase 0 勘察） | ✅ | 旧文件落在 `{project_id}/raw/` 前缀下；本 Phase **不迁移**，年度树兼容列入（标 `legacy_raw`） |

> 决策 2/7/12 **已确认**（按建议）；年度口径若将来调整，只改 `derive_audit_year()` 一处（见 §3/P2-1）。

## 2. 目标

**年度隔离 + 项目隔离，不建 MinIO 空目录。** 资料树以 `workspace-manifest.json` 为单一事实源，前端年度—项目—类型树读真实数据，跨项目/跨年度串读被后端拒绝。

四条硬指标（对应 §9 验收门）：
1. 年度树返回真实年度/项目/文件（非 mock、非拼字符串）。
2. 跨项目 / 跨年度访问被拒（403/404）。
3. manifest 与 MinIO 实际对象一致（对账无漂移）。
4. 重复 finalize 幂等（Phase 1 已验，本 Phase 不回退）。

## 3. 资料空间核心规则

### 3.1 目录与对象前缀（不建 MinIO 空目录）

每项目一个 bucket（Phase 1 finalize 已建），对象按「年度/项目/分类」前缀落位，**用对象 key 前缀表达目录，绝不创建零字节标记对象**（空目录是反模式）：

```
bucket: audit-project-{project_id}
└─ {audit_year}/{project_id}-{safe_name}/
   ├─ project-materials/                 ← 项目自产资料（封面/底稿等，无文件即无该前缀）
   ├─ text/{word,pdf,excel,txt}/         ← 文本类按格式分子类
   ├─ audio/{original,transcript}/       ← original=原始音频；transcript=转写产物（本轮目录预留，不产生）
   ├─ image/   video/   other/           ← 图片/视频/兜底
   └─ workspace-manifest.json            ← 资料树单一事实源（年/项目/类型/文件索引）
```

完整对象 key 示例：
`2026/f0ce53d90fe1-某市教育局2026预算执行审计/text/pdf/a1b2c3d4e5f6.采购合同.pdf`

要点：
- **年度**是查询与对象前缀的一级逻辑维度；**项目**以 `project_id` 为唯一隔离键。
- **文件类型由后端判定**（MIME / 扩展名），前端不承担目录规则（P2-4 映射表见 §3.4）。
- 上传 API 路径保持兼容，由**后端**把文件落入正确前缀（前端只传文件，不传路径/分类）。

### 3.2 年度派生（决策 12）

唯一函数 `derive_audit_year(audit_period: str, created_at: datetime) -> (year: str, source: str)`：
- 优先解析 `audit_period`，正则取**第一个 4 位年份**（如 `"2026-01-01至2026-06-30"` → `"2026"`）。
- `audit_period` 缺失或解析失败 → 兜底 `created_at` 年份，`source="created_at"`；否则 `source="audit_period"`。
- 派生值落 `audit_document_traces.audit_year`（冗余，便于年度树/对账查询），**不落 `audit_projects`**（年度是派生属性，非立项字段）。

### 3.3 workspace manifest（单一事实源）

每个项目空间在 finalize 时生成首版 `workspace-manifest.json`（存在该项目 bucket 根：`{audit_year}/{pid}-{safe_name}/workspace-manifest.json`），上传/删除时增量更新。**年度树、文件列表均以 manifest 为准，MinIO 列对象只用于对账兜底。**

```jsonc
{
  "manifest_version": 1,
  "project_id": "f0ce53d90fe1",
  "project_name": "某市教育局2026年度预算执行审计",
  "safe_name": "某市教育局2026预算执行审计",
  "audit_year": "2026",
  "bucket": "audit-project-f0ce53d90fe1",
  "prefix": "2026/f0ce53d90fe1-某市教育局2026预算执行审计/",
  "created_at": "2026-08-07T10:00:00",
  "updated_at": "2026-08-07T10:05:00",
  "files": [
    {
      "trace_id": 123,
      "file_name": "采购合同.pdf",            // 用户可见原始名
      "object_key": "2026/.../text/pdf/a1b2c3.采购合同.pdf",  // MinIO 完整 key
      "category": "text", "subcategory": "pdf",
      "size": 123456, "md5": "...", "content_type": "application/pdf",
      "uploaded_at": "2026-08-07T10:05:00",
      "deleted": false,
      "legacy_raw": false                      // {pid}/raw/ 旧文件兼容标记（P2 兼容列入）
    }
  ]
}
```

### 3.4 文件类型分类映射（后端判定，P2-4）

| category | subcategory | 判定规则 |
|---|---|---|
| text | word | 扩展名 `.doc` / `.docx` |
| text | pdf | 扩展名 `.pdf` 或 MIME `application/pdf` |
| text | excel | 扩展名 `.xls` / `.xlsx` / `.csv` |
| text | txt | 扩展名 `.txt` / `.md` |
| image | — | MIME `image/*` |
| audio | original | MIME `audio/*`（转写产物本轮不产生，`transcript` 子目录仅预留） |
| video | — | MIME `video/*` |
| other | — | 兜底；**旧 `{pid}/raw/` 文件**首次纳入时按本表尽量归类，无法判定归 `other` 并置 `legacy_raw=true` |

### 3.5 软删（P2-9，决策"留痕"）

删除文件**不物理移除 MinIO 对象**：
1. `audit_document_traces.deleted_at = NOW()`（留痕，可审计/可恢复）。
2. manifest `files[].deleted = true`（列表/树默认过滤 `deleted=true`）。
3. MinIO 对象**保留原位**（不移动到 `trash/` 前缀，避免 key 漂移与对账复杂化）。

### 3.6 跨项目 / 跨年度隔离（P2-10）

- 一切文件操作以 `project_id` 为隔离键；`year` 仅作**查询过滤维度**，不是独立寻址键。
- download / delete 必须校验目标 `object_key` **属于当前 `project_id`**（从 key 前缀 `{year}/{pid}-` 解析 pid 比对 + manifest 对账），禁止用别项目的 `object_key` 越权读取。
- 年度树 `GET /workspace/tree?year=2026` 只返回该年度项目，不混入其它年度。

## 4. 任务清单（P2-1 .. P2-10，逐个测试）

| # | 小功能 | 完成标准 |
|---|---|---|
| P2-1 | 后端统一计算审计年度 | `derive_audit_year()` 唯一实现；`audit_period` 解析优先、`created_at` 兜底并标来源；年度只由后端派生（决策 12） |
| P2-2 | 项目 bucket 延迟创建 | 删除 upload 里的 `make_bucket`（finalize 已建）；upload 前置校验 `setup_stage=workspace` 且桶存在，否则 409/404 |
| P2-3 | workspace manifest | finalize 生成首版 manifest；上传/软删增量更新；manifest 为资料树单一事实源 |
| P2-4 | 文件类型分类规则 | 后端按 §3.4 映射表判定 category/subcategory；前端不传分类 |
| P2-5 | 年度项目树接口 | `GET /api/audit/workspace/tree?year=` 返回真实年度—项目—类型—文件树（读 manifest） |
| P2-6 | 文件上传路径 | upload 把文件落入 §3.1 正确前缀；旧 `{pid}/raw/` 不再用于新文件 |
| P2-7 | 文件列表 | `GET /projects/{id}/files` 支持 `year`/`category` 过滤；按 manifest 返回，过滤 `deleted` |
| P2-8 | 文件下载 | 预签名 URL 支持「每项目 bucket」（修 `minio_client` 硬编码）；跨项目 key 被拒 |
| P2-9 | 文件删除 | 软删（trace.deleted_at + manifest.deleted=true），对象留原位，列表/树过滤 |
| P2-10 | 跨项目访问拦截 | download/delete/list 越权访问别项目对象 → 403/404；年度树不串年度 |

**涉及文件**：`backend/services/workspace_service.py`（新增）、`backend/services/minio_client.py`（加 bucket 参数）、`backend/routes/audit_routes.py`（upload/files/download/delete + 新 tree）、`backend/data/migrations/M002_*`（trace 加列）。

## 5. 本 Phase DDL（M002，幂等，单独 commit）

```sql
-- ① audit_document_traces 资料空间管理列（Phase 2）
--    注：该表为旧系统已有表（Phase 3 另加解析技术标识列 external_document_id 等，不在本 Phase）。
--    幂等：存储过程判列存在再 ADD，重复执行安全。
ALTER TABLE tt.audit_document_traces
  ADD COLUMN audit_year       VARCHAR(4)   NULL COMMENT '审计年度（决策12派生）',
  ADD COLUMN file_category    VARCHAR(20)  NULL COMMENT '一级分类 text/image/audio/video/other',
  ADD COLUMN file_subcategory VARCHAR(20)  NULL COMMENT '二级分类 word/pdf/excel/txt/original/...',
  ADD COLUMN minio_bucket     VARCHAR(80)  NULL COMMENT '所在 bucket（audit-project-{pid}）',
  ADD COLUMN file_size        BIGINT       NULL COMMENT '文件字节数（manifest 对账用）',
  ADD COLUMN deleted_at       DATETIME     NULL COMMENT '软删时间（NULL=未删，决策：留痕可恢复）',
  ADD INDEX idx_audit_year (audit_year),
  ADD INDEX idx_project_cat (project_id, file_category);

-- 回滚（单独文件，开发期用）
-- ALTER TABLE tt.audit_document_traces
--   DROP INDEX idx_project_cat, DROP INDEX idx_audit_year,
--   DROP COLUMN deleted_at, DROP COLUMN file_size, DROP COLUMN minio_bucket,
--   DROP COLUMN file_subcategory, DROP COLUMN file_category, DROP COLUMN audit_year;
```

> **manifest 不是表**：`workspace-manifest.json` 是 MinIO 对象，不建 MySQL 资料表。`audit_document_traces` 的新列是冗余（便于查询/对账），资料树事实源仍是 manifest。

## 6. 本 Phase 接口契约（完整，直接对照实现）

**统一约定**：文件类操作均要求项目处于 `workspace` 阶段（`allowed_actions` 含 `upload`），否则 409；所有 MinIO 调用显式传 `bucket="audit-project-{project_id}"`。

### 6.1 修改：`POST /api/audit/projects/{id}/upload`（P2-2/P2-3/P2-4/P2-6）

行为变更（**OCR 触发保留现状不动**）：
- 前置：`setup_stage=workspace` 且桶存在；否则 409（"请先完成立项四阶段并创建资料空间"）。
- **删除** `if not bucket_exists: make_bucket`（line 727-728）——桶由 finalize 建。
- `minio_path` 由 `{pid}/raw/{file_id}/{filename}` 改为 §3.1 前缀：`{year}/{pid}-{safe_name}/{category}/[{subcategory}/]{file_id}.{原扩展名}`。
- 写 trace 时落新列：`audit_year / file_category / file_subcategory / minio_bucket / file_size`。
- **写 manifest**：上传成功后把文件追加进 `files[]`（增量更新 `updated_at`）。
- 其余（MD5 去重、建 trace、异步 OCR 任务）保持。

响应不变（`{success, file_id, file_name, minio_bucket, minio_path, trace_id, task_id, ocr_status}`）。

### 6.2 修改：`GET /api/audit/projects/{id}/files`（P2-7）

- 新增可选 query：`year`、`category`（按 manifest 过滤；缺省返回全部未删）。
- 数据源**改为 manifest**（而非直接查 trace 表），默认过滤 `deleted=true`；保留 `ocr_done` 字段（与 trace join）。
- 响应增加：`audit_year / category / subcategory / size / deleted`。

### 6.3 新增：`GET /api/audit/workspace/tree?year={year}`（P2-5/P2-10）

- 读所有 `status='active'`（或 `setup_stage=workspace`）项目，按 `audit_year` 过滤，返回真实年度—项目—类型—文件树。
- 结构：
```jsonc
{
  "success": true, "year": "2026",
  "projects": [
    { "project_id": "...", "project_name": "...", "safe_name": "...",
      "counts": { "text": 3, "image": 1, "audio": 0, "video": 0, "other": 0 },
      "files": [ { "trace_id":1, "file_name":"...", "category":"text", "subcategory":"pdf", "size":123, "uploaded_at":"..." } ] }
  ]
}
```
- 数据源：manifest（跨项目汇总）；manifest 缺失时回退「列对象 + trace 对账」并告警（不静默）。

### 6.4 修改：`GET /api/audit/workspace/download`（P2-8/P2-10）

- query 增加 `project_id`（必填）；从 `project_id` 派生 bucket，**不再用单一 `MINIO_BUCKET`**。
- 校验 `file`（或 `object_key`）所属 pid == `project_id`（前缀解析 + manifest 对账），不匹配 → 403。
- `get_presigned_url(path, bucket=...)` 需先修 `minio_client` 硬编码（见 §6.7）。

### 6.5 修改：`DELETE /api/audit/workspace/delete`（P2-9/P2-10）

- query 增加 `project_id`（必填）；校验对象归属同 §6.4。
- 改为**软删**：trace.`deleted_at=NOW()` + manifest.`files[].deleted=true`；MinIO 对象不动。
- 响应：`{success, message, soft_deleted:true, trace_id}`。

### 6.6 `POST /api/audit/projects/{id}/workspace/finalize`（Phase 1 已实现，本 Phase 叠加）

- finalize 成功后**追加**：生成首版 `workspace-manifest.json`（空 `files[]` + 年度/safe_name/bucket/prefix 元信息），存入该项目 bucket。
- 幂等不变：重复 finalize 不重建桶、不覆盖已有 manifest（已存在则跳过 manifest 初始化）。

### 6.7 底层改造：`backend/services/minio_client.py`（P2-2/P2-8 前置）

现状 `get_presigned_url / list_objects / list_folders / delete_object / get_object_info` **硬编码 `Config.MINIO_BUCKET`**，每项目独立 bucket 下全部失效。统一加 `bucket: str = None` 参数（默认回落 `Config.MINIO_BUCKET`，与 `upload_file/download_file` 一致）：
- `get_presigned_url(object_path, bucket=None, expires=3600)`
- `list_objects(prefix='', bucket=None, recursive=True)`（顺带暴露 recursive）
- `list_folders(prefix='', bucket=None)`
- `delete_object(object_path, bucket=None)`
- `get_object_info(object_path, bucket=None)`

### 6.8 旧接口灰度（兼容，不删）

`GET /workspace/files`、`GET /workspace/download`、`DELETE /workspace/delete`（按 MinIO 文件夹名寻址的三接口，line 1493/1513/1527）标记 `@deprecated`，保留兼容至 Phase 5 前端切换；新逻辑统一走 §6.2-6.5（带 `project_id`）。

### 6.9 新增服务：`backend/services/workspace_service.py`（P2-1/P2-3/P2-4/P2-5）

职责：年度派生、safe_name 计算、分类映射、manifest 读写（load/save/追加/软删标记）、年度树聚合、跨项目归属校验。依赖 `minio_client` + DB。**所有"前缀/分类/年度"规则集中在此服务，路由层只调不重复实现。**

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| `minio_client` 五个函数硬编码 `MINIO_BUCKET` | §6.7 统一加 `bucket` 参数；改完先单测旧接口（默认桶）不破 |
| upload 现状二次 `make_bucket`（line 727-728） | 删除；桶仅 finalize 建（P2-2） |
| 新旧两个上传入口并存 | 本 Phase 改 `POST /projects/{id}/upload`；旧 `/workspace/files` 等三接口仅灰度保留（§6.8） |
| 旧文件在 `{pid}/raw/`（K3） | **不迁移**；首次年度树/列对象时纳入 manifest（`legacy_raw=true`），按扩展名尽量归类、否则 `other` |
| manifest 与实际对象漂移 | 对账规则：年度树/列表前「列对象 vs manifest」比对，差集告警不静默；上传/软删为唯一合法变更点 |
| 年度派生口径（决策 12）未最终勾选 | 口径集中在 `derive_audit_year()` 一处，领导改口径只改这一函数 |
| upload 耦合异步 OCR（属 Phase 3） | 本 Phase **不动 OCR**，只改落位/桶/manifest；OCR 完善留 Phase 3 |
| finalize 已建桶但 manifest 首版缺失（存量 active 项目） | P2-3 兜底：读 manifest 失败时按"列对象 + trace"重建首版并写回 |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit

# 前置：用 Phase 1 流程造一个 workspace 项目（已 finalize，有桶）
# PID=<已 finalize 的项目 id>；假定 audit_period="2026-01-01至2026-06-30"

# P2-1 年度派生：上传后 trace.audit_year == "2026"（取 audit_period 首年）
curl -s -X POST $BASE/projects/$PID/upload -F "file=@采购合同.pdf" | python -m json.tool
# 断言：返回 minio_path 以 "2026/" 开头；查 trace.audit_year == "2026"

# P2-2 bucket 延迟：用未 finalize 的项目上传 → 期望 409
curl -s -X POST $BASE/projects/<未finalize的PID>/upload -F "file=@x.pdf" | python -m json.tool
# 断言：409 + "请先完成立项四阶段并创建资料空间"

# P2-3 manifest：列对象含 workspace-manifest.json，且 files[] 含刚传文件
# （后端排错时用 minio_client.list_objects(prefix="2026/", bucket=audit-project-$PID)）

# P2-4 分类：传 .pdf → category=text/subcategory=pdf；传 .png → category=image
curl -s -X POST $BASE/projects/$PID/upload -F "file=@截图.png" | python -m json.tool
# 断言：minio_path 含 "/image/"；category=image

# P2-5 年度树：真实年度/项目/文件
curl -s "$BASE/workspace/tree?year=2026" | python -m json.tool
# 断言：projects[] 含 $PID；counts 与实际文件数一致；不含其它年度项目

# P2-7 文件列表（带过滤）
curl -s "$BASE/projects/$PID/files?year=2026&category=text" | python -m json.tool
# 断言：只含 text 类、2026 年、未删文件

# P2-8 下载（每项目桶预签名）
curl -s "$BASE/workspace/download?project_id=$PID&file=<object_key>" | python -m json.tool
# 断言：返回 url；url 指向 audit-project-$PID 桶

# P2-9 软删
curl -s -X DELETE "$BASE/workspace/delete?project_id=$PID&file=<object_key>" | python -m json.tool
# 断言：soft_deleted=true；再列 files 不见该文件；MinIO 对象仍在原位

# P2-10 跨项目拦截：用 B 项目的 object_key 配 A 项目的 project_id → 期望 403
curl -s "$BASE/workspace/download?project_id=$PID_A&file=<PID_B的object_key>" | python -m json.tool
# 断言：403（对象不属于该项目）

# 幂等回检：重复 finalize 不重建桶、不覆盖已有 manifest（Phase 1 已验，本 Phase 不回退）
curl -s -X POST $BASE/projects/$PID/workspace/finalize -H "Content-Type: application/json" -d '{}' | python -m json.tool
```

> `test_p1_flow.py` 风格的脚本可仿写为 `backend/tests/test_p2_workspace.py`，覆盖 P2-1/2/5/7/8/9/10 的断言（P2-3/4 由 tree/files 间接覆盖）。

## 9. 完成标准（汇总）

- [ ] 数据库 `M002` 迁移执行成功，可回滚
- [ ] 年度树 `GET /workspace/tree?year=` 返回真实年度/项目/文件（非 mock）
- [ ] 跨项目 / 跨年度访问被拒（P2-10 全部命中 403/404）
- [ ] manifest 与 MinIO 实际对象一致（对账无静默漂移）
- [ ] 重复 finalize 幂等（Phase 1 行为不回退，manifest 不被覆盖）
- [ ] upload 落正确前缀 + 不再二次建桶 + OCR 触发未破坏
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_2.md`）
- [ ] `05-regression-baseline.md` 回归通过（旧接口、Phase 1 流程未破坏）
