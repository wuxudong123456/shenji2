# PHASE_1 执行包：项目生命周期

> **执行协议**：本文件是 Phase 1 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 0 已完成（勘察 K1-K6 + 决策 D4/D5/D8 已确认）。
> 铁律：不改变现有前端视觉；不改变已有字段定义；旧接口路径保持兼容。

---

## 0. 执行者须知（先读）

- **只做本 Phase 的事**：不做资料空间目录结构（Phase 2）、不做 OCR 溯源（Phase 3/4）、不碰智能分析（Phase 8）。
- **小功能切片**：按第 4 节 P1-1..P1-10 逐个开发，**每个小功能测试通过后才进入下一个**。
- **数据库变更单独 commit**，每张表配回滚语句（见第 5 节，走 `backend/data/migrations/` 幂等模板）。
- **不破坏现有接口**：`POST /projects` 响应结构保持，忽略越阶段字段不报错。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 1 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 0 勘察 K1（DB diff） | ✅ | `audit_projects` 已有 P1.2 全字段，无 setup_stage（需迁移 M001） |
| Phase 0 勘察 K3（双项目盘点） | ✅ | 3 个孤儿 bucket + 3 个 MinIO-only 旧文件夹已按 P1-10 处理 |
| 决策 4（补持久化） | ✅ 已确认 | **除报告文号外全部纳入**：`target-scope` 落 scope + target_unit + extend_unit + focus；报告文号留文书阶段 |
| 决策 5（旧项目阶段推断） | ✅ 已确认 | 允许自动推断 + 人工批量确认（P1-10） |
| 决策 13（业务发生时间） | ⚠️ 方案A 已确认 | 必填性待定；本 Phase 先实现字段与弱校验 |

## 2. 目标

严格四阶段受控流程：`审计立项 → 对象和范围 → 审计事项 → 创建资料空间`。
任何客户端（前端、curl、其他程序）都不能跳过前序阶段。**立项阶段完成后，数据库无审计事项、无项目 bucket。**

## 3. 项目状态机（核心规则）

两个字段职责分离，**"active" 只在 status 出现一次**：

| 字段 | 语义 | 取值 | 变化时机 |
|---|---|---|---|
| `setup_stage` | 立项流程进度 | `basic → target_scope → items → workspace` | 阶段推进时变，**不含 active** |
| `status` | 项目生命周期 | `draft / active / completed / archived` | workspace 完成 → draft 转 active |

**阶段准入矩阵**（后端强制；前端只按返回的 `allowed_actions` 切换 Tab/按钮）：

| 当前 setup_stage | allowed_actions（可执行） | 不可执行 |
|---|---|---|
| basic | `save_basic` | target_scope / items / finalize / upload / analysis |
| target_scope | `save_basic, save_target_scope` | items / finalize / upload / analysis |
| items | `save_basic, save_target_scope, save_items, split_items` | finalize / upload / analysis |
| workspace | `save_basic, finalize, upload, analysis` | — |

规则：
- 阶段只允许向后推进，不允许回退（回退走管理操作，另行审批）。
- 每个动作返回 `{setup_stage, allowed_actions, missing_fields}`，前端据此禁用/启用。
- `finalize` 幂等：重复调用不创建第二个 bucket。
- **推进 vs 越阶段**：target-scope / items 接口为"推进+编辑"二合一——在更低阶段首次合法调用即推进到该阶段（非 409）。"越阶段拒绝"指**跳过中间阶段**直接调更靠后的接口：basic 直接 `save_items`（PUT /items 前置校验拦截→409）、basic 直接 `finalize`/`upload`/`analysis`（→409）。`finalize` 另由 `check_stage` 兜底校验前置必填（scope 等），即使 `setup_stage` 被绕过推进也拒绝。

## 4. 任务清单（P1-1 .. P1-10，逐个测试）

| # | 小功能 | 完成标准 |
|---|---|---|
| P1-1 | 创建项目只保存基础信息 | 立项后 DB 无事项、无 bucket；含 business_start/end_date |
| P1-2 | 项目默认保持 draft | status=draft，不建 bucket |
| P1-3 | 第一阶段字段白名单 | 越阶段字段被忽略/拒绝（scope/target_unit/items 不在 basic 白名单） |
| P1-4 | 保存审计对象和范围 | scope 必填；target_unit/extend_unit/focus 持久化（决策 4）；setup_stage→target_scope |
| P1-5 | 阶段完整性检查 | 前序未完成返回 409 + missing_fields |
| P1-6 | 审计事项人工增删改 | 页面可增删改事项 |
| P1-7 | 审计事项保存 | PUT /items 落库，乐观锁防覆盖（update_time 校验） |
| P1-8 | 项目空间 finalize | 四阶段完整后建空间、status→active、workspace_created_at |
| P1-9 | 重复 finalize 幂等 | 重复调用结果一致，不重复建 bucket |
| P1-10 | 旧项目兼容 + 阶段推断迁移 | 旧接口/旧项目仍可读；存量项目按推断规则补 setup_stage（决策 5）：有对象/范围→target_scope，有 audit_items→items，有 bucket→workspace；推断结果人工批量确认 |

**前端同步（随 P1-1/P1-4 一起）**：Tab/按钮按 `setup_stage/allowed_actions` 切换；保存按当前 Tab 调阶段接口；立项表单新增「业务发生期间」起止日期（方案A，弱校验起≤止）。

**涉及文件**：`backend/services/project_lifecycle.py`（新增）、`backend/routes/audit_routes.py`、`frontend/projects.html`、`frontend/js/api.js`。

## 5. 本 Phase DDL（完整，直接执行，走 migrations/M001）

```sql
-- ① 流程控制字段（setup_stage 不含 active，见第 3 节；主方案 6.1 ① 的旧注释以本文件为准）
ALTER TABLE tt.audit_projects
  ADD COLUMN setup_stage     VARCHAR(20) DEFAULT 'basic' COMMENT 'basic/target_scope/items/workspace',
  ADD COLUMN workspace_created_at DATETIME NULL COMMENT '资料空间创建时间';

-- ② 业务发生时间（决策 13 方案A）
ALTER TABLE tt.audit_projects
  ADD COLUMN business_start_date DATE NULL COMMENT '被审计业务实际发生起始时间',
  ADD COLUMN business_end_date   DATE NULL COMMENT '被审计业务实际发生结束时间';

-- ③ 补持久化字段（决策 4 确认，除报告文号外全部纳入）
ALTER TABLE tt.audit_projects
  ADD COLUMN start_date    DATE NULL COMMENT '项目开始日期',
  ADD COLUMN entry_date    DATE NULL COMMENT '审计进点日期',
  ADD COLUMN extend_unit   VARCHAR(500) NULL COMMENT '延伸审计单位',
  ADD COLUMN audit_focus   JSON NULL COMMENT '审计重点标签列表';

-- 回滚（单独文件，开发期用）
-- ALTER TABLE tt.audit_projects
--   DROP COLUMN setup_stage, DROP COLUMN workspace_created_at,
--   DROP COLUMN business_start_date, DROP COLUMN business_end_date,
--   DROP COLUMN start_date, DROP COLUMN entry_date,
--   DROP COLUMN extend_unit, DROP COLUMN audit_focus;
```

执行方式：独立脚本 `backend/data/migrations/M001_phase1_project_lifecycle.sql`，幂等（存储过程判断列是否存在），**单独 commit**。**注意**：K1 实测 `audit_projects` 已有 P1.2 全字段，本迁移只加 ①③ 的增量列，不重复建已有列。

## 6. 本 Phase 接口契约（完整，直接对照实现）

**统一响应包装**：项目类接口返回 `{success, project:{...原字段, setup_stage, allowed_actions, missing_fields}}`；`_project_to_dto` 保留旧别名（title/unit/domain/level）不变。

### 6.1 `PUT /api/audit/projects/{id}/basic`（新增）

请求（基础字段白名单，**不含** scope/target_unit/items/focus）：
```json
{
  "name": "某市教育局2026年度预算执行审计",
  "project_code": "审通〔2026〕001号",
  "audit_type": "预算执行审计",
  "audit_method": "就地审计",
  "target_level": "市级",
  "audited_unit": "某市教育局",
  "objective": "揭示预算执行与采购合规问题",
  "audit_period": "2026-01-01至2026-06-30",
  "amount": 500,
  "business_start_date": "2026-01-01",
  "business_end_date": "2026-06-30"
}
```
校验：`name` 必填；白名单外字段忽略（兼容旧前端）。响应：`{success, project}`。

### 6.2 `PUT /api/audit/projects/{id}/target-scope`（新增）

请求：
```json
{ "scope": "2026年上半年预算执行及采购合规", "target_unit": "某市教育局", "extend_unit": "下属5家二级单位", "audit_focus": ["采购程序合规性", "资金使用合规性"] }
```
校验：项目存在且 `setup_stage ∈ {basic, target_scope}`（basic 首次调用即**推进**到 target_scope，非拒绝——这是推进+编辑二合一接口，见 P1-4/§3 规则）；`scope` 必填；target_unit/extend_unit/focus 均持久化（决策 4）。响应：`{success, project}`，`setup_stage=target_scope`。

### 6.3 `POST /api/audit/projects/{id}/workspace/finalize`（新增）

请求：`{}`。校验：`setup_stage=items` 且 ≥1 项已确认事项。响应：`{success, project:{status:'active', minio_bucket, workspace_created_at}}`。**幂等**：重复调用返回已有 bucket。

### 6.4 修改：`POST /api/audit/projects`

- 响应字段保持不变（兼容旧前端），但内部只保存基础白名单、`status='draft'`、**不建 bucket**。
- 请求含 scope/items 时忽略（不报错）。

### 6.5 修改：`POST /api/audit/projects/extract-info`

- LLM 提示词去掉 target_unit/extend_unit/scope/audit_items 字段，只返回基础信息 + 业务期间（若文本可提取）。

### 6.6 修改：`POST /api/audit/projects/split-audit-items`

- 请求增加 `project_id`；从 DB 读项目（校验落库 + `setup_stage ≥ target_scope`）；未通过返回 409。
- 项目上下文来自 DB，不接受前端自由文本伪造完整上下文。

### 6.7 新增：`POST /api/audit/projects/migrate-stages`（P1-10）

请求：`{}`（管理员）。行为：按推断规则扫描存量项目补 setup_stage，返回待人工确认清单；确认接口 `POST /api/audit/projects/migrate-stages/confirm` 批量落库。校验：仅管理员可调用。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| 系统并存两套项目（MinIO 文件夹 / MySQL） | 本 Phase 只处理 MySQL 项目；MinIO-only 已在 Phase 0 盘点，P1-10 按 K3 结果导入或冻结 |
| 旧前端 `Proj.save()` 一次提交全字段 | `POST /projects` 与 `PUT /basic` 忽略越阶段字段（兼容灰度），不报错 |
| 单一「保存项目」按钮无分阶段语义 | 按钮按当前 Tab 调对应阶段接口，disabled 由 allowed_actions 控制 |
| `audit_period` 为拼接字符串 | 本 Phase 不改，年度派生在 Phase 2 |
| 存量项目无 setup_stage（K1 实测 19 个） | P1-10 推断 + 人工批量确认（决策 5） |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit

# ① 创建项目（只带基础字段）→ 期望 setup_stage=basic, status=draft
curl -s -X POST $BASE/projects -H "Content-Type: application/json" -d '{
  "name":"测试教育局2026预算执行审计","audit_type":"预算执行审计",
  "audit_period":"2026-01-01至2026-06-30","audited_unit":"某市教育局",
  "amount":500,"business_start_date":"2026-01-01","business_end_date":"2026-06-30"}' | python -m json.tool
# 断言：project.setup_stage == "basic" && project.status == "draft"

# ② 越阶段保存审计事项（basic 阶段不允许 save_items）→ 期望 409
#    注：target-scope 是"推进+编辑"语义——basic 首次调用即推进到 target_scope（非 409，见 §6.2/§3 规则）。
#    真正的"越阶段拒绝"指跳过中间阶段直接调更靠后的接口（basic 直接 save_items/finalize/upload/analysis）。
curl -s -X PUT $BASE/projects/<PID>/items -H "Content-Type: application/json" \
  -d '{"audit_items":[{"title":"不应在立项阶段写入"}]}' | python -m json.tool

# ③ 保存基础信息 → setup_stage 仍 basic
curl -s -X PUT $BASE/projects/<PID>/basic -H "Content-Type: application/json" \
  -d '{"objective":"测试目标"}' | python -m json.tool

# ④ 推进对象范围（含 extend_unit/focus，决策4）→ setup_stage=target_scope
curl -s -X PUT $BASE/projects/<PID>/target-scope -H "Content-Type: application/json" \
  -d '{"scope":"2026年上半年预算执行及采购合规","extend_unit":"下属5家二级单位","audit_focus":["采购程序合规性"]}' | python -m json.tool

# ⑤ 拆事项（带 project_id）→ 200 返回事项
curl -s -X POST $BASE/projects/split-audit-items -H "Content-Type: application/json" \
  -d '{"project_id":"<PID>","project_name":"测试教育局2026预算执行审计"}' | python -m json.tool

# ⑥ 保存事项 → 至少1项
curl -s -X PUT $BASE/projects/<PID>/items -H "Content-Type: application/json" \
  -d '{"audit_items":[{"title":"采购方式合规性审计","priority":"高"}]}' | python -m json.tool

# ⑦ finalize → status=active, 有 minio_bucket；重复调用不重建
curl -s -X POST $BASE/projects/<PID>/workspace/finalize -H "Content-Type: application/json" -d '{}' | python -m json.tool
curl -s -X POST $BASE/projects/<PID>/workspace/finalize -H "Content-Type: application/json" -d '{}' | python -m json.tool

# ⑧ 新建项目直接 finalize → 409（前置阶段未完成）

# ⑨ 阶段推断迁移（P1-10）→ 返回存量项目推断清单
curl -s -X POST $BASE/projects/migrate-stages -H "Content-Type: application/json" -d '{}' | python -m json.tool
```

## 9. 完成标准（汇总）

- [ ] 数据库 `M001` 迁移执行成功，可回滚
- [ ] 阶段 1 完成后 DB 无 audit_items、无 bucket
- [ ] 越阶段调用全部 409
- [ ] P1-10 存量项目阶段推断清单正确，人工确认后落库
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_1.md`）
- [ ] `05-regression-baseline.md` 回归通过（旧接口未破坏）
