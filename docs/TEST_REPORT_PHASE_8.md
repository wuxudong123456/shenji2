# Phase 8 测试报告 — 七步智能分析引擎契约层

> 执行依据：`docs/phase-exec/PHASE_8.md`（P8-1..P8-12）+ `docs/dev-specs/04-analysis-engine-contracts.md`（附录A v1 契约）
> 本轮范围：七步端到端（M008 + P8-1..P8-8 + P8-10/P8-11 + 前端 P8-9）。**P8-12 质量评测（黄金集 + 准确率/漏报/误报）留待下一轮。**
> 测试时间：2026-08-10｜分支：phase2

---

## 1. 结论

| 维度 | 结果 |
|------|------|
| 契约层断言（test_p8_seven_step.py） | ✅ **PASS=47 / FAIL=0** |
| 回归 test_p5_data.py | ✅ PASS=23 / FAIL=0 |
| 回归 test_p7_rules.py | ✅ PASS=18 / FAIL=0 |
| 后端 health | ✅ 200 |

七步分析引擎**契约层**完整落地，前后端可跑通，未破坏 Phase 1-7。

---

## 2. 契约层落地统计（附录A v1）

| 契约项 | 落地点 | 验证 |
|--------|--------|------|
| **M008** 三表三列 | `migrate.py:migrate_phase8_contract_tables()`（CREATE project_suspicions/audit_agent_traces/audit_step_summaries + ALTER analysis_tasks 加 focus_item_id/analysis_target/analysis_scope） | 幂等跑两遍；7 项 schema 断言 ✅ |
| **§2 current_step=1 权威源** | `analysis_lifecycle.create_analysis_task()` 落 current_step=1 | task_code 16 位 + current_step/step/focus_item_id/analysis_target 回填 ✅ |
| **§9 readiness 三道门禁** | `check_readiness(stage)` entry(5)/data_ready(7)/evidence_complete(4) + `GET /analysis/{id}/readiness` | 三道 checks 数量 + ready 流转 ✅ |
| **七步 current_step 推进** | `advance_step(to_step)` UPDATE current_step + JSON_MERGE_PATCH step_data + UPSERT summaries | 1→7 推进 + step_data 合并齐全 ✅ |
| **§8 固定 message_id** | `_upsert_step_summary` message_id=`step-{N}-summary` | step2-7 message_id 全匹配 ✅ |
| **§7 疑点五态流转** | `POST /analysis/{id}/suspicions/review`（MODEL_FOUND→WAIT_CONFIRM→{CONFIRMED\|REJECTED\|NEED_MORE_EVIDENCE}） | 五态流转 + status 同步 + evidence_chain 合并 + 非法态 400 ✅ |
| **P8-11 trace 落库** | `base.py:_persist_trace` 三出口 + `graph.py` 6 节点补 context（task_id/project_id/step/node_name/upstream_trace_ids） | audit_agent_traces 全列可写 + task_id/step/node 关联 ✅ |
| **P8-8 文书证据继承** | `documents/batch` 收 task_id → AnalysisContextBuilder + `get_confirmed_suspicion_evidence` + 报告读 CONFIRMED 疑点 | 四件套齐全 + report 继承 CONFIRMED 疑点 + readiness.evidence_complete ✅ |
| **Q1 GET 权威状态** | `GET /analysis/{id}` 纯 MySQL 读（get_authoritative_state） | current_step=7 + summaries 非空 ✅ |
| **Q2 graph 拓扑** | 移除 step_6 节点，step_5_analysis→END；Step6 走独立 `/suspicion/generate` | 编译 + 端点契约 ✅ |

---

## 3. 前端收敛清单（P8-9，analysis-wiz.js）

**关键发现**：`parseIntent()` 早已调 `POST /api/audit/analysis` 并缓存 `this._taskId` —— Step1 任务生命周期本已接通，`_primaryLaws`/`_matches` 已是真后端数据。本轮收敛据此展开。

| 收敛项 | 内容 | 状态 |
|--------|------|------|
| **F2 syncStepFromTask** | 新增 `syncStepFromTask(taskId)` → GET 权威状态 → `this.step=current_step`（§0：后端唯一权威）；接入 `resumeProgress`（恢复时后端校正）+ 疑点/文书成功后 reconcile | ✅ |
| **疑点/文书 task_id** | `/suspicion/generate`、`/documents/batch` body 传 `task_id`，后端按 task_id 装配上下文（替前端 `_buildDocContext`） | ✅ |
| **F5 mock 清理（6 处）** | ①`handleDrop` 随机 match→60 ②`_pickRecommendations` 去 `Math.random` ③`showProjectContext` 去伪造单位性质/职能/预算 + 伪造 4 法规 → 改读 `_primaryLaws` ④`renderS3` 写死 4 类法规 fallback → 真 `_primaryLaws`/法规库/空态 ⑤`compareRegulations` 写死招标投标法等 → 改读 `_primaryLaws` ⑥`renderS6` 随机 match → 按风险等级确定性 | ✅ |
| **F3 11 处硬切** | chat 流（process）中 `this.step=N` 本地渲染保留；通过「parseIntent 建 task + 疑点/文书成功后 syncStepFromTask + resume 后端校正」实现后端权威，**未盲改 11 处导航式硬切**（无浏览器逐处手验条件，避免破坏可用 chat demo） | ⚠️ 部分（见 §6） |

---

## 4. 三处规格偏差记录（偏离执行包字面，已核实）

1. **M008 落地改 migrate.py 函数，非 `.sql`**。项目无 `migrations/` 目录，迁移走 migrate.py 函数式（`DATABASE="tt"` + `_table_exists`/`_column_exists` 幂等预检）。且 3 张目标表 DB 全未建 → **CREATE 三表**（非执行包假设的 ALTER）。
2. **`current_step` 列原是空壳**（路由只写 `step`）。本轮从零启用 current_step 为唯一权威源（1-7），`step` 列降为兼容别名。
3. **graph 6 节点 `agent.run()` 不传 context**（P8-11 硬阻塞）。本轮补 6 节点 context 参数 + base.py 三出口 `_persist_trace`。

---

## 5. 测试中发现的 Bug（已修）

| Bug | 根因 | 修复 |
|-----|------|------|
| `_check_data_ready` 500 | 引用不存在列 `template_name`/`doc_type` | 改读真实列 `file_category`/`file_subcategory` |
| `/suspicion/generate` Data truncated | hex `task_code` 写入 INT 列 `analysis_id` | 由 task_code 反查数值 id |
| 文书 report.total_suspicions=0 | `get_confirmed_suspicion_evidence` 返回 dict 漏 `verify_status`/`status`，`_build_report_template` 复核全落空 | 返回 dict 透传 verify_status/status |
| `documents/batch` 疑点不继承 | 同 analysis_id INT vs task_code hex 不匹配 | 由 task_code 反查数值 analysis_id |

---

## 6. 未完成 / 下一轮

- **F3 全量硬切替换**：11 处 `this.step=N` 未逐个改为「调确认端点 → syncStepFromTask」。当前以「疑点/文书成功后 reconcile + resume 后端校正」实现后端权威；导航式硬切（推荐/依据/上传，纯 UI 渲染且与后端 current_step 一致）保留。**需浏览器逐处手验后再决定是否收口**（避免盲改破坏可用 demo）。
- **P8-12 质量评测**：黄金集 + 准确率/漏报/误报（需 LLM 在线 + 标注集），本轮明确不做。
- **LLM 驱动的 graph 端到端**：本轮 test_p8 走契约层（service/路由 DB 契约，不依赖 LLM）；graph 6 Agent 全链端到端跑通属 P8-12 范畴。

---

## 7. 复现

```bash
cd backend && python app.py                    # 后端
python tests/test_p8_seven_step.py             # 47 项契约断言
python tests/test_p5_data.py                   # 回归
python tests/test_p7_rules.py                  # 回归
```

提交链：C1(31931fe) → C2(b3490f8) → C3(e82e041) → C5(d8c32f7) → C4(9fd62ae) → C6(aebbd88) → C7(857eba3)。
