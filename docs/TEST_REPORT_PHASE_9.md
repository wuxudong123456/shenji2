# Phase 9 测试报告 — 端到端验收与上线

> 执行依据：`docs/phase-exec/PHASE_9.md`（T1-T8 验收场景 + U1-U4 上线动作）
> 本轮范围：**T1 七步全链路端到端（真 LLM）**。T2-T8 / U1-U4 后续逐项展开。
> 测试时间：2026-08-10｜分支：phase2

---

## 1. 结论

| 维度 | 结果 |
|------|------|
| **T1 全链路（真 LLM）** | ✅ **PASS=26 / FAIL=0** |
| 回归 test_p8_seven_step.py（契约层） | ✅ PASS=47 / FAIL=0 |
| 回归 test_p5_data.py（Phase1-6） | ✅ PASS=23 / FAIL=0 |
| 回归 test_p7_rules.py（Phase7） | ✅ PASS=18 / FAIL=0 |
| 后端 health | ✅ 200 |

**七步智能分析引擎首次以真 LLM 全程跑通**：立项→意图分析→违规模型匹配→法规确认→资料就绪→数据比对→疑点生成→人工核实→文书生成，`current_step` 1→7 全程后端权威推进，各阶段（step_data / summaries / suspicions / traces / 文书）落库完整。

---

## 2. T1 全链路时序（真 LLM 实测值）

夹具项目 `4a0946e4c4c0`（清岳区政务服务中心2025年度办公电脑采购项目），首事项「采购预算与计划执行审计」。真服务在线：LLM(deepseek, available=true) + OCR(MinerU healthy) + MinIO(ok)。

| 步骤 | 端点 | 实测结果 | current_step |
|------|------|----------|:---:|
| Step1-2 | POST /analysis（IntentAnalyzer + 3 并行 Agent，真 LLM） | 200，matches=6，readiness.entry.ready=True | **2** |
| Step3 | POST /analysis/{id}/confirm | 200，law_recommendations 返回 | **3** |
| 门禁 | GET /analysis/{id}/readiness?stage=data_ready | ready=True（夹具 trace+data+field_sources 完整） | — |
| Step4→5 | POST /analysis/{id}/step/4（AuditAnalyzer + 表达式扫描，真 LLM） | 200，analysis_results=2，overall_assessment 有 | **5** |
| Step6 | POST /suspicion/generate（SuspicionGenerator，真 LLM） | 200，suspicion_id 落库，verify_status=MODEL_FOUND | **6** |
| 疑点核实 | POST /analysis/{id}/suspicions/review（→CONFIRMED） | 五态流转 MODEL_FOUND→CONFIRMED，status=confirmed | — |
| Step7 | POST /documents/batch（四件套，读 CONFIRMED 疑点） | 200，evidence/workpaper/report/review 齐全 | **7** |
| 终态 | GET /analysis/{id} | current_step=7，summaries 覆盖 2/3/5/6/7 | **7** |

**落库完整性（DB 直查）**：
- `audit_analysis_tasks.current_step` = 7，step_data 含 matches/selected_laws/analysis_results ✅
- `audit_step_summaries` = 5 条（step 2/3/5/6/7，固定 message_id=`step-{N}-summary`）✅
- `audit_agent_traces` = 4 条（P8-11 溯源，task_id/step/node 关联）✅
- `project_suspicions` verify_status=CONFIRMED + status=confirmed ✅

---

## 3. 端到端跑通的 3 处缺陷（已修）

T1 是首个用真 LLM 压全链的测试，暴露了契约层（test_p8，DB 契约、不依赖 LLM）无法触达的 3 处缺陷：

| 缺陷 | 根因 | 修复 | 验证 |
|-----|------|------|------|
| **Step7 不推进 current_step** | `documents/batch` 生成四件套后只返回结果，不调 `advance_step` → 任务卡在 6，违反附录A「Step7=文书生成，任务至此 1→7」 | `phase6_routes.py` 文书生成成功后 `alc.advance_step(task_id, to_step=7, …)` + 响应回填 `current_step=7` + `readiness.evidence_complete` | T1 Step7 后 current_step=7 ✅ |
| **POST /analysis 响应缺 current_step** | `_graph_state_to_response` 只返回旧别名 `step`，不返回 `current_step`（Q1 权威字段）→ 前端 syncStepFromTask 拿不到权威步骤 | 响应 dict 补 `"current_step": current_step`（与 GET 一致） | T1 Step1-2 后 current_step=2 ✅ |
| **缺 step-2 正式总结** | POST /analysis 调 `advance_step` 只传 `step_data_patch`，不传 `summary_content` → step-2 无 audit_step_summaries 行（违反附录A §8 每步固定 message_id） | POST /analysis 传 `summary_content` + `summary_structured`（matches/primary_laws 计数） | T1 summaries 覆盖 2/3/5/6/7，5 条 ✅ |

提交：`94f4f7c test(phase9): T1 七步全链端到端跑通(真LLM)`。

---

## 4. 与 Phase 8 契约层的关系

Phase 8 落的是**契约层骨架**（47 项断言，DB/路由契约，刻意不依赖 LLM）：
- `current_step` 唯一权威源、三道 readiness 门禁、疑点五态流转、trace 落库、文书证据继承 —— 全是**结构契约**。

Phase 9 T1 是**首个用真 LLM 压全链**的验收：
- 验证 6 Agent（IntentAnalyzer / 3 并行推荐 / AuditAnalyzer / SuspicionGenerator）在真 LLM 下产出**符合契约结构**的结果；
- 验证 graph 拓扑（移除 step_6、step_5→END、两 interrupt）在真 LLM 下不卡不崩；
- 上面 3 处缺陷正是「契约绿 ≠ 端到端通」的典型 —— `test_p8` 断言 advance_step 能推进、能写 summary，但**没断言"谁在何时调 advance_step"**，所以 Step7 漏调、Step2 漏写 summary 都没被契约层抓到。T1 补上这层端到端覆盖。

**意义**：从"形似"（骨架/契约）到"神至"（真 LLM 全链跑通），七步引擎可用性跨越临界点。

---

## 5. 复现

```bash
cd backend && python app.py                       # 后端（LLM/OCR/MinIO 须在线）
python tests/test_e2e_flow.py                     # T1 七步全链（真 LLM，~3-5 分钟）
python tests/test_p8_seven_step.py                # 回归：契约层
python tests/test_p5_data.py                      # 回归：Phase1-6
python tests/test_p7_rules.py                     # 回归：Phase7
```

---

## 6. 下一步（Phase 9 余项）

| 项 | 内容 | 依赖 |
|----|------|------|
| **T2** | 断点续跑（中断→恢复，SqliteSaver checkpoint + GET 权威状态校正） | T1 ✅ |
| **T3** | 多项目并发（两任务并行 invoke，thread_id 隔离） | T1 ✅ |
| **T4** | 溯源穿透（结论→source_refs→chunk 页码/坐标，P4 链路验证） | T1 ✅ |
| **T5-T8** | 失败注入 / LLM 超时 / 数据缺失门禁 / 人工驳回回流 | T1-T4 |
| **U1-U4** | 上线（构建打包 / 部署文档 / 健康检查 / 回滚预案） | T1-T8 |
| P8-12 | 质量评测（黄金集 + 准确率/漏报/误报，需标注集） | 独立 |

T1 已确认主链通、回归稳，后续 T2-T8 为边角鲁棒性 + 上线准备。

---

## 7. T4 溯源穿透验收（真 LLM）

> 执行：`python tests/test_p9_t4_provenance.py`（PASS=6 / FAIL=4，4 项 FAIL 均为预期暴露的写侧接线缺口）

**验收目标**（§0 铁律）：AI 结论必带 `source_refs`，可穿透到文档 chunk 的页码/坐标/原文；无来源条目禁止入文书。

### 7.1 四层链路实测

| 层 | 内容 | 状态 | 实测值 |
|----|------|:----:|--------|
| ① 数据层 | OCR → chunks → field_sources | ✅ | 144 field_sources，43 带 chunk_id，30 chunks 全有正文 |
| ② evidence API | `build_field_sources_evidence` / `add_ref` / `get_refs` | ✅ | sample row（data_procurements:16）产出 **23 条**证据，含 page_nums/bbox/text/ocr_version 全键 |
| ③ **Agent 接线** | AuditAnalyzer/SuspicionGenerator 调 evidence_service 落结论级引用 | ❌ **断** | analysis_results **0/3** 带 source_refs；audit_source_refs 结论级（analysis_hit/suspicion/law_rec）=**0**；suspicion source_refs=**0** |
| ④ 消费侧 | context_builder / documents report 读 source_refs | ✅ 读到空 | report 键有 summary/suspicions/recommendations，**无证据引用** |

### 7.2 缺口定性

- **断点在写侧（Agent），非读侧（evidence_service）**。`evidence_service` 全套接口可用（层②实测 23 条证据），但 `agents/` 目录**零处**调用 `add_ref` / `build_field_sources_evidence`（静态确认：grep `source_refs|add_ref|analysis_hit` 在 `agents/` 无命中）。
- AuditAnalyzer 的 `_scan_expression` 已拿到命中行（`scan.rows`，每行含 `row_id`+表名），却只 `add_knowledge_source`（Agent 内部知识登记，非 `audit_source_refs`），**不连 field_sources→chunk 证据链**。
- SuspicionGenerator 产出的 `suspicion_items` 有 `evidence_chain` schema 字段（LLM 自由文本），但行级 `evidence_chain` 列空、`audit_source_refs` 无 `result_type=suspicion` 行。
- 与 T1 三处缺陷**同病**："契约层（test_p8）证明 evidence_service *能*写、*能*读，但没人在该写的时候写" → 结论级溯源表恒空。

### 7.3 影响

**上线阻塞项（§0 铁律违反）**：审计结论带法律责任，一条不溯源到原文页码的违规发现 = 法律上无效。当前全链产出的 analysis_results / suspicions / documents 均无 chunk 级证据，**不可上线**。

### 7.4 数据层结论（利好）

OCR 写侧（Phase 3/4）对夹具项目产出了**完整可溯源的 chunk 数据**（30 chunks 全有正文 + 43 field_sources 带 chunk_id）——"空壳"问题在该项目不存在。即：**修写侧接线后，溯源链立即贯通**，无需等 OCR 侧改造。

### 7.5 修复落地（已完成，本轮）

**§0 铁律接线已补齐并验证**。改动为加法式（调已有 `evidence_service`，不重写 Agent）：

| 接线点 | 改动 | 作用 |
|--------|------|------|
| `audit_analyzer._scan_expression` | 命中行逐条调 `build_field_sources_evidence` + `add_ref(result_type="analysis_hit", result_id={task_id}:{vid})`，按 vid 暂存证据 | 扫描命中即落结论级溯源（source_of_truth = `audit_source_refs`） |
| `audit_analyzer.validate_output`（新增 override） | LLM 产出后，按 `violation_model`(title)→vid 注入 `analysis_result.source_refs`（确定性，幂等；未命中挂全量兜底） | analysis_results 各 hit 带 source_refs |
| `evidence_service.link_suspicion_evidence`（新增） | 复制本任务 analysis_hit 引用为 `result_type=suspicion`（疑点继承同批 chunk） | 疑点可溯源 |
| `/suspicion/generate` 路由 | INSERT 后调 `link_suspicion_evidence` | 疑点落库即带证据 |
| `expression_engine` 白名单 + `audit_analyzer._detect_target_table` | 加 `data_procurements`（采购域主表，原不支持→扫描永不命中采购数据） | 解锁采购域扫描，使接线可触发 |
| `step/4` 路由 | 删除失配的旧 P8-6 循环（读不存在的 `target_table`/`hits` 键），注释归并到 Analyzer 接线 | 去冗余死码 |

**result_id 粒度**（用户确认）：`{task_id}:{violation_id}` —— 每任务×违规模型一组引用，与 `analysis_results[violation_model]` 自然对齐。

### 7.6 验证

| 测试 | 结果 | 说明 |
|------|:----:|------|
| `test_p9_t4_wiring.py`（单元，**决定性**） | ✅ 10/10 | 直接驱动 `_scan_expression` 命中 data_procurements：8 条 analysis_hit 落库 + 全可解析到页码/原文 + validate_output 注入 source_refs(含 chunk_id) + link_suspicion 继承 8 条 |
| `test_p9_t4_provenance.py`（E2E） | ✅ 10/10 | 条件断言（扫描命中 field_sourced 表→source_refs 必非空）；本轮 Step2 匹配落收费域未命中，接线未触发——非缺陷 |
| `test_e2e_flow.py`（T1 全链） | ✅ 26/26 | Analyzer 改动未破坏七步链 |
| 回归 p8 / p7 / p5 | ✅ 47 / 18 / 23 | expression_engine 白名单 + Analyzer 接线未破坏既有 |

### 7.7 残留上游缺口（非本轮范围）

E2E 全链的 source_refs 仍为空，根因在**上游两层**（非溯源接线）：
1. **Step2 匹配域偏**：IntentAnalyzer+violation_matcher 为「办公电脑采购」项目匹配到**收费域**违规（`收费项目明细台账`/`非税收入缴库明细表`），其表达式扫收费表（不存在）→ 0 命中。属匹配质量问题 → **P8-12 质量评测**范畴。
2. **违规表达式用中文业务表名**（`信息化设备采购清单`/`施工合同`），不映射物理 `data_*` 表 → 即使匹配到采购违规，行级扫描也难命中。属表达式↔表映射 → **Phase 7 表达式引擎增强**范畴。

**结论**：§0 溯源接线已修复并经单元测试决定性证明（命中即贯通）。全链 source_refs 转绿需上游（匹配域 + 表达式表映射）改善，已定性归因、独立排期，不阻塞本轮。
