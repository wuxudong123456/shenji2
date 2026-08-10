# PHASE_8 执行包：七步智能分析

> **执行协议**：本文件是 Phase 8 的**唯一执行依据**。执行者只读本文件 + `docs/dev-specs/04-analysis-engine-contracts.md`（附录A 完整 JSON Schema 契约），不要读主方案全文。
> 前置状态：Phase 1-7 已完成（项目链 + 资料空间 + OCR + 溯源 + 数据工坊 + 权限 + 知识/规则）。
> 铁律：以**附录A v1 契约为实现依据**；步骤推进由后端 `audit_analysis_tasks` 唯一权威驱动（4.5.5）；前端 = 渲染器 + 确认器（删 `this.step` 自推进、关键词驱动）；AI 输入由 `AnalysisContextBuilder` 从 DB 装配，**LLM 请求禁含 HTML**；所有 AI 结论必须带 `source_refs`，无来源标「待人工核实」不得进文书；**不复写** 6 Agent / LangGraph 主干 / 知识库 / 表达式引擎已有能力，只补契约层。

---

## 0. 执行者须知（先读）

- **关键认知：七步骨架大半已实现（Phase 1-3 内联产物），缺的是附录A 契约层**：
  - 6 个 Agent（`intent_analyzer`/`violation_matcher`/`data_advisor`/`regulation_advisor`/`audit_analyzer`/`suspicion_generator`）成熟可用，对应 Step1-6；LangGraph 主干（StateGraph + SqliteSaver + 两 interrupt）可跑；Step1-2 真实可用，Step3-7 骨架在但契约层空。
  - 表达式引擎、知识工坊三库、OCR 上传链路、文书生成（`document_service`）均可用。
  - **本 Phase 硬骨头 = 附录A 契约层**（`AnalysisContextBuilder` / readiness 三道检查 / `source_refs` 证据链 / 疑点五态 / `_persist_trace` 落库 / `audit_step_summaries` 固定 ID）+ **前端去自推进 / resume 后端化** —— 这一层当前 HEAD 几乎全空。
- **实现路径（用户确认）**：以附录A v1 为目标从当前 HEAD 实现，**不 cherry-pick `fix/seven-step-mvp-recovery` 分支代码**（该分支 Task 1-11 未合并、范围窄、与当前 HEAD 是两套实现）。`docs/superpowers/specs/2026-08-02-seven-step-mvp-recovery-design.md`（18 节）作**设计蓝本参考**（状态机/错误码/ExecutionPlan/幂等表设计质量高），但其代码不并入。
- **只做本 Phase 的事**：七步分析引擎契约层 + 前端收敛。
  - **不做向量检索**（决策 9）：语义检索不在范围。
  - **不做 recovery 独有设计**：方案/附录A **未要求**的「任务状态机七态枚举（initializing/awaiting_…）」「幂等操作表 `audit_task_operations`」**不纳入本 Phase**——按方案的 `current_step + status`（draft/in_progress/completed）走；如后续需要幂等/七态，另开任务。不臆造。
  - **不重写 Agent / 引擎**：6 Agent 的 `build_prompt` 三段式、`invoke_tool`、知识溯源复用；表达式引擎复用。
- **小功能切片**：按第 4 节 P8-1..P8-12 逐个推进，每个测试通过才进下一个。每切片 0.5-2 天。
- **数据库变更单独 commit**（M008，§5；前置 Phase 4 三表若缺须先补）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 8 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 1-7 | ✅ 执行包就绪 | 项目链/资料/OCR/数据工坊/权限/知识规则 |
| **Phase 4 ⑤⑥⑦ 溯源三表** | ⚠️ **须核实** | `audit_document_chunks` / `audit_source_refs` / `audit_field_sources`（方案 504-555）—— schema.sql 现未见。**Phase 8 的证据链（P8-4/P8-6/P8-8）硬依赖**。开工前核实：若 DB 已有只是 schema.sql 滞后 → 继续；若 DB 真缺 → 先补 Phase 4 M004 迁移再开工 |
| Phase 7 ⑩ 映射链 | ✅ 依赖 | `audit_engine_rules`（target_table/expression/field_mapping）+ `audit_item_methods`（data_requirements）—— P8-3 Step2 推荐、P8-6 Step5 确定性表探测依赖 |
| 决策 9（无向量） | ✅ | 语义检索排除 |
| Phase 8 实现路径 | ✅ 用户确认 | 附录A 全量重写，recovery 当蓝本不 cherry-pick |

## 2. 目标

附录A 七步契约全落地：意图确认 → 方法推荐 → 法规确认 → 资料准备 → 数据比对 → 疑点核实 → 文书生成；三道控制层检查（entry/data_ready/evidence_complete）就位；步骤推进由后端 `audit_analysis_tasks` 唯一权威驱动；AI 上下文由 `AnalysisContextBuilder` 装配（无 HTML）；Agent trace 落库；疑点五态流转；每步固定消息 ID 覆盖；前端去自推进、resume 后端化；AI 质量评测达标。**至此七步全流程可跑、可溯源、可恢复。**

## 3. 核心规则（方案 4.5.5 / 4.6 强制）

### 3.1 步骤驱动（4.5.5）—— 后端唯一权威

```
用户点「确认并进入下一步」→ POST 确认接口（携带已确认 ID）
  → 后端三道检查 → 推进 current_step → 计算本步结果 → 持久化 step_data + audit_step_summaries
  → 返回权威状态 {current_step, status, 本步数据, 已确认 ID, source_refs}
  → 前端照返回的状态渲染
```

- **前端 = 渲染器 + 确认器**：删除 `analysis-wiz.js` 的 `this.step=N` 关键词硬切（6 处）与 localStorage resume；只调确认/推进端点，按响应渲染。
- **后端 = 状态源 + 计算器**：`audit_analysis_tasks.current_step` + `step_data` 唯一权威。
- **LangGraph = 每步内部可选的 Agent 编排引擎**：不要求前端跟随 interrupt 协议；步骤推进由后端任务状态控制，非 graph interrupt 驱动前端。
- **Step7 文书走独立 `documents/batch`**（方案 4.5.5），后端逻辑记为 step 7，不并入 graph 节点。

### 3.2 三类信息严格分离（4.6）

- 页面状态 / Toast 反馈 / 审计日志 **一律不进聊天、不进 LLM**。
- `audit_step_summaries`（⑧表）存每步**一条固定 ID 正式总结**（`step-1-summary` … `step-7-summary` 覆盖，UNIQUE task+step）。
- LLM 输入由 `AnalysisContextBuilder` 从 DB 装配。

### 3.3 证据必带来源（附录A §1）

- 每个 AI 结论输出必须携带 `source_refs`（对应 `audit_source_refs` ⑥表）。
- 无来源条目标 `confirm_status: "待人工核实"`，**禁止进入最终文书**（Step7）。

### 3.4 三道控制层检查（附录A §9）

`GET /api/audit/analysis/{task_id}/readiness?stage=entry|data_ready|evidence_complete` —— 固定检查项：

| stage | 触发点 | name 枚举 | source |
|---|---|---|---|
| entry | Step1 前 | 项目完成/对象范围完成/事项完成/空间存在/权限正确 | `audit_projects.setup_stage`、`audit_items`、权限上下文 |
| data_ready | Step5 前 | 文件存在/OCR完成/分类完成/结构化完成/进入data_*/字段完整/trace存在 | traces/chunks/data_*/field_sources |
| evidence_complete | Step7 前 | 疑点已确认/数据证据存在/文档引用存在/法规存在 | `project_suspicions`、`audit_source_refs` |

### 3.5 疑点五态流转（附录A §7）

`project_suspicions.verify_status`（⑪列）：`MODEL_FOUND → WAIT_CONFIRM → {CONFIRMED | REJECTED | NEED_MORE_EVIDENCE}`，`NEED_MORE_EVIDENCE → WAIT_CONFIRM`（补料后）。命中 ≠ 成立，须人工确认。

### 3.6 不复写已有能力

6 Agent / `BaseAgent.invoke_tool`+`add_knowledge_source`+`validate_output` / `AgentRegistry` / LangGraph 主干 / 表达式引擎 / 知识工坊三库 / `document_service` / OCR 上传链路 —— 均复用，只补契约层与缺口。

## 4. 任务清单（P8-1 .. P8-12，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P8-1 | 分析入口检查 | 🟡 `create` 仅校验 intent；`project_lifecycle.check_stage` 可复用 | entry 门禁接 `setup_stage`/`audit_items`/权限，5 项全检查（附录A §9 entry） |
| P8-2 | Step1 结构化分析任务 | 🟡 路由 `create` 在（body 取 intent，非附录A） | 改 body=`project_id+focus_item_id+user_intent`；落 `analysis_task`（current_step=1，project_context 仅来自 DB） |
| P8-3 | Step2 违规与方法推荐 | ✅ violation_matcher/data_advisor/regulation_advisor 成熟 | 接 Phase7 映射链：candidate 含 `engine_rule`+`audit_methods`；`match_score` 规则排序非 AI 打分 |
| P8-4 | Step3 法规推荐 | ✅ regulation_advisor 成熟 | 输出 `law_recommendations`（law_id/clause_id/clause_text/source_refs）；无条款/无原文 → 待人工核实；confirm 回填 `selected_laws` |
| P8-5 | Step4 资料准备度 | ❌ 无 readiness 端点 | 新建 `GET /readiness?stage=data_ready`，7 项检查（附录A §5），`ready` 布尔驱动能否进 Step5 |
| P8-6 | Step5 数据比对 | 🟡 引擎能跑；`audit_analyzer._detect_target_table` 猜表 | 改用 Phase7 `audit_engine_rules.target_table`（确定性，禁猜表）；输出 `exec_results` 逐行证据 `field_sources→chunk` |
| P8-7 | Step6 疑点人工核实 | 🟡 generator 成熟；status 3 态；不落库 | 加 `verify_status`⑪五态 + 落 `project_suspicions` + 核实 API（五态流转） |
| P8-8 | Step7 文书生成 | 🟡 `documents/batch` 能生成；上下文前端 `_buildDocContext` 拼 | 改后端按 task_id 构建上下文（继承已确认疑点证据链）；AI 只组织语言不创造事实 |
| P8-9 | 固定消息 ID 覆盖 | ❌ 全无 | 前端每步右栏 `id="step-N-summary"`；后端 `audit_step_summaries`⑧ 持久化（UNIQUE task+step，返回修改只覆盖） |
| P8-10 | AI 上下文装配 | ❌ 无 `AnalysisContextBuilder`，route 内联拼 | 新建 `AnalysisContextBuilder`（按 task_id 装配 project_context/focus_item/confirmed_results），LLM 请求纯文本无 HTML |
| P8-11 | Agent 工具调用 + trace 落库 | ✅ `invoke_tool` 有；❌ `_persist_trace` 无 | 补 `BaseAgent._persist_trace()`（F3）：run 末尾写 `audit_agent_traces`（trace_id/input/output/knowledge_sources/tool_call_records/llm_raw_response） |
| P8-12 | AI 质量评测 | ❌ 无 | 黄金集 + 准确率/漏报率/误报率评测达标（报告入 `TEST_REPORT_PHASE_8.md`） |

**涉及文件**：
- `backend/services/analysis_context_builder.py`（新增，P8-10）
- `backend/services/analysis_lifecycle.py`（新增，P8-1/P8-2/P8-5 readiness + 任务推进；可参考 recovery 设计 §4/§9 蓝本，不抄代码）
- `backend/services/evidence_service.py`（新增/加固，P8-4/P8-6/P8-8 统一 `source_refs` 读写）
- `backend/agents/base.py`（补 `_persist_trace`，P8-11）
- `backend/agents/audit_analyzer.py`（P8-6 改确定性表探测，禁 `_detect_target_table` 猜表）
- `backend/agents/suspicion_generator.py`（P8-7 输出五态 + 落库）
- `backend/workflow/state.py` / `graph.py`（拓扑按附录A Step 顺序理顺；Step7 不入 graph）
- `backend/routes/audit_routes.py`（analysis create/confirm/readiness 改造）+ `phase6_routes.py`（documents/batch 上下文来源改造）
- `frontend/js/analysis-wiz.js`（删 `this.step` 自推进 + localStorage resume；加固定消息 ID + 后端权威渲染）
- `backend/data/migrations/M008_*.sql`（⑧ `audit_step_summaries` + ⑪ `verify_status`）
- `backend/tests/test_p8_seven_step.py`（新增，P8-12 + 各步契约断言）

## 5. 本 Phase DDL（M008，照抄方案 §六 ⑧⑪；前置 Phase4 三表标注）

```sql
-- ⑧ 步骤正式总结（Phase 8，方案 557-570）
CREATE TABLE IF NOT EXISTS tt.audit_step_summaries (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  analysis_task_id VARCHAR(64) NOT NULL,
  step_no       TINYINT NOT NULL,
  message_id    VARCHAR(30) COMMENT 'step-1-summary ... step-7-summary',
  content       TEXT COMMENT '正式总结文本',
  structured    JSON COMMENT '结构化总结',
  source_refs   JSON COMMENT '来源引用列表',
  version       INT DEFAULT 1,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_step (analysis_task_id, step_no)
) COMMENT '七步正式总结—固定消息ID覆盖';

-- ⑪ 疑点五态（Phase 8，方案 606-609，增量列不改原 status 语义）
ALTER TABLE tt.project_suspicions
  ADD COLUMN verify_status VARCHAR(30) DEFAULT 'MODEL_FOUND'
  COMMENT 'MODEL_FOUND/WAIT_CONFIRM/CONFIRMED/REJECTED/NEED_MORE_EVIDENCE' AFTER status;

-- 回滚
-- ALTER TABLE tt.project_suspicions DROP COLUMN verify_status;
-- DROP TABLE IF EXISTS tt.audit_step_summaries;
```

> **⚠️ 前置依赖（不在 M008 重复建，但开工前必须就位）**：Phase 4 三表 —— `audit_document_chunks`(⑤) / `audit_source_refs`(⑥) / `audit_field_sources`(⑦)（方案 504-555）。P8-4/P8-6/P8-8 的证据链硬依赖。若 DB 真缺，先执行 Phase 4 M004 迁移。

## 6. 本 Phase 接口契约（附录A 04 文档为准，现状对照）

> 完整 JSON Schema 见 `docs/dev-specs/04-analysis-engine-contracts.md` §2-§10。下表给端点 + 现状 + 动作。

### 6.1 Step1 意图确认（P8-1/P8-2/P8-10）

- `POST /api/audit/analysis`（现状 `audit_routes.py:1238`）—— 改 body 为 `{project_id, focus_item_id?, user_intent?}`（附录A §2）。
- **前置 entry 检查**（P8-1）：create 前调 readiness?stage=entry（项目/对象范围/事项/空间/权限 5 项）。
- 输出 `analysis_task`：`task_id/project_id/project_context`（仅来自 DB，AI 不得重写）/`focus_item`/`current_step=1`。
- 上下文由 `AnalysisContextBuilder`（P8-10）装配，非 route 内联拼。

### 6.2 Step2 方法推荐（P8-3）

- 工作流内 violation_matcher + data_advisor + regulation_advisor 编排（已有）。
- 输出 `violation_candidates[]`：每项含 `violation_id/match_score/engine_rule`(来自 Phase7 `audit_engine_rules`)/`audit_methods`(来自 Phase7 `audit_item_methods`)/`source_refs`（附录A §3）。
- `match_score` 规则排序，非 AI 自由打分。

### 6.3 Step3 法规确认（P8-4）

- `POST /api/audit/analysis/{id}/confirm`（现状 `audit_routes.py:1389`）—— 回填 `selected_violations` / `selected_laws`。
- 输出 `law_recommendations[]`：`law_id/clause_id/clause_no/clause_text/source_refs/confirm_status`（附录A §4）。
- 无条款或无原文 → `confirm_status:"待人工核实"`，禁入文书。

### 6.4 Step4 资料准备度（P8-5）

- `GET /api/audit/analysis/{id}/readiness?stage=data_ready`（**新建**）—— 输出 `{ready, checks[], missing_items[]}`（附录A §5）。
- 7 项检查：文件存在/OCR完成/分类完成/结构化完成/字段完整/进入data_*/trace存在。
- `ready=false` → 拦截进 Step5（Phase 9 T2 场景）。

### 6.5 Step5 数据比对（P8-6）

- `POST /api/audit/analysis/{id}/step/4`（现状 `audit_routes.py:1332`，端点名沿用）—— 输出 `exec_results[]`（附录A §6）。
- **禁猜表**：`target_table` 取 Phase7 `audit_engine_rules.target_table`（确定性），不用 `audit_analyzer._detect_target_table` 字段签名猜测。
- 每命中行带 `evidence.field_sources→chunk`（依赖 Phase4 ⑦表）。

### 6.6 Step6 疑点核实（P8-7）

- `POST /api/audit/suspicion/generate`（现状 `audit_routes.py:1156`，独立端点，附录A.5）—— 输出 `suspicion_candidates[]`，每项 `verify_status` 初态 `MODEL_FOUND`，落 `project_suspicions`（附录A §7）。
- `POST /api/audit/analysis/{id}/suspicions/review`（**新建**）—— 人工核实，body `{suspicion_id, action: confirm|reject|need_more_evidence, comment?}`，按五态流转表迁移 `verify_status`。

### 6.7 Step7 文书生成（P8-8）

- `POST /api/audit/documents/batch`（现状 `phase6_routes.py:206`，独立端点，方案 4.5.5）—— 输出 `documents[]` 四件套（取证单/审计底稿/审计报告初稿/定性复核意见书）（附录A §8）。
- **上下文改造**：后端按 `task_id` 从已确认疑点 + 证据 + 法规构建（替代前端 `_buildDocContext` 从 DOM/localStorage 拼）；`source_refs` 继承已确认疑点证据链，不重新自由生成。
- 前置 evidence_complete 检查：疑点已确认/数据证据/文档引用/法规存在。

### 6.8 三道控制层统一接口（P8-1/P8-5/P8-8 共用）

- `GET /api/audit/analysis/{id}/readiness?stage=entry|data_ready|evidence_complete`（附录A §9）—— 输出 `{stage, ready, checks[{name, pass, detail, source}]}`。
- entry 在 Step1 前、data_ready 在 Step5 前、evidence_complete 在 Step7 前。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| **F3** `audit_agent_traces` 只建表不落库 | P8-11 补 `BaseAgent._persist_trace()`，run 末尾写库 |
| **F4** LangGraph `SqliteSaver` 与 MySQL `audit_analysis_tasks` 双状态源 | 定 MySQL 为唯一权威源（current_step/step_data），sqlite 只作 graph 内部瞬时执行细节，不作为恢复依据 |
| **F5** graph state 存前端展示格式 | 引擎内用附录A 规范契约，路由出口层转前端展示格式（分离） |
| `audit_analyzer._detect_target_table` 猜表（字段签名+回退） | P8-6 改用 Phase7 `audit_engine_rules.target_table`（确定性）；保留 detect 仅作 Phase7 反填期兜底 |
| 前端 `this.step=N` 关键词硬切（6 处） | 删除；改「确认→后端推进→按响应渲染」（4.5.5） |
| 前端 resume 读 localStorage | 改 `resumeFromBackend(taskId)`，GET `/analysis/{id}` 驱动 |
| 前端固定消息 ID 全无 | 每步右栏加 `id="step-N-summary"`；后端 `audit_step_summaries` 持久化 |
| 前端 mock 残留（`compareRegulations` 写死4法规/`_initRecommendPool` 12静态/match:90+random） | 清理，改真实后端数据 |
| Step7 上下文前端 `_buildDocContext` 拼 | P8-8 改后端按 task_id 构建 |
| **Phase4 ⑤⑥⑦三表可能未落地** | §1 前置核实；缺则先补 M004 |
| recovery 分支未合并 | 不 cherry-pick；其 spec 18 节当蓝本参考 |
| LLM 请求混 HTML | `AnalysisContextBuilder` 装配纯文本；现状已无 HTML（base.py call_llm_json），保持 |
| graph 拓扑（ViolationMatcher 串行前置再扇出） | 按附录A Step 顺序理顺（实现 P8-3 时定具体边改造）；Step7 不入 graph |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：Phase 1-7 完成；Phase4 ⑤⑥⑦表已落地；项目 $PID 已立项+事项+资料+data_* 有行

# P8-1/P8-2 entry 检查 + Step1 建任务
curl -s "$BASE/analysis/$TID/readiness?stage=entry" | python -m json.tool   # 若需先建任务则调整
curl -s -X POST "$BASE/analysis" -H "Content-Type: application/json" \
  -d "{\"project_id\":\"$PID\",\"focus_item_id\":<iid>,\"user_intent\":\"核查采购合规\"}" | python -m json.tool
# 断言：返回 analysis_task（task_id/project_context/current_step=1）；entry 5 项检查

# P8-3 Step2 推荐（工作流内）
curl -s "$BASE/analysis/$TID" | python -m json.tool
# 断言：violation_candidates[] 含 engine_rule + audit_methods + match_score

# P8-4 Step3 法规确认
curl -s -X POST "$BASE/analysis/$TID/confirm" -H "Content-Type: application/json" \
  -d "{\"selected_violations\":[<vid>],\"selected_laws\":[<law_id>]}" | python -m json.tool
# 断言：law_recommendations[] 含 clause_text/source_refs；无来源标待人工核实

# P8-5 Step4 readiness（data_ready 7 项）
curl -s "$BASE/analysis/$TID/readiness?stage=data_ready" | python -m json.tool
# 断言：ready + checks[]；未就绪则 missing_items[] 列出

# P8-6 Step5 数据比对（确定性表）
curl -s -X POST "$BASE/analysis/$TID/step/4" -H "Content-Type: application/json" \
  -d "{\"project_id\":\"$PID\"}" | python -m json.tool
# 断言：exec_results[] target_table 来自 audit_engine_rules（非猜）；命中行带 field_sources

# P8-7 Step6 疑点（五态）
curl -s -X POST "$BASE/suspicion/generate" -H "Content-Type: application/json" \
  -d "{\"project_id\":\"$PID\",\"task_id\":\"$TID\"}" | python -m json.tool
curl -s -X POST "$BASE/analysis/$TID/suspicion/review" -H "Content-Type: application/json" \
  -d "{\"suspicion_id\":<sid>,\"action\":\"confirm\"}" | python -m json.tool
# 断言：suspicion_candidates verify_status=MODEL_FOUND；review 后流转到 CONFIRMED；落 project_suspicions

# P8-8 Step7 文书（后端上下文）
curl -s "$BASE/analysis/$TID/readiness?stage=evidence_complete" | python -m json.tool
curl -s -X POST "$BASE/documents/batch" -H "Content-Type: application/json" \
  -d "{\"project_id\":\"$PID\",\"task_id\":\"$TID\"}" | python -m json.tool
# 断言：documents[] 四件套；source_refs 继承已确认疑点；evidence_complete 4 项

# P8-11 trace 落库
curl -s -X GET "mysql://... SELECT COUNT(*) FROM audit_agent_traces WHERE task_id=$TID" 
# 断言：每步 Agent 调用都有 trace 行（非空）

# P8-9 固定消息 ID（前端校验：浏览器 DevTools 查 #step-1-summary..#step-7-summary 存在）
# P8-10 LLM 无 HTML：抓 call_llm_json 入参，确认纯文本
# P8-12 质量评测
cd backend && python -m pytest tests/test_p8_seven_step.py -v
# 断言：七步契约断言 + 黄金集准确率/漏报/误报达标
```

> 仿 `test_p1_flow.py` 写 `backend/tests/test_p8_seven_step.py`，覆盖：entry/data_ready/evidence_complete 三道检查、七步 current_step 推进、疑点五态流转、trace 落库、文书证据继承、固定消息 ID。

## 9. 完成标准（汇总）

- [ ] 前置：Phase 4 ⑤⑥⑦三表已落地（核实/补建）
- [ ] `M008` 迁移成功（`audit_step_summaries` ⑧ + `verify_status` ⑪），可回滚
- [ ] P8-1：entry 门禁 5 项检查接 `setup_stage`/`audit_items`/权限
- [ ] P8-2：Step1 `analysis_task` 落库（project_context 仅 DB，current_step=1）
- [ ] P8-3：Step2 candidate 含 `engine_rule`+`audit_methods`，`match_score` 规则排序
- [ ] P8-4：Step3 法规带 `source_refs`，无来源标待人工核实
- [ ] P8-5：`/readiness?stage=data_ready` 7 项检查，拦截未就绪进 Step5
- [ ] P8-6：Step5 用 `audit_engine_rules.target_table`（确定性，禁猜），逐行 `field_sources→chunk`
- [ ] P8-7：疑点 `verify_status` 五态 + 落 `project_suspicions` + 核实 API 流转
- [ ] P8-8：Step7 后端按 task_id 构建上下文，证据继承，AI 不创造事实
- [ ] P8-9：前端 `step-N-summary` 固定 ID + 后端 `audit_step_summaries` 持久化（覆盖式）
- [ ] P8-10：`AnalysisContextBuilder` 装配，LLM 请求无 HTML
- [ ] P8-11：`BaseAgent._persist_trace()` 落 `audit_agent_traces`（F3 修复）
- [ ] P8-12：AI 质量评测达标（准确率/漏报/误报，报告入 `TEST_REPORT_PHASE_8.md`）
- [ ] 前端：删 `this.step` 自推进 + localStorage resume，改后端权威渲染
- [ ] F4/F5：MySQL 唯一权威源；引擎内规范契约、路由出口转前端格式
- [ ] 8 节验收脚本全通过（七步端到端跑通）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1-7 行为未破坏）
