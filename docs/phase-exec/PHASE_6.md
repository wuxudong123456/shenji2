# PHASE_6 执行包：安全、日志和运行保障

> **执行协议**：本文件是 Phase 6 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 5（数据层 `project_id` 隔离 + `DataService`）已完成。
> 铁律：不破坏 Phase 1-5 已验收行为；本轮用**最小伪鉴权**（`X-User` header），不建真实登录系统；权限提前到智能分析之前。
> ⚠️ **权限边界说明**：本 Phase 只落方案明确的最小边界——**项目内成员可见、非成员全拒**（方案 line 407/396-400）。**owner 与 member 之间的权限细分、是否设全局管理员角色，属 P0-5「权限矩阵确认」待领导拍板，本 Phase 不实现、不臆测**；P6-1~P6-5 在此最小边界内做，角色细分留空等 P0-5。

---

## 0. 执行者须知（先读）

- **关键认知：系统当前完全无鉴权**（无登录、无 `X-User`、无 `current_user`；仅取 `X-Forwarded-For` IP）。`audit_logs` 由 `db.py:145/179` 对所有 INSERT/UPDATE/DELETE **自动写入**（表在 `migrate_logs.sql`，非 schema.sql）。
  - 本 Phase 在此基础上加**最小伪鉴权层**（`X-User` header 标识用户 + 项目成员判定）+ 语义化日志 + 任务监控/告警 + 归档策略。
- **只做本 Phase 的事**：
  - **不做真实登录系统**（账号/密码/SSO/会话）：本轮 `X-User` 伪鉴权，真实登录留待上线前或独立项目。
  - **不做角色细分 / RBAC 引擎**：只实现「成员可见 / 非成员全拒」最小边界；owner/member 权限差异、全局管理员角色**待 P0-5 定稿后再补**（见上方⚠️）。
  - **不做 Agent trace 落库**（`BaseAgent._persist_trace`，Phase 8，§4.7）。
  - **不重建数据层隔离**（Phase 5 已做 `project_id` 强制）：本 Phase 在路由层叠加**用户成员校验**。
- **小功能切片**：按第 4 节 P6-1..P6-10 逐个开发，**每个测试通过后才进入下一个**。
- **数据库变更单独 commit**（M006，见第 5 节）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 6 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 5 数据层 project_id 隔离 | ✅ | `DataService` 强制 project_id；本 Phase 在路由层叠加用户成员校验 |
| Phase 1 状态机 | ✅ | `status` 含 `archived`（P6-10 归档复用）；`migrate-stages` 已要求管理员确认（管理员角色本身待 P0-5） |
| **决策 P0-5（权限矩阵：角色/作用域/权限）** | ⚠️ **未定稿（部分已定向）** | 方案第十三章 P0-5 待领导确认。**已确认**：设全局管理员角色（用户定，细节后续调整）。**仍待定**：owner/member 权限差异、是否设只读角色、全局管理员标识方式与权限范围、本轮是否实现。**本 Phase 只落最小边界**（成员可见/非成员拒）；角色细分留空等 P0-5，不臆测 |
| `audit_logs` 现状 | ✅ | `migrate_logs.sql`，结构含 log_type/action/user/target_type/target_id/detail/duration_ms；57247 行（容量评估见 P6-10） |

## 2. 目标

跨项目访问**全拒**（非项目成员 → 403）；文件/数据/trace 三类资源按项目成员校验；操作日志 + 阶段变更留痕；OCR 任务监控看板；失败重试告警可查；数据归档与保留策略落地。**权限在智能分析（Phase 8）之前就位，比"最后再做"更稳。**

> 说明：「成员可见 / 非成员全拒」是本 Phase 验收的硬指标（方案 line 407/398-400）；**角色间权限细分不在本 Phase 验收范围**（待 P0-5）。

## 3. 安全模型核心规则

### 3.1 伪鉴权（X-User，系统无登录）

- 请求带 `X-User: <用户标识>` header；middleware 提取为 `request.user`（缺省 `"anonymous"`）。
- **真实登录本轮不做**：`X-User` 由前端/网关注入（信任内网网关），上线前接真实认证。
- **来源**：方案 line 407「系统无登录 → 最小伪鉴权（`X-User` header + 项目组成员）」、line 763。

### 3.2 项目成员与访问边界（方案 DDL ③，最小边界）

- **数据模型**：`audit_projects.owner_id`（项目负责人）+ `audit_projects.member_ids`（项目组成员 JSON 数组）——方案 DDL ③（line 491-494）两列，**本 Phase 不额外增减角色列**。
- **最小边界（本 Phase 实现 + 验收）**：
  - `owner_id` 或 `member_ids` 命中 `request.user` → 该项目**成员**，可访问项目内资源；
  - 未命中 → **非成员**，对该项目一切访问 → 403。
- **不在本 Phase 实现的（待 P0-5）**：
  - owner 与 member 之间的权限差异（如：删除文件 / 管理成员 / 归档 是否仅 owner）；
  - 是否存在跨项目的全局管理员角色；
  - 只读（viewer）类角色。
  - 以上统一标「待 P0-5」，执行者遇到时按最小边界（成员均可）处理，并在代码/接口标注 `TODO(P0-5)`，**不自行拍角色差异**。

### 3.3 访问控制点（AccessControl 服务）

- 新增 `AccessControl`（方案 line 672/705「`assert_project_access` / `AccessControlService`」）：
  - `is_member(project_id, user) → bool`：`owner_id == user` 或 `member_ids` 含 `user`。
  - `assert_project_access(project_id, user)`：非成员 → 抛 403（路由层统一调用）。
- 三类资源在路由层校验：文件（P6-3）/ data_* 行（P6-4）/ trace（P6-5）；非成员 → 403。
- 与 Phase 5 `DataService` 叠加：`DataService` 保证数据层 project_id 隔离，`AccessControl` 保证用户层成员授权。
- **不实现 `is_admin` / 全局角色**（待 P0-5）。

### 3.4 操作留痕（audit_logger 扩事件）

- `audit_logs` **不加列**（现有结构够），丰富 `log_type`/`action` 语义值：
  - `log_type ∈ {request, operation, llm_call, db_write, trace, stage_change, alert}`（新增 `stage_change`/`alert`）。
  - `action` 细化：`project_create`/`stage_advance`/`upload`/`parse_done`/`file_delete`/`member_change`/`archive` 等。
  - `detail` JSON 存前后值（阶段变更的前后 setup_stage、成员变更前后）。

### 3.5 监控 / 告警 / 归档

- **任务监控**（P6-8）：读 `audit_task_queue` 看板（pending/processing/failed 计数 + 历时）。
- **任务超时回收**（P6-8）：`processing` 超过阈值（借本看板 OntoSKU 真实耗时数据联调定，**不预设**）→ 视作僵死回 `pending` 重跑（从 Phase 3 挪入，属运行保障）。
- **告警**（P6-9）：Phase 3 重试耗尽（failed）→ 写 `audit_logs`(log_type=alert) + 看板标红；本轮**最小告警**（日志 + 看板），外部通知（邮件/IM）后续。
- **归档**（P6-10）：`audit_logs` 按保留期清理（默认 1 年可配）；项目 `status=archived` 归档（数据保留、接口只读）。

## 4. 任务清单（P6-1 .. P6-10，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P6-1 | 项目成员关系 | audit_projects 无 owner/member 列 | `owner_id`/`member_ids` 落库 + 成员管理接口（方案 DDL ③） |
| P6-2 | 项目成员权限 | 无鉴权 | `X-User` middleware + `AccessControl` + **成员可见/非成员全拒**生效；**角色细分待 P0-5** |
| P6-3 | 文件访问权限 | 无校验 | upload/download/delete/files 非成员 → 403 |
| P6-4 | 数据行访问权限 | Phase 5 数据层 project_id | data_* 接口叠加成员校验，非成员 → 403 |
| P6-5 | trace 访问权限 | 无校验 | traces 接口按项目归属校验，非成员 → 403 |
| P6-6 | 操作日志 | audit_logs 自动写（db_write） | 语义化事件类型（operation/stage_change/...） |
| P6-7 | 阶段变更日志 | — | `setup_stage` 流转写 `stage_change`（前后值留痕） |
| P6-8 | OCR 任务监控与超时回收 | Phase 3 task_queue | 项目级任务看板接口（状态计数 + 历时 + 失败列表）+ processing 超时回收（阈值联调定）；全局看板访问控制待 P0-5 |
| P6-9 | 失败重试和告警 | Phase 3 重试 | 重试耗尽 → alert 日志 + 看板标红（本轮最小通知） |
| P6-10 | 数据归档和保留策略 | — | `audit_logs` 保留期清理 + 项目 `archived` 归档（只读） |

**涉及文件**：`backend/middleware/auth.py`（新增，X-User）、`backend/services/access_control.py`（新增，成员判定 + assert_project_access）、`backend/services/audit_logger.py`（扩事件）、`backend/routes/audit_routes.py`（文件/data/trace 接口加成员校验 + 阶段变更日志 + 监控/归档接口）、`backend/data/migrations/M006_*`（owner_id/member_ids）。

## 5. 本 Phase DDL（M006，幂等，单独 commit）

```sql
-- ③ audit_projects 权限与成员（方案 DDL ③，Phase 6；严格对齐方案，不加额外列/索引）
ALTER TABLE tt.audit_projects
  ADD COLUMN owner_id   VARCHAR(64) NULL COMMENT '项目负责人账号',
  ADD COLUMN member_ids JSON        NULL COMMENT '项目组成员账号列表';

-- 回滚（开发期用）
-- ALTER TABLE tt.audit_projects
--   DROP COLUMN member_ids, DROP COLUMN owner_id;
```

> `audit_logs` **不加列**（`migrate_logs.sql` 现有结构足够，P6-6 只丰富 log_type/action 值）。
> DDL 严格对齐方案 line 491-494，不再额外加索引/角色列；如 P0-5 后需补，另起迁移。

## 6. 本 Phase 接口契约（完整，直接对照实现）

### 6.1 项目成员关系（P6-1）

- `owner_id`/`member_ids` 落 `audit_projects`（M006）；创建项目时 `owner_id = X-User`。
- 成员管理：`POST /projects/{id}/members` 修改 `member_ids`。
  - **谁能改成员**属角色细分，待 P0-5；本 Phase 暂定**仅 owner 可改**（非 owner → 403），代码标 `TODO(P0-5)`。

### 6.2 伪鉴权 + 成员校验（P6-2）

- middleware 提取 `X-User` → `request.user`；`AccessControl.is_member` / `assert_project_access`。
- **全局管理员角色已确认要有**（用户定），本轮不写实现代码——实现细节/时机待 P0-5 后续调整；任何「跨项目/全局」权限需求标 `TODO(P0-5)`，不臆造角色实现（含 Config.ADMINS 等具体方式均待 P0-5）。

### 6.3 文件访问权限（P6-3）

- `upload`/`download`/`delete`/`files`/`workspace/tree` 加 `AccessControl.assert_project_access(project_id, request.user)`；非成员 → 403。

### 6.4 数据行访问权限（P6-4）

- data_* 接口（Phase 5 的 `/projects/<id>/data/*`）在路由层叠加成员校验；`DataService` 已强制 project_id，本 Phase 再校验 `request.user` 是成员。

### 6.5 trace 访问权限（P6-5）

- `GET /traces/{result_type}/{result_id}`：从结果反查 project_id（data_row→table+row_id→project_id；document→trace→project_id），校验成员；非成员 → 403。

### 6.6 操作日志（P6-6）

- `audit_logger` 封装语义化写入：`log_operation(user, action, target_type, target_id, detail)`；关键动作（upload/parse/delete/member_change）显式记 `operation`，不再只靠 db_write 自动日志。

### 6.7 阶段变更日志（P6-7）

- `setup_stage` 每次推进（basic→target_scope→items→workspace）写 `stage_change`：`detail={from, to, missing_fields_before}`；finalize/归档同样留痕。

### 6.8 OCR 任务监控（P6-8）

- `GET /projects/{id}/tasks`（项目成员）：返回该项目 task_queue 状态计数（pending/processing/completed/failed）、历时、失败列表。
- processing 超时回收：巡检/重启时把超阈值（联调定）的 `processing` 任务回 `pending`（从 Phase 3 挪入的运行保障项）。
- 跨项目全局任务看板的访问控制（谁可看全部项目任务）**待 P0-5**；本 Phase 先提供项目级看板。

### 6.9 失败重试和告警（P6-9）

- Phase 3 重试耗尽（`retry_count ≥ max_retries` → failed）时：写 `audit_logs`(log_type=`alert`, action=`task_failed_exhausted`, detail=task)；看板该任务标红。
- 本轮最小通知（日志 + 看板）；外部通道（邮件/IM）后续。

### 6.10 数据归档和保留策略（P6-10）

- `audit_logs` 保留期清理：定时任务删 `created_at < now - N 天`（默认 365，可配）。
- 日志查询接口 `GET /projects/{id}/logs`（项目成员，按 target_id 等过滤）；跨项目日志查询访问控制待 P0-5。
- 项目归档：`POST /projects/{id}/archive` → `status='archived'`；归档项目接口只读、不接收写入（upload/分析等 → 409）。**谁能归档**待 P0-5，本 Phase 暂定 owner（标 `TODO(P0-5)`）。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| 系统无登录 | §3.1 `X-User` 伪鉴权；真实登录上线前接 |
| `audit_logs` 自动写所有 db_write（容量 57247 行） | P6-10 保留期清理；语义化日志只补关键 operation，不放大自动日志 |
| `audit_logs` DDL 在 `migrate_logs.sql` 不在 schema.sql | K1 已 diff；本 Phase 不改其结构，只丰富 log_type/action |
| 数据层 Phase 5 已隔离 project_id，易误以为已鉴权 | §3.3 明确两层：DataService=数据隔离，AccessControl=用户授权，缺一不可 |
| `X-User` 可伪造（无真实认证） | 信任内网网关注入；上线前必须接真实认证；本 Phase 标注"伪鉴权，非生产安全边界" |
| **角色细分未定（P0-5）** | 本 Phase 只做成员可见/非成员拒；owner/member 差异、全局管理员一律 `TODO(P0-5)`，不臆测 |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：项目 $PID，owner=alice，member=bob；非成员=carol
# 注：owner/member 间权限差异不在本轮验收（待 P0-5）；本轮验「非成员全拒」

# P6-1 成员关系
curl -s -X POST $BASE/projects/$PID/members -H "X-User: alice" -H "Content-Type: application/json" -d '{"members":["bob"]}' | python -m json.tool
# 断言：member_ids 含 bob

# P6-2/P6-3 文件访问权限（非成员被拒）
curl -s "$BASE/projects/$PID/files" -H "X-User: carol" | python -m json.tool
# 断言：403（carol 非成员）；alice/bob → 200（成员可见，owner/member 细分待 P0-5）

# P6-4 数据行访问权限
curl -s "$BASE/projects/$PID/data/data_contracts/rows" -H "X-User: carol" | python -m json.tool
# 断言：403

# P6-5 trace 访问权限
curl -s "$BASE/traces/data_row/<row_id>?table=data_contracts" -H "X-User: carol" | python -m json.tool
# 断言：403（row 属 $PID，carol 非成员）

# P6-6/P6-7 操作日志 + 阶段变更（项目成员可见）
curl -s "$BASE/projects/$PID/logs?target_type=project&target_id=$PID" -H "X-User: alice" | python -m json.tool
# 断言：含 stage_change（前后 setup_stage）、operation（upload 等）

# P6-8 任务监控（项目级）
curl -s "$BASE/projects/$PID/tasks" -H "X-User: alice" | python -m json.tool
# 断言：返回状态计数 + 失败列表

# P6-9 告警（构造任务重试耗尽）
# 断言：audit_logs 含 log_type=alert, action=task_failed_exhausted；看板标红

# P6-10 归档（owner；谁能归档待 P0-5，本轮暂定 owner）
curl -s -X POST $BASE/projects/$PID/archive -H "X-User: alice" | python -m json.tool
curl -s -X POST $BASE/projects/$PID/upload -H "X-User: alice" -F "file=@x.pdf"
# 断言：status=archived；归档后 upload → 409
```

> 仿 `test_p1_flow.py` 写 `backend/tests/test_p6_auth.py`，覆盖 P6-2/3/4/5 的断言（核心：**非成员 → 403**）；owner vs member 细分用例标 `TODO(P0-5)` 暂不写。

## 9. 完成标准（汇总）

- [ ] 数据库 `M006` 迁移执行成功（owner_id/member_ids，严格对齐方案 ③），可回滚
- [ ] `X-User` 伪鉴权 + `AccessControl` **成员可见/非成员全拒**生效（P6-1/P6-2）
- [ ] 文件/数据行/trace 接口非成员 → 403（P6-3/P6-4/P6-5）
- [ ] 语义化操作日志 + 阶段变更留痕（P6-6/P6-7）
- [ ] OCR 任务监控看板（项目级）+ processing 超时回收（阈值联调定）（P6-8）
- [ ] 失败重试告警（alert 日志 + 看板标红）（P6-9）
- [ ] 归档保留策略（audit_logs 保留期 + 项目 archived 只读）（P6-10）
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_6.md`）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1-5 行为未破坏）
- [ ] **角色细分（owner/member 差异、全局管理员）以 `TODO(P0-5)` 留痕，待 P0-5 定稿后回头补 P6-2**
