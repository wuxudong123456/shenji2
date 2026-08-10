# 回滚预案（Rollback Plan）— U4

> 用途：AuditWorkbench 上线后出现故障时，按本预案回退数据库结构 + 代码 + 灰度。
> 数据源：`backend/data/migrate.py`（14 个迁移函数，逐字反推回滚 DDL）+ git 历史。
> 配套：[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)（§4 备份 + §5 回滚演练引用本预案）。
> 建立时间：2026-08-10｜分支：phase2

---

## 0. 前置原则

1. **DDL 回滚是破坏性的**——`DROP TABLE` / `DROP COLUMN` 丢数据。**任何回滚前必须先备份**（见 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) §4.1：`mysqldump ... tt > backup_tt_<date>.sql`）。
2. **回滚顺序与 main() 相反**：后加的先撤（子表/依赖先 DROP，父表后 DROP）。migrate.py `main()` 顺序见 [:678-691](../backend/data/migrate.py#L678-L691)；全量回滚逆序见本预案 §4。
3. **按需回滚**：通常只回滚出问题的那一个 Phase（对应若干迁移函数），不必全量。本预案 §1-§3 按 Phase（M-tag）分节，每节自包含。
4. **列/索引回滚**：`DROP INDEX` 须先于其引用列的 `DROP COLUMN`；多列逆序 DROP。
5. **migrate.py 不支持自动回滚**——本预案是**人工执行**的 SQL 清单。执行后**不要**再跑 migrate.py（会重新建回来）；如需重跑，先确认要恢复哪些对象。

---

## 1. 数据库 DDL 回滚（按 Phase）

> 每节标题标注 migrate.py 函数名 + M-tag。SQL 均在 `tt` 库执行。`DROP COLUMN` 用 `IF EXISTS` 防重复执行报错（MySQL 8.0+ 语法；5.7 不支持则逐列先 `information_schema` 预检）。

### M008 — Phase 8 七步契约层（`migrate_phase8_contract_tables`）

最新一期结构变更，回滚优先级最高。**先撤列、再 DROP 表**。

```sql
-- ④ 撤 audit_analysis_tasks 三列（附录A §2 增量列）
ALTER TABLE tt.audit_analysis_tasks
  DROP COLUMN IF EXISTS analysis_scope,
  DROP COLUMN IF EXISTS analysis_target,
  DROP COLUMN IF EXISTS focus_item_id;

-- ③ DROP 三表（无相互 FK，顺序无关；audit_step_summaries 引用 analysis_tasks.id 但无 FK 约束）
DROP TABLE IF EXISTS tt.audit_step_summaries;
DROP TABLE IF EXISTS tt.audit_agent_traces;
DROP TABLE IF EXISTS tt.project_suspicions;
```

> ⚠️ 注意：`audit_agent_traces` 是**溯源铁律**的落库表（Phase8 P8-11 `_persist_trace`）。DROP 后所有历史 Agent 推理链丢失——回滚前务必单独 dump：`mysqldump tt audit_agent_traces > traces_backup.sql`。

### M006 — Phase 7 引擎规则（`migrate_engine_rules`）

```sql
DROP TABLE IF EXISTS tt.audit_item_methods;
DROP TABLE IF EXISTS tt.audit_engine_rules;
```

> 两表逻辑关联 `audit_violations.id`（无 FK 约束），无依赖顺序。

### M005 — Phase 5 数据工坊（`migrate_phase5_data_tables`）

```sql
DROP TABLE IF EXISTS tt.data_interviews;
DROP TABLE IF EXISTS tt.data_procurements;
```

> ⚠️ 这是**业务数据表**——DROP 丢失该项目的采购/访谈结构化数据。回滚前 dump 这两张表 + `data_contracts` 等（data_contracts 不在本 Phase 建，但承载疑点命中行）。

### M004 — Phase 4 溯源三表（`migrate_phase4_provenance_tables`）

```sql
-- audit_field_sources.chunk_id 逻辑引用 audit_document_chunks.id（无 FK 约束），仍按依赖序先 DROP
DROP TABLE IF EXISTS tt.audit_field_sources;
DROP TABLE IF EXISTS tt.audit_source_refs;
DROP TABLE IF EXISTS tt.audit_document_chunks;
```

> ⚠️ 这是**溯源铁律的核心存储**（文档切片 + 证据引用 + 字段来源）。DROP 后所有结论失去页码/原文锚点——溯源链断裂。非极端情况不建议回滚此 Phase。

### M003 — Phase 3 解析标识（`migrate_phase3_task_payload` + `migrate_phase3_trace_parse_columns`）

```sql
-- ② audit_task_queue.payload
ALTER TABLE tt.audit_task_queue DROP COLUMN IF EXISTS payload;

-- ① audit_document_traces 五列 + 两索引（先 DROP INDEX 再 DROP COLUMN）
ALTER TABLE tt.audit_document_traces DROP INDEX idx_external_doc;
ALTER TABLE tt.audit_document_traces DROP INDEX idx_parse_status;
ALTER TABLE tt.audit_document_traces
  DROP COLUMN IF EXISTS parsed_at,
  DROP COLUMN IF EXISTS parse_status,
  DROP COLUMN IF EXISTS parse_engine,
  DROP COLUMN IF EXISTS external_job_id,
  DROP COLUMN IF EXISTS external_document_id;
```

> ⚠️ `parse_status` 默认 `'pending'`，DROP 后解析状态丢失，重新解析链路断。

### M002 — Phase 2 资料空间列（`migrate_phase2_trace_columns`）

```sql
ALTER TABLE tt.audit_document_traces DROP INDEX idx_project_cat;
ALTER TABLE tt.audit_document_traces DROP INDEX idx_audit_year;
ALTER TABLE tt.audit_document_traces
  DROP COLUMN IF EXISTS deleted_at,
  DROP COLUMN IF EXISTS file_size,
  DROP COLUMN IF EXISTS minio_bucket,
  DROP COLUMN IF EXISTS file_subcategory,
  DROP COLUMN IF EXISTS file_category,
  DROP COLUMN IF EXISTS audit_year;
```

> ⚠️ `deleted_at` 是**软删标记**——DROP 后软删记录无法区分，已删文件"复活"。回滚前确认无在用软删数据。

### 知识工坊 — 4 关联表 + 2 列 + 法规 collation + 案例索引

涉及 `migrate_knowledge_tables` / `migrate_audit_violations_columns` / `migrate_law_refs_collation` / `migrate_case_indexes`。

```sql
-- 案例索引（注意：这三个索引在建表 DDL 里也有，DROP 表后此步空跑无妨）
ALTER TABLE tt.audit_case_law_refs     DROP INDEX idx_case;
ALTER TABLE tt.audit_case_violations   DROP INDEX idx_violation;
ALTER TABLE tt.audit_cases             DROP INDEX idx_created_at;

-- law_id collation 还原（migrate_law_refs_collation 把它改成 utf8mb4_0900_ai_ci，还原回 unicode_ci）
ALTER TABLE tt.audit_case_law_refs
  MODIFY law_id VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL
  COMMENT 'sys_core_law_allaudit.id';
ALTER TABLE tt.audit_violation_law_refs
  MODIFY law_id VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL
  COMMENT 'sys_core_law_allaudit.id';

-- 4 张关联/案例表（audit_violation_law_refs 有 FK→audit_violations，最后 DROP）
DROP TABLE IF EXISTS tt.audit_case_law_refs;
DROP TABLE IF EXISTS tt.audit_case_violations;
DROP TABLE IF EXISTS tt.audit_cases;
DROP TABLE IF EXISTS tt.audit_violation_law_refs;

-- audit_violations 两列（逆序：required_data 先于 audit_procedure）
ALTER TABLE tt.audit_violations DROP COLUMN IF EXISTS required_data;
ALTER TABLE tt.audit_violations DROP COLUMN IF EXISTS audit_procedure;
```

> ⚠️ collation 还原后，跨库 JOIN 法规表会重新出现 `COLLATE` 转换导致的慢查询（这正是 migrate 修复的问题）。仅当确认要彻底退回旧版才执行。

### Phase 1 — 立项上下文列（`migrate_project_context_columns`）

```sql
ALTER TABLE tt.audit_projects DROP INDEX idx_type;
ALTER TABLE tt.audit_projects DROP INDEX idx_unit;
ALTER TABLE tt.audit_projects
  DROP COLUMN IF EXISTS amount,
  DROP COLUMN IF EXISTS scope,
  DROP COLUMN IF EXISTS objective,
  DROP COLUMN IF EXISTS auditor,
  DROP COLUMN IF EXISTS leader,
  DROP COLUMN IF EXISTS target_level,
  DROP COLUMN IF EXISTS audit_method,
  DROP COLUMN IF EXISTS audit_type,
  DROP COLUMN IF EXISTS audited_unit,
  DROP COLUMN IF EXISTS project_code;
```

### Q2.2 — 表达式 SQL 缓存（`migrate_expression_sql`）

```sql
DROP TABLE IF EXISTS tt.audit_expression_sql;
```

### Q1.4 — 文件 MD5 去重（`migrate_trace_md5`）

```sql
ALTER TABLE tt.audit_document_traces DROP INDEX idx_project_md5;
ALTER TABLE tt.audit_document_traces DROP COLUMN IF EXISTS file_md5;
```

---

## 2. 代码回滚（git）

**项目无 git tag**（截至 2026-08-10）。代码回滚靠「定位 Phase 边界 commit + reset」。

### 2.1 发布前先打回滚点（必做，[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) §4.4）

```powershell
# 在含 Phase 9 全部 T 修复的 HEAD 上打标签
git tag pre-release-20260810
```

### 2.2 场景 A：整个发布出问题，回退到发布前

```powershell
# 代码回到发布点（保留改动用 --soft；彻底丢弃用 --hard，慎用）
git reset --hard pre-release-20260810
# 重启后端（必杀进程树，见下方约束）
```

### 2.3 场景 B：单个 Phase 出问题，回退到该 Phase 前

定位该 Phase 的**首个 commit**（commit message 含 `phaseN`/`M00X` 标记），reset 到它的父提交：

```powershell
# 例：Phase 8 首个 commit = 31931fe（M008 建表）
git log --grep="phase8" --oneline          # 列出该 Phase 所有 commit
git log --grep="M008"  --oneline
# 回退到 31931fe 的父提交（即 Phase 7 末尾）
git reset --hard 31931fe~1
```

**已知 Phase 边界锚点**（git log 实测）：

| Phase | 首个 commit | 回退目标（其父） | 说明 |
|-------|------------|-----------------|------|
| Phase 9 | `23e03b0`（phase-exec 文档）+ T1-T8 修复 commits | `23e03b0~1` 或具体 T-commit 前 | T 项均为 bugfix，可逐项回滚 |
| Phase 8 | `31931fe`（M008 建表）| `31931fe~1`（Phase 7 末 `30089ba`/`c89ada2`）| 七步契约层 |
| Phase 7 | `c89ada2`（M006 引擎规则）| `c89ada2~1` | 引擎映射表 |

> 更早 Phase（1-6）首 commit 需 `git log --grep="phaseN"` 自行定位；本表只列最近可实测的锚点。

### 2.4 后端重启约束

改代码回滚后，旧 app.py 进程树若没杀干净仍占 5000 端口，新代码不生效。**必须 `taskkill /F /T`**（单独一个 PS 调用，hook 会拦 `/F` + `Remove-Item` 组合）：

```powershell
taskkill /F /T /IM python.exe   # 杀整棵进程树（单独调用）
cd backend; python app.py        # 重启
```

---

## 3. 灰度切回（U1 待建）

**现状**：前端**无 feature flag**（U1 尚未实现）。新旧接口切换当前只能靠代码回滚，无法运行时灰度。

### 3.1 当前手动切回法（U1 建成前的过渡）

若新接口（七步契约层）出问题、需切回旧行为：

1. 前端 `js/api.js` 的 `AuditAPI` 目标端点回退到旧路径（git 回滚 `analysis-wiz.js` / `api.js` 到 Phase 7 末 `c89ada2~1`）。
2. 后端 `audit_routes.py` 旧路由在 Phase 8 改造中**未被删除**（仅新增契约层端点），所以后端可不回滚——旧前端打旧路由仍可用。
3. **数据库不回滚**（新表/列向后兼容，旧代码不读它们）。

### 3.2 U1 建成后的灰度切回（目标方案）

U1 计划在前端加 feature flag（如 `localStorage.audit_use_legacy = "1"` 或 URL 参数），运行时切换新旧接口路径，无需发版。**待 U1 实现后补全本节。**

---

## 4. 回滚演练流程（上线前必跑）

在**测试环境**（非生产库）执行，验证预案可回退到 pre-release：

1. **备份**：`mysqldump -u <u> -p tt > pre_drill_tt.sql`；`git tag drill-start`。
2. **模拟故障**：随意选一个 Phase（建议 M008，最新），执行 §1 对应 SQL 块。
3. **验证回退**：
   - `DESCRIBE tt.audit_analysis_tasks` 确认 `focus_item_id` 等列已撤；
   - `SHOW TABLES LIKE 'project_suspicions'` 确认表已 DROP；
   - 跑回归测试 `python tests/test_p8_seven_step.py` 预期**报错**（表不存在=回退生效）。
4. **恢复**：从备份还原 `mysql -u <u> -p tt < pre_drill_tt.sql`；再跑 migrate.py 确认幂等（全部「= 已存在」）；跑回归测试预期**全绿**。
5. **演练记录**：在 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) §5.2 签字 + 写演练日期。

### 4.1 全量回滚顺序（极端情况，撤掉所有迁移）

逆 main() 顺序 [:678-691](../backend/data/migrate.py#L678-L691)：

```
M008 → M006 → M005 → M004 → M003 → M002 → 知识工坊 → Phase1 → Q2.2 → Q1.4
```

即：先撤 Phase 8 契约层，最后撤最早的 Q1.4 MD5 列。每块 SQL 见 §1 各小节。

---

## 5. 决策树：出问题后回滚到哪一级？

```
线上故障
  ├─ 单个接口/前端 bug      → 场景 B 单 commit 回滚（§2.3），不动 DB
  ├─ 整个发布行为异常        → 场景 A 回到 pre-release tag（§2.2）+ §3 手动灰度切回
  ├─ 某张新表数据有问题      → §1 对应 M00X 的 DROP（先 dump！）
  └─ 数据库结构错乱/不可逆   → 全量备份还原（mysqldump 还原）+ §4.1 全量回滚
```

---

## 附：迁移函数 ↔ 回滚块 对照表

| migrate.py 函数 | M-tag | 本预案节 | 破坏性 |
|----------------|-------|---------|:------:|
| `migrate_phase8_contract_tables` | M008 | §1 M008 | 🔴 高（溯源链） |
| `migrate_engine_rules` | M006 | §1 M006 | 🟡 中（规则映射） |
| `migrate_phase5_data_tables` | M005 | §1 M005 | 🔴 高（业务数据） |
| `migrate_phase4_provenance_tables` | M004 | §1 M004 | 🔴 高（溯源核心） |
| `migrate_phase3_task_payload` | M003② | §1 M003 | 🟡 中（任务输入） |
| `migrate_phase3_trace_parse_columns` | M003① | §1 M003 | 🟡 中（解析状态） |
| `migrate_phase2_trace_columns` | M002 | §1 M002 | 🟡 中（软删） |
| `migrate_audit_violations_columns` | 知识工坊 | §1 知识工坊 | 🟢 低（两列） |
| `migrate_knowledge_tables` | 知识工坊 | §1 知识工坊 | 🟡 中（案例库） |
| `migrate_case_indexes` | 知识工坊 | §1 知识工坊 | 🟢 低（索引） |
| `migrate_law_refs_collation` | 知识工坊 | §1 知识工坊 | 🟢 低（collation，可恢复慢查询） |
| `migrate_project_context_columns` | Phase1 | §1 Phase1 | 🟢 低（立项字段） |
| `migrate_expression_sql` | Q2.2 | §1 Q2.2 | 🟢 低（缓存表） |
| `migrate_trace_md5` | Q1.4 | §1 Q1.4 | 🟢 低（去重列） |
