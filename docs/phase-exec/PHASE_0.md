# PHASE_0 执行包：口径、契约和测试基础

> **执行协议**：本文件是 Phase 0 的**唯一执行依据**。只读本文件，不要读主方案全文。
> 铁律：本 Phase **不开发任何业务功能**，只做口径确认、契约冻结、勘察、测试基础。
> 验收门：所有关键业务字段和状态无歧义；勘察六项完成并有书面结论；决策 1-13 有明确答案（含"暂缓"）。

---

## 0. 执行者须知

- 只做本 Phase 的事：不写业务代码，不建业务表（除测试脚手架所需的表）。
- 勘察项结果写入 `docs/phase-exec/PHASE_0.md` 的"勘察结果"节（本文件即记录载体）。
- 需要外部服务的勘察项，无法访问时**明确标记 BLOCKED**，不伪造数据。
- 产出决策单供领导勾选（见 `docs/决策单.md`）。

## 1. 目标

消除所有关键业务字段和状态歧义，冻结智能分析契约（不实现），建立测试跑道，完成代码层勘察。Phase 0 完成即可安全开工 Phase 1。

## 2. 小功能清单（P0-1 .. P0-9）

| # | 小功能 | 做什么 | 产出 | 完成标准 |
|---|---|---|---|---|
| P0-1 | 字段盘点 | 盘点 `audit_projects` 现有字段 + 前端表单字段，映射到字段—阶段—表 | 字段映射表（已确认版） | 每个业务含义有唯一落点，无歧义 |
| P0-2 | 项目状态定义 | 定 `setup_stage`/`status` 职责（见主方案 4.1） | 状态机文档 | "active" 只在 status 出现 |
| P0-3 | 审计年度计算规则 | 从 `audit_period` 派生 `audit_year`（决策 12） | 年度派生规则 | 统一取第一个年份 + 兜底规则 |
| P0-4 | 审计对象字段关系 | 定 `f-target-unit`/`f-unit` 关系（决策 3/4） | 对象字段映射 | 对象/延伸/范围字段唯一落点 |
| P0-5 | 权限矩阵确认 | 定角色/作用域/权限（决策 + 用户输入） | 用户—项目—角色矩阵 | 矩阵逐格明确 |
| P0-6 | 数据表映射确认 | 六类表 + 采购/访谈（决策 8） | 数据工坊表清单 | 每类数据有落点 |
| P0-7 | 证据引用契约确认 | 定 `audit_source_refs`/`audit_document_chunks`/`audit_field_sources` 字段 | 证据契约文档 | 契约无歧义 |
| P0-8 | 数据库迁移和回滚规则 | ✅ 幂等迁移文件模板 + 每表回滚 | `backend/data/migrations/README.md`（含幂等/回滚模板） | 新表可迁移可回滚 |
| P0-9 | 测试基础建立 | ✅ 测试脚手架 + 回归基线 | `backend/tests/smoke_test.py` + `dev-specs/05-regression-baseline.md` | **冒烟脚本跑通 7/7**（2026-08-06 实测）；模板接口已知延迟已记录 |

## 3. 勘察六项（处置 F1-F5 的依据）

| # | 勘察项 | 状态 | 结果 |
|---|---|---|---|
| K1 | DB diff（F2） | ✅ 完成（2026-08-06） | 见下：schema.sql 严重滞后，`audit_agent_traces` 真实库不存在 |
| K2 | OntoSKU 探活快照 | ✅ 完成（2026-08-06） | 用户提供真实解析样例，已固化进 `dev-specs/06-ontosku-api-snapshot.md`。剩余：真实 `chunks.json` 原始结构待补（见快照 §4，不阻塞 Phase 3 开工） |
| K3 | 双项目盘点（F1） | ✅ 完成（2026-08-06） | 见下：19 MySQL 项目 / 22 bucket / 3 个 MinIO-only 旧文件夹 |
| K4 | localStorage 数据源盘点 | ✅ 完成（2026-08-06） | 见下 |
| K5 | 字段映射缺口 | ✅ 完成（抽样，2026-08-06） | 见下 |
| K6 | agent trace 落库确认（F3） | ✅ 完成 | 见下 |

### 勘察结果 K4：localStorage 数据源盘点

前端共 6 个 key，分两类：

| key | 使用位置 | 内容 | 分类 | 处置 |
|---|---|---|---|---|
| `aw_project_memory` | analysis-wiz.js / analysis.js / portal.js | 项目背景（title/domain/unit/objective...） | **业务状态** | Phase 8 迁移为后端 `project_context` |
| `aw_analysis_progress` | analysis-wiz.js | 分析进度（chatHTML/rightPanelHTML + step） | **业务状态** | Phase 8 迁移为后端 `audit_analysis_tasks`；**当前是聊天污染源** |
| `aw_sidebar_collapsed` | app.js / portal.js | 侧边栏折叠 | 纯 UI | 保留 |
| `aw_doc_status_visible` | portal.js | 文档状态可见性 | 纯 UI | 保留 |
| `aw_bg_tasks` | app.js | 后台任务缓存 | 纯 UI | 保留 |
| `aw_guide_shown` | app.js | 新手引导标记 | 纯 UI | 保留 |

**结论**：2 个业务状态 key 需在 Phase 8 移除/迁移；4 个纯 UI key 保留不入业务。

### 勘察结果 K1：DB diff（2026-08-06 实测）

真实端点：MySQL `192.168.3.164:3306` / MinIO `192.168.3.164:9100`（非 127.0.0.1）/ OntoSKU `192.168.3.189:5005`。

tt 库实际 23 张表（含 5 张 schema.sql 未收录的新表）：

| 表 | 行数 | 与 schema.sql |
|---|---|---|
| audit_logs | 57,247 | ❌ 不在 schema（在 migrate_logs.sql），**F2 坐实** |
| audit_task_queue / audit_task_operations | 10 / 0 | ❌ 不在 schema（任务系统） |
| audit_expression_sql | 2 | ❌ 不在 schema（SQL 人工确认） |
| audit_generated_documents | 0 | ❌ 不在 schema（文书生成） |
| audit_items | 0 | 路由启动兜底建表 |
| audit_agent_traces | **不存在** | **schema.sql 定义了但真实库从未建表 → F3 强化** |

**关键列核对：**
- `audit_projects` 已有 P1.2 全字段（project_code/audited_unit/audit_type/target_level/objective/scope/amount…）→ Phase 1 直接在这些列上做白名单，无需建列。
- `audit_analysis_tasks` 已有 `task_code/session_id/execution_mode/current_step/next_action/error_code/error_message/confirmed_at/completed_at/active_key` 等 20+ 列，**远超 schema.sql** → F2 坐实，Phase 8 的 analysis_task 增量列基于真实列。
- `audit_document_traces` 有 position_anchor/ontosku_template/extracted_fields；无 external_document_id 等（Phase 3 增列）。

**影响**：schema.sql 仅作参考；所有新表/新列以真实库为准走 `backend/data/migrations/`（P0-8 已建）。

### 勘察结果 K3：双项目盘点（2026-08-06 实测）

| 系统 | 数量 | 明细 |
|---|---|---|
| MySQL 项目（deleted=0） | 19 | 含测试脏数据：`<script>alert('xss')</script>`、`??????` 乱码、creator 为空多条 |
| 新系统 bucket（`audit-project-*`） | 22 | **比 MySQL 多 3 个 → 3 个孤儿 bucket**（无项目记录） |
| 旧系统 MinIO 文件夹（仅旧 `/api/projects`） | 4 | `00917be56d5d`（有 MySQL 记录）、`<img src=x onerror=alert(1)>`、`TestProject2026`、`教育局2026采购审计` —— 后 3 个 **无 MySQL 记录，需导入或冻结** |
| 其他 bucket | 3 | `audit-materials` / `knowhere-results` / `knowhere-uploads`（系统桶，非项目） |

**结论（F1 具体化）**：3 个孤儿 bucket + 3 个 MinIO-only 旧文件夹需 Phase 0 决策（导入/冻结/清理）；MySQL 测试脏数据需清理策略（或保留仅测试）。→ 列入 P0 收尾与决策单补充项。

### 勘察结果 K5：字段映射缺口（抽样 2 个模板）

`field_mapper.FIELD_ALIAS_MAP` 六张表共约 100 个别名，而模板库有 **1511 个文件**。抽样两个：

| 模板 | 输出字段数 | 可映射到列 | 落 `extra_fields` | 缺口率 |
|---|---|---|---|---|
| 合同协议类/买卖合同 | 22 | ~9（甲方名称/乙方名称/合同编号/金额/币种/签订/生效/终止/履约期限） | ~13（买方信息/产权取得日期/合同名称/合同类型/丙方名称/合同标的/履约地点/付款方式/违约责任/争议解决方式/签订地点/联系电话/统一社会信用代码） | ~59% |
| 业务单据类/报销单 | 26 | ~4（摘要/标题/经办人/日期） | ~22（报销金额/会议名称/票据编号/开具单位/涉及金额 等） | ~85% |

**结论与影响**：
1. 缺口率 59%-85%，**绝大多数模板字段落 `extra_fields` JSON**——部分是设计使然（文档特有字段），部分是可补别名（如"合同名称/付款方式"这类通用字段应进别名表）。
2. **Phase 4 的 `audit_field_sources` 必须对 extra_fields 字段也建溯源**（`extra_fields->'$.字段名'`），不能只覆盖已映射列——否则 60-85% 的字段无法溯源。
3. **Phase 7 的字段映射**需补"按模板 output.fields 自动扩展别名表"，不纯手工维护。
4. 建议 Phase 0 补一个**别名表扩充脚本**（P0-9 范围内或 Phase 7 前置），把高频通用字段（合同名称/付款方式/经办人/摘要 等）补进别名表，降低 extra_fields 比例。

### 勘察结果 K6：agent trace 落库确认（F3）

grep 全 backend：`audit_agent_traces` 表仅 schema.sql 定义，**无任何 Python 代码写入**。BaseAgent 的 trace_id/source_knowledge/tool_call_records 只存在于返回值内存。→ Phase 8 首项补 `BaseAgent._persist_trace()`。

## 4. 决策依赖（13 项，见 `docs/决策单.md`）

| 决策 | 阻塞 | 建议 | 状态 |
|---|---|---|---|
| D4 延伸单位/报告文号/开始/进点/审计重点补持久化 | Phase 1（target-scope 字段） | 除报告文号外全部纳入；报告文号留文书阶段 | ✅ 已确认 |
| D5 旧项目阶段推断迁移 | Phase 1（P1-10） | 允许自动推断 + 人工批量确认 | ✅ 已确认 |
| D8 采购/访谈表 | Phase 5（六类表映射） | 新增 `data_procurements`/`data_interviews` 骨架表 | ✅ 已确认 |
| D13 业务发生时间必填 | Phase 1（P1 前端字段） | 非必填 | ⚠️ 方案A 已确认，必填性待定 |
| 其余 D1/2/3/6/7/9/10/11/12 | 各对应 Phase | 见决策单建议 | ⏳ 待领导 |

> 硬阻塞：D4、D5、D8、D13 不确认，Phase 1/5 无法开工。其余可先按建议默认，领导后补。

## 5. 验收门

- [ ] P0-1..P0-9 产出物齐全（决策单有明确答案）
- [ ] 勘察 K1/K2/K3 完成（或明确 BLOCKED 并记录原因）
- [ ] K4/K6 结果已记录（本文件）
- [ ] 测试脚手架跑通（冒烟 + 回归基线）
- [ ] 智能分析契约冻结（附录A + `dev-specs/04`），本轮不实现

## 6. 已知坑

| 坑 | 对策 |
|---|---|
| 决策未定 → 开发卡死 | 硬阻塞 D4/D5/D8 已确认、D13 方案A 已确认（必填性待定）；其余 D1/2/3/6/7/9/10/11/12 可按建议默认推进 |
| DB/MinIO/OntoSKU 内网不可达 | 勘察项 BLOCKED 标记，不伪造；内网可用后再补 |
| `schema.sql` 非权威（F2） | K1 DB diff 后以实际库为准，统一幂等迁移 |
| 双项目并存（F1） | K3 盘点差集，决定导入或冻结 |
