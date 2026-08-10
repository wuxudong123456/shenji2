# PHASE_9 执行包：端到端验收与上线

> **执行协议**：本文件是 Phase 9 的**唯一执行依据**。执行者只读本文件。
> 前置状态：Phase 1-8 已全部实现并通过各自阶段验收门。
> 铁律：**全链路垂直切片一气呵成才算通过**（立项 → 对象范围 → 审计事项 → 创建空间 → 上传 → OCR → 结构化 → 溯源 → 数据分析 → 疑点核实 → 文书）；各拦截门该拦就拦、可恢复、不破坏旧接口、可回滚。本 Phase **不写新业务功能**，只做端到端验收 + 上线准备。

---

## 0. 执行者须知（先读）

- **关键认知：本 Phase 是验收 + 上线，不是开发**：
  - 业务功能在 Phase 1-8 已实现；本 Phase 把它们串成端到端全链路，验证 8 个关键场景，然后准备上线（灰度/压测/检查单/回滚）。
  - 现状测试基础：`backend/tests/test_p1_flow.py`（Phase 1 切片）+ `smoke_test.py`；Phase 2-8 的切片测试（`test_p5_data.py`/`test_p7_rules.py`/`test_p8_seven_step.py` 等）在各自 Phase 已建。本 Phase 新增**端到端 `test_e2e_flow.py`** 覆盖 T1-T8。
- **只做本 Phase 的事**：端到端验收 + 上线动作。
  - **不补业务功能**：验收中发现的 bug 反馈到对应 Phase 修，不在本 Phase 临时塞功能。
  - **不跳过拦截门**：T2/T4 验证的是"拦截正确"，拦截失效 = bug。
- **小切片验收**：按 T1..T8 逐个场景验收，每个通过才进下一个；最后 U1..U4 上线动作。
- **本 Phase 无 DDL**（纯验收 + 上线；回滚引用 Phase 1-8 各 M00x 的回滚 DDL）。
- 完成后全链路 + 8 场景全绿 + 上线检查单签字，才算 Phase 9 完成（=项目上线）。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 1-8 全部实现 | ⚠️ 须全部落地 | 当前 HEAD 仅 Phase 1；Phase 2-8 须先实现并通过各自验收门，本 Phase 才能开工 |
| Phase 4 ⑤⑥⑦ 溯源三表 | ⚠️ 须核实 | `audit_document_chunks`/`audit_source_refs`/`audit_field_sources`——T1/T2 溯源链、U2 抽样验收依赖 |
| 决策 11（金额元统一） | ✅ | T5 金额边界验收依据 |
| 决策 9（无向量） | ✅ | 验收不含语义检索 |
| 回归基线 `dev-specs/05-regression-baseline.md` | ✅ | 各 Phase 已累积，本 Phase 全量回归 |

## 2. 目标

端到端全链路一气呵成跑通（立项 → 文书）；8 个关键场景（T1-T8）全部正确；性能并发压测达标；灰度开关就绪可切换新旧接口；上线检查单 + 回滚预案完备。**至此系统具备上线条件。**

## 3. 核心规则（验收原则）

### 3.1 全链路垂直切片（T1）

立项 → 对象范围 → 审计事项 → 创建资料空间 → 上传文件 → OCR → 结构化落 data_* → 溯源落 chunk/source_refs → 数据分析 → 疑点核实 → 文书生成 —— 一气呵成，**各阶段数据落库完整、可回溯**，不得中途手工补数据。

### 3.2 拦截即正确（T2/T4）

- readiness 三道门（entry/data_ready/evidence_complete）**该拦就拦**：OCR 未完成进 Step5 必被拦（T2）。
- 跨项目隔离**全拒**：项目 A 查项目 B 的任何数据/分析/文书全部拒绝（T4）。

### 3.3 可恢复（T3）

任意步骤刷新/重开/中断，从中断点续，步骤号与已确认结果与中断前一致（Phase 8 后端权威 resume）。

### 3.4 不破坏旧接口 + 可回滚（U1/U4）

- 旧接口灰度保留，前端按开关切换新旧；新接口异常可切回旧。
- 上线失败有回滚预案：各 Phase M00x 回滚 DDL + 代码回滚点。

### 3.5 降级不白屏（T6）

LLM 停机时，降级路径提示「非 AI 推理」（规则结果仍可用），不白屏/不抛 500。

## 4. 任务清单（T1..T8 验收场景 + U1..U4 上线动作）

### 验收场景（方案 §五 Phase 9）

| # | 场景 | 现状基础 | 完成标准（预期正确行为） |
|---|---|---|---|
| T1 | 全链路主流程 | Phase 1-8 全链路 | 立项→文书一气呵成，各阶段数据落库完整、可回溯 |
| T2 | OCR 未完成进 Step5 | P8-5 readiness(data_ready) | 被 readiness 拦截；OCR 完成后自动放行进 Step5 |
| T3 | 恢复分析（刷新/重开） | P8 后端权威 resume | 步骤号 + 已确认结果与中断前一致 |
| T4 | 跨项目隔离 | Phase 6 权限 + DataService project_id 强制 | 项目 A 查项目 B 数据/分析/文书全部拒绝 |
| T5 | 金额边界（万/元、阈值） | 决策 11 元统一 + Phase 5 质量 | 单位换算正确，阈值比对不差万倍 |
| T6 | LLM 停机 | 各 Phase 降级路径 | 降级提示「非 AI 推理」，规则结果可用，不白屏 |
| T7 | 大数据表扫描 | Phase 5 游标分页 + 超时保护 | 限时分页，不超时 |
| T8 | 并发编辑事项 | 乐观锁 | 并发冲突有提示，不互相覆盖 |

### 上线动作（方案 §五 上线）

| # | 动作 | 现状基础 | 完成标准 |
|---|---|---|---|
| U1 | 旧接口灰度 | ❌ 前端无 feature flag | 前端开关切换新旧接口（新异常可切回旧）；灰度策略文档 |
| U2 | 溯源抽样验收 | Phase 4 溯源链 | 抽样 N 条 AI 结论，证据可回溯到 chunk/页/原文 |
| U3 | 性能并发压测 | — | 压测（locust 或同等）达标：大数据表扫描、七步并发不超时 |
| U4 | 上线检查单 + 回滚预案 | 各 Phase M00x 回滚 DDL | 检查单签字 + 回滚预案（DDL 回滚 + 代码回滚点 + 灰度切回） |

**涉及文件**：
- `backend/tests/test_e2e_flow.py`（新增，T1-T8 端到端）
- `docs/TEST_REPORT_PHASE_9.md`（新增，8 场景 + 压测 + 抽样结果）
- `docs/RELEASE_CHECKLIST.md`（新增，U4 上线检查单）
- `docs/ROLLBACK_PLAN.md`（新增，U4 回滚预案）
- 前端灰度开关（U1，`frontend/js/app.js` 或配置；现状无，需新建）

## 5. 本 Phase DDL

**无新表**。本 Phase 为纯验收 + 上线；回滚引用 Phase 1-8 各 `M00x_*.sql` 的回滚段（见 `ROLLBACK_PLAN.md` 汇总）。

## 6. 验收契约（T1-T8 预期 + 上线检查单）

### 6.1 T1 全链路主流程

- 端到端：`POST /projects`(立项) → 对象范围 → `POST /items`(事项) → 创建资料空间 → `POST /files`(上传) → OCR → 结构化(data_*) → 溯源(chunks/source_refs) → `POST /analysis`(Step1) → … → `POST /documents/batch`(Step7)。
- 断言：每个阶段产出落库（audit_projects.setup_stage 推进 / audit_items / audit_document_traces / data_* / audit_analysis_tasks.current_step 1→7 / project_suspicions / audit_step_summaries / 文书）。

### 6.2 T2 OCR 未完成进 Step5

- 上传后立即（OCR 未完成）调 `POST /analysis/{id}/step/4` 或 `readiness?stage=data_ready`。
- 断言：`ready=false`，checks 列出"OCR未完成/结构化未完成"；待 OCR 完成后 `ready=true` 放行。

### 6.3 T3 恢复分析

- Step3 确认后刷新页面/重开浏览器。
- 断言：`GET /analysis/{id}` 返回 current_step=3 + 已确认 selected_violations/selected_laws；前端渲染与中断前一致（**非 localStorage**）。

### 6.4 T4 跨项目隔离

- 项目 A 的成员/凭证访问项目 B 的 `/projects/B/data/*`、`/analysis`(B)、`/documents`(B)。
- 断言：全部 403/拒绝；DataService project_id 强制 + Phase 6 权限双重拦截。

### 6.5 T5 金额边界

- data_* 金额字段以「元」存（决策 11）；构造万/元混入场景。
- 断言：阈值比对（如 ≥200万公开招标）正确，不因单位差万倍误判。

### 6.6 T6 LLM 停机

- 停掉 LLM 服务（`/api/llm/health` 不可用），跑七步。
- 断言：规则可执行步骤（Step5 表达式扫描）仍出结果；LLM 依赖步骤（Step1 意图/Step7 文书语言组织）降级提示「非 AI 推理」，不白屏/不 500。

### 6.7 T7 大数据表扫描

- data_* 灌入大批量行（如 10 万+），跑 Step5 + 数据工坊查询。
- 断言：游标分页 + 超时保护生效，不超时（Phase 5 P5-6）。

### 6.8 T8 并发编辑事项

- 两个会话同时编辑同一 audit_item。
- 断言：乐观锁（version 字段）生效，后提交者收到冲突提示，不静默覆盖。

### 6.9 上线检查单（U4）

- [ ] Phase 1-8 验收门全过 + 本 Phase T1-T8 全绿
- [ ] 回归基线 `05-regression-baseline.md` 全量通过
- [ ] U2 溯源抽样 N 条可回溯
- [ ] U3 压测达标
- [ ] U1 灰度开关可用
- [ ] 回滚预案演练通过
- [ ] 数据库备份 + 配置备份

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| HEAD 仅 Phase 1，Phase 2-8 未实现 | 本 Phase 开工前确认 Phase 1-8 全部落地；否则先补 |
| Phase 4 ⑤⑥⑦三表可能未落地 | §1 前置核实；T1/T2/U2 依赖 |
| 前端无灰度开关基础设施 | U1 新建 feature flag（localStorage/配置切换新旧 API）；实现细节 TODO |
| 无并发压测工具 | U3 选型（locust 推荐，Python 栈一致）；TODO 待定 |
| 降级路径分散在各 Phase | T6 需逐一验证各 Phase 降级点不白屏 |
| 全链路数据依赖链长 | T1 严格按业务链顺序，不跳步不手工补数据 |

## 8. 验收脚本（端到端 + 8 场景）

```bash
BASE=http://localhost:5000/api/audit
# 前置：Phase 1-8 全部实现；LLM/OCR/MinIO/MySQL 服务就绪；测试项目数据

# T1 全链路（pytest 端到端）
cd backend && python -m pytest tests/test_e2e_flow.py::test_full_chain -v
# 断言：立项→文书一气呵成，各表落库完整

# T2 OCR 未完成拦截
cd backend && python -m pytest tests/test_e2e_flow.py::test_ocr_not_ready_blocked -v
# 断言：readiness ready=false；OCR 完成后放行

# T3 恢复分析
cd backend && python -m pytest tests/test_e2e_flow.py::test_resume_after_refresh -v
# 断言：current_step + 已确认结果一致（后端权威，非 localStorage）

# T4 跨项目隔离
cd backend && python -m pytest tests/test_e2e_flow.py::test_cross_project_isolation -v
# 断言：A 查 B 全拒

# T5 金额边界
cd backend && python -m pytest tests/test_e2e_flow.py::test_amount_unit_boundary -v
# 断言：万/元换算、阈值比对正确

# T6 LLM 停机降级
cd backend && python -m pytest tests/test_e2e_flow.py::test_llm_down_degrade -v
# 断言：规则结果可用，LLM 步骤提示非 AI 推理，不白屏

# T7 大数据扫描
cd backend && python -m pytest tests/test_e2e_flow.py::test_large_table_scan -v
# 断言：游标分页 + 超时保护，不超时

# T8 并发编辑事项
cd backend && python -m pytest tests/test_e2e_flow.py::test_concurrent_edit_conflict -v
# 断言：乐观锁冲突提示

# 全量回归
cd backend && python -m pytest tests/ -v
cd backend && python tests/smoke_test.py

# U2 溯源抽样（人工 + 脚本）
# 抽样 N 条 AI 结论，逐条 GET /traces/... 回溯到 chunk/页/原文

# U3 压测（locust，TODO 工具选型）
# locust -f tests/perf_locust.py --host=$BASE  # 大数据扫描 + 七步并发

# U1 灰度开关（前端手动验证）
# 切换开关 → 新旧接口切换 → 新异常可切回旧
```

> `test_e2e_flow.py` 仿 `test_p1_flow.py`，但覆盖完整业务链 + 8 场景断言。

## 9. 完成标准（汇总 = 上线门）

- [ ] T1 全链路主流程一气呵成，各阶段数据落库完整
- [ ] T2 OCR 未完成被 readiness 拦截，完成后放行
- [ ] T3 恢复分析步骤与已确认结果一致（后端权威）
- [ ] T4 跨项目隔离全拒
- [ ] T5 金额边界（万/元、阈值）正确
- [ ] T6 LLM 停机降级不白屏
- [ ] T7 大数据表扫描限时分页不超时
- [ ] T8 并发编辑事项乐观锁生效
- [ ] U1 灰度开关可用（新旧接口可切）
- [ ] U2 溯源抽样验收通过
- [ ] U3 性能并发压测达标
- [ ] U4 上线检查单签字 + 回滚预案演练
- [ ] 全量回归 `05-regression-baseline.md` 通过（Phase 1-8 行为未破坏）
- [ ] 8 节验收脚本全绿（记录到 `docs/TEST_REPORT_PHASE_9.md`）
- [ ] **系统上线**
