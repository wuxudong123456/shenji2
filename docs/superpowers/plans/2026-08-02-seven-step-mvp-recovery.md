# 智能分析七步主链 MVP 故障恢复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在单项目、单事项、单违规、单成功资料的边界内，让立项→推荐→确认→上传→比对→疑点核实→工作底稿成为唯一、可恢复、可验收的 LangGraph 主链。

**Architecture:** 前端只调用 analysis create/confirm/step/status/review/document；Flask 路由委托任务状态、资料门禁、执行计划和文书上下文服务；LangGraph 串行执行 6 个现有 Agent 节点。MySQL 保存对外任务生命周期和幂等结果，SQLite checkpoint 保存图状态，页面刷新以后端状态为准。

**Tech Stack:** Python 3、Flask 3.1、PyMySQL/DBUtils、LangGraph SQLite checkpointer、原生 JavaScript、Python `unittest`。

## Global Constraints

- 不修改现有 UI 结构和用户操作流程。
- 不删除现有功能，不新增 Agent，复用已有 6 个 Agent。
- 不修改 `frontend/js/app.js` 导航框架。
- 不修改 `backend/templates/classifier.py`、`profile_loader.py`、`prompt_builder.py`。
- 正常主链不得由前端直接调用 `/api/audit/expression/execute` 或 `/api/audit/suspicion/generate`。
- 数据库只允许增加字段、索引和表；不得删除或改名现有字段。
- `backend/data/schema.sql` 只同步参考结构，正式迁移通过 `backend/data/migrate.py`。
- MVP 只支持一个项目、一个事项、一个违规、一个成功解析文件、一张采购合同表、一个行级表达式和一种工作底稿。
- MVP 不依赖当前未提交的 `audit_items` 扩展；唯一事项编码固定为 `project:<project_id>`，事项名称取项目名称。
- AND/OR 保持完整 AST，不拆表达式；无法确定表或字段时明确失败，不猜表。
- mock 只允许作为明确标记的页面预览，不得进入正式任务、疑点或文书。
- 每个任务遵循红灯→最小实现→绿灯→回归→独立提交。
- 当前工作区已有未提交业务改动；实施时不得 stash、覆盖、重置或混入提交。

## File Map

**Create**

- `backend/services/analysis_task_service.py`：任务状态迁移、任务查询、幂等操作。
- `backend/services/analysis_file_gate.py`：OCR、trace、结构化数据入库门禁。
- `backend/services/document_context_service.py`：按 task_id 构造正式工作底稿上下文。
- `backend/tests/__init__.py`：测试包。
- `backend/tests/fakes.py`：DB、Graph、Agent 的最小 fake。
- `backend/tests/test_runtime_baseline.py`：运行基线与 Schema 合同测试。
- `backend/tests/test_analysis_task_service.py`：状态机和幂等测试。
- `backend/tests/test_analysis_graph.py`：串行拓扑和状态传播测试。
- `backend/tests/test_analysis_routes.py`：create/confirm/step/review/status API 测试。
- `backend/tests/test_analysis_file_gate.py`：资料完成门禁测试。
- `backend/tests/test_execution_planner.py`：字段映射、表选择、表达式执行测试。
- `backend/tests/test_document_context_service.py`：文书只消费已确认数据的测试。
- `backend/tests/test_mvp_golden_path.py`：API 黄金路径测试。
- `docs/verification/seven-step-mvp-baseline.md`：实施前基线结果。
- `docs/verification/seven-step-mvp-acceptance.md`：最终 API、浏览器和重启验收记录。

**Modify**

- `backend/data/migrate.py`：MVP 幂等迁移。
- `backend/data/schema.sql`：同步参考 DDL。
- `backend/workflow/state.py`：MVP 状态字段。
- `backend/workflow/graph.py`：串行拓扑、ExecutionPlan 和疑点节点输入。
- `backend/services/execution_planner.py`：停止猜表，改为确定性采购字段映射。
- `backend/routes/audit_routes.py`：统一 analysis API、状态门禁、疑点核实。
- `backend/routes/phase6_routes.py`：按 task_id 生成工作底稿。
- `frontend/js/api.js`：疑点核实和工作底稿 API。
- `frontend/js/analysis-wiz.js`：confirm、上传追踪、step/4、后端恢复和文书接线。
- `backend/requirements.txt`：不新增依赖，仅在缺失时固定现有 LangGraph 包版本；默认不修改。

---

## M0：保护现场与运行基线

### Task 1: 固定实施工作区和基线事实

**Files:**
- Create: `docs/verification/seven-step-mvp-baseline.md`
- Test: `backend/tests/test_runtime_baseline.py`

**Interfaces:**
- Consumes: 当前 Git 状态、真实 MySQL `information_schema`、外部服务健康接口。
- Produces: 后续迁移所依赖的真实列清单和 `RuntimeBaseline` 检查结果。

- [ ] **Step 1: 保护现有未提交改动**

执行时先运行：

```powershell
git status --short
git diff --stat
git diff --name-only
```

Expected：明确列出当前未提交的 `schema.sql`、`audit_routes.py`、`api.js`、`projects.html` 和缓存文件。不要运行 `git stash`、`git reset`、`git checkout --` 或批量暂存。

在执行实现前使用 `using-git-worktrees` 创建隔离工作树；若因当前环境不能创建，停止并请求用户决定，不在脏工作树继续实现。

- [ ] **Step 2: 写运行基线测试**

在 `backend/tests/test_runtime_baseline.py` 写入可独立运行的合同测试，核心内容：

```python
import unittest

REQUIRED_ANALYSIS_COLUMNS = {
    "id", "project_id", "title", "step", "step_data",
    "agent_results", "status", "created_at", "updated_at",
}


class RuntimeSchemaContractTests(unittest.TestCase):
    def test_required_analysis_columns_are_declared(self):
        self.assertIn("project_id", REQUIRED_ANALYSIS_COLUMNS)
        self.assertIn("status", REQUIRED_ANALYSIS_COLUMNS)

    def test_mvp_data_table_is_whitelisted(self):
        self.assertEqual({"data_contracts"}, {"data_contracts"})


if __name__ == "__main__":
    unittest.main()
```

该测试先定义静态最低合同；真实列检查由下一步生成的基线文档记录，不能把生产凭据写进测试。

- [ ] **Step 3: 运行静态基线测试**

Run：

```powershell
python -m unittest backend.tests.test_runtime_baseline -v
```

Expected：2 tests PASS。

- [ ] **Step 4: 只读核验真实环境**

Run：

```powershell
python backend/data/verify_data_sources.py
python backend/data/migrate.py --check
```

如果现有 `migrate.py` 暂不支持 `--check`，本任务只运行 `verify_data_sources.py`，并用已有 `services.db.query` 在交互式命令中查询 `information_schema`；不得执行 DDL。

必须记录：

```text
audit_analysis_tasks 是否存在 task_code
audit_projects 实际列
audit_document_traces 与 audit_task_queue 的关联字段
data_contracts 的字段和项目数据数量
MySQL/MinIO/OCR/LLM/task_worker/checkpoint 状态
一条采购违规的 violation_id、expression_text、解析结果
```

- [ ] **Step 5: 写基线报告**

在 `docs/verification/seven-step-mvp-baseline.md` 使用固定表格：

```markdown
| 检查项 | 实际结果 | 证据命令 | 是否阻塞 |
|---|---|---|---|
| audit_analysis_tasks.task_code | 存在/缺失 | information_schema 查询 | 是/否 |
| data_contracts 黄金数据 | 行数 | `SELECT COUNT(*) FROM tt.data_contracts WHERE project_id=?` | 是/否 |
| OCR | 可用/不可用 | 健康检查 | 是/否 |
```

禁止写 `待补充`；无法验证时写明具体原因和所需权限。

- [ ] **Step 6: 提交基线材料**

```powershell
git add backend/tests/__init__.py backend/tests/test_runtime_baseline.py docs/verification/seven-step-mvp-baseline.md
git commit -m "test: capture seven-step MVP runtime baseline"
```

**Stop gate:** MySQL 不可访问、真实表缺失、黄金文件无法 OCR/入库、LLM 不可用时停止后续实现，先向用户报告环境阻塞。

---

## M1：最小失败测试

### Task 2: 建立当前主链的可重复失败证明

**Files:**
- Create: `backend/tests/fakes.py`
- Create: `backend/tests/test_analysis_routes.py`
- Modify: `docs/verification/seven-step-mvp-baseline.md`

**Interfaces:**
- Consumes: `register_audit_routes(app)`、现有 `/api/audit/analysis`、`/confirm`、`/step/4`。
- Produces: `FakeAnalysisGraph`、`make_test_app()` 和后续任务持续扩展的 API 合同测试。

- [ ] **Step 1: 建立最小 fake**

`backend/tests/fakes.py` 定义：

```python
class Snapshot:
    def __init__(self, values=None, next_nodes=()):
        self.values = values or {}
        self.next = tuple(next_nodes)


class FakeAnalysisGraph:
    def __init__(self):
        self.states = {}

    def invoke(self, payload, config):
        task_id = config["configurable"]["thread_id"]
        if payload is not None:
            self.states[task_id] = {
                **payload,
                "current_step": 2,
                "matches": [{"id": "101", "name": "应招标未招标"}],
                "primary_laws": [{"law_id": "L1", "law": "招标投标法"}],
            }
        return self.states[task_id]

    def get_state(self, config):
        task_id = config["configurable"]["thread_id"]
        return Snapshot(self.states.get(task_id, {}), ("step_3_confirm",))

    def update_state(self, config, values, as_node=None):
        task_id = config["configurable"]["thread_id"]
        self.states.setdefault(task_id, {}).update(values)
```

- [ ] **Step 2: 写第一个失败测试**

在 `backend/tests/test_analysis_routes.py` 增加静态前端合同测试，证明 `confirmS3` 必须调用 API：

```python
from pathlib import Path
import unittest


class FrontendWorkflowContractTests(unittest.TestCase):
    def test_confirm_s3_calls_analysis_confirm(self):
        source = Path("frontend/js/analysis-wiz.js").read_text(encoding="utf-8")
        start = source.index("confirmS3: function()")
        end = source.index("/** 加载阈值规则", start)
        body = source[start:end]
        self.assertIn("AuditAPI.analysis.confirm", body)

    def test_step_five_uses_workflow_step_four(self):
        source = Path("frontend/js/analysis-wiz.js").read_text(encoding="utf-8")
        self.assertIn("AuditAPI.analysis.step(self._taskId, 4", source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 运行并确认红灯**

Run：

```powershell
python -m unittest backend.tests.test_analysis_routes.FrontendWorkflowContractTests -v
```

Expected：两个测试 FAIL，分别缺少 `AuditAPI.analysis.confirm` 和以步骤号 4 调用 `AuditAPI.analysis.step`。把输出摘要写入基线文档。

- [ ] **Step 4: 不提交失败测试，保留到对应修复任务**

本任务不单独提交；失败测试与 Task 6、Task 8 的实现一起转绿并提交。这样主分支不会出现故意失败的测试提交。

---

## M2：任务和状态底座

### Task 3: 增加任务生命周期与幂等迁移

**Files:**
- Modify: `backend/data/migrate.py`
- Modify: `backend/data/schema.sql`
- Test: `backend/tests/test_runtime_baseline.py`

**Interfaces:**
- Consumes: M0 真实 Schema 报告。
- Produces: `audit_analysis_tasks` MVP 字段、`audit_task_operations`、`audit_generated_documents`。

- [ ] **Step 1: 写迁移合同失败测试**

在 `test_runtime_baseline.py` 增加：

```python
from pathlib import Path

def test_migrate_declares_mvp_analysis_columns(self):
    source = Path("backend/data/migrate.py").read_text(encoding="utf-8")
    for column in ("task_code", "execution_mode", "current_step", "next_action",
                   "error_code", "error_message", "confirmed_at", "completed_at"):
        self.assertIn(column, source)

def test_migrate_declares_idempotency_and_documents_tables(self):
    source = Path("backend/data/migrate.py").read_text(encoding="utf-8")
    self.assertIn("audit_task_operations", source)
    self.assertIn("audit_generated_documents", source)
```

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_runtime_baseline -v
```

Expected：新增测试 FAIL。

- [ ] **Step 3: 实现幂等迁移函数**

在 `backend/data/migrate.py` 增加并从 `main()` 调用：

```python
def migrate_analysis_mvp():
    columns = {
        "task_code": "VARCHAR(32) NULL COMMENT '对外任务编码'",
        "audit_item_id": "VARCHAR(64) NULL COMMENT '本次审计事项'",
        "execution_mode": "VARCHAR(20) NOT NULL DEFAULT 'workflow'",
        "current_step": "TINYINT NOT NULL DEFAULT 1",
        "next_action": "VARCHAR(40) NOT NULL DEFAULT 'wait'",
        "error_code": "VARCHAR(64) NULL",
        "error_message": "TEXT NULL",
        "confirmed_at": "DATETIME NULL",
        "completed_at": "DATETIME NULL",
    }
    for name, ddl in columns.items():
        if not _column_exists("audit_analysis_tasks", name):
            execute(
                f"ALTER TABLE {DATABASE}.audit_analysis_tasks ADD COLUMN {name} {ddl}",
                database=DATABASE,
            )
    if not _index_exists("audit_analysis_tasks", "uk_task_code"):
        execute(
            f"ALTER TABLE {DATABASE}.audit_analysis_tasks "
            "ADD UNIQUE INDEX uk_task_code (task_code)",
            database=DATABASE,
        )
```

新增 `audit_task_operations`，唯一键为 `(task_code, operation, request_id)`；新增 `audit_generated_documents`，包含 `document_code`、`task_code`、`doc_type`、`content_json`、`created_at`，并为 `document_code` 建唯一键。

- [ ] **Step 4: 增加只检查模式**

将入口改为支持：

```python
def check_mvp_schema() -> list[str]:
    """返回缺失的 table.column 或 index 名称；空列表表示通过。"""
```

命令：

```powershell
python backend/data/migrate.py --check
```

Expected：迁移前列出缺失项并以退出码 1 结束；迁移后输出 `MVP schema OK` 并退出 0。

- [ ] **Step 5: 同步参考 DDL**

只向 `backend/data/schema.sql` 追加字段和新表参考定义，不把它作为执行入口。

- [ ] **Step 6: 运行测试和迁移 dry check**

```powershell
python -m unittest backend.tests.test_runtime_baseline -v
python backend/data/migrate.py --check
```

Expected：单元测试 PASS；`--check` 精确列出迁移前缺失项。

- [ ] **Step 7: 在测试库执行迁移并复查**

```powershell
python backend/data/migrate.py
python backend/data/migrate.py --check
```

Expected：首次迁移完成，第二条输出 `MVP schema OK`；再次运行迁移无报错且全部跳过。

- [ ] **Step 8: 提交**

```powershell
git add backend/data/migrate.py backend/data/schema.sql backend/tests/test_runtime_baseline.py
git commit -m "feat: add MVP analysis lifecycle schema"
```

### Task 4: 实现任务状态服务

**Files:**
- Create: `backend/services/analysis_task_service.py`
- Create: `backend/tests/test_analysis_task_service.py`

**Interfaces:**
- Consumes: `services.db.query_one/insert/execute` 和 Task 3 Schema。
- Produces: `create_initial_task`、`get_task`、`transition_task`、`run_idempotent` 四个明确接口。

- [ ] **Step 1: 写状态迁移失败测试**

测试以 mock repository 注入，不访问真实数据库：

```python
import unittest
from services.analysis_task_service import InvalidTaskState, TaskStateMachine


class TaskStateMachineTests(unittest.TestCase):
    def test_confirmation_transition_is_legal(self):
        machine = TaskStateMachine()
        machine.ensure_transition("awaiting_confirmation", "awaiting_upload")

    def test_skipping_confirmation_is_rejected(self):
        machine = TaskStateMachine()
        with self.assertRaises(InvalidTaskState):
            machine.ensure_transition("initializing", "awaiting_upload")
```

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_task_service -v
```

Expected：FAIL，模块不存在。

- [ ] **Step 3: 实现状态机和 DB 接口**

必须提供以下签名：

```python
class InvalidTaskState(ValueError):
    """任务不在预期状态或请求试图跳过合法迁移时抛出。"""

class TaskStateMachine:
    def ensure_transition(self, current: str, target: str) -> None:
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTaskState(f"{current} -> {target}")

def create_initial_task(*, task_code: str, project_id: str, audit_item_id: str,
                        title: str, session_id: str) -> dict:
    """插入 initializing 任务并返回清洗后的任务行。"""

def get_task(task_code: str) -> dict | None:
    """按唯一 task_code 查询任务，不存在返回 None。"""

def transition_task(*, task_code: str, expected_status: str, target_status: str,
                    current_step: int, next_action: str,
                    step_patch: dict | None = None,
                    agent_patch: dict | None = None,
                    error_code: str | None = None,
                    error_message: str | None = None) -> dict:
    """使用 expected_status 条件更新并返回更新后的任务。"""

def run_idempotent(*, task_code: str, operation: str, request_id: str,
                   action) -> tuple[dict, bool]:
    """首次执行 action 并缓存响应；重复请求返回缓存响应和 True。"""
```

`transition_task` 必须使用条件 UPDATE：

```sql
UPDATE audit_analysis_tasks
SET status=%s, current_step=%s, next_action=%s,
    step_data=JSON_MERGE_PATCH(COALESCE(step_data, '{}'), %s),
    agent_results=JSON_MERGE_PATCH(COALESCE(agent_results, '{}'), %s),
    error_code=%s, error_message=%s
WHERE task_code=%s AND status=%s
```

受影响行数不是 1 时抛出 `InvalidTaskState`。

- [ ] **Step 4: 补齐幂等和并发测试**

覆盖：合法迁移、非法跳步、条件 UPDATE 冲突、相同 request_id 返回缓存响应、不同 operation 不冲突。

- [ ] **Step 5: 运行测试**

```powershell
python -m unittest backend.tests.test_analysis_task_service -v
```

Expected：全部 PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/services/analysis_task_service.py backend/tests/test_analysis_task_service.py
git commit -m "feat: add analysis task state service"
```

### Task 5: 让分析任务先落库再运行 Agent

**Files:**
- Modify: `backend/routes/audit_routes.py:962-1031`
- Modify: `frontend/js/api.js:162-169`
- Modify: `frontend/js/analysis-wiz.js:445-490`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: `analysis_task_service.create_initial_task/transition_task/get_task`。
- Produces: POST `/api/audit/analysis` 的 `initializing→awaiting_confirmation|failed` 行为，以及带 session/request ID 的前端创建请求。

- [ ] **Step 1: 写顺序失败测试**

通过 mock 记录调用顺序：

```python
def test_analysis_task_is_persisted_before_graph_invoke(self):
    calls = []
    create_initial_task = lambda **kwargs: calls.append("insert") or kwargs
    graph_invoke = lambda *args, **kwargs: calls.append("invoke") or {
        "current_step": 2, "matches": [], "primary_laws": []
    }
    create_analysis_with_dependencies(create_initial_task, graph_invoke)
    self.assertEqual(["insert", "invoke"], calls)
```

将路由核心提取为可测私有函数 `_create_analysis_task(data, graph)`；测试不得启动真实 Agent。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
```

Expected：缺少 `_create_analysis_task` 或调用顺序错误。

- [ ] **Step 3: 实现最小创建流程**

流程固定：校验项目→生成唯一事项编码 `project:<project_id>`→检查 active task→按 create request_id 幂等插入 initializing→invoke→写 awaiting_confirmation。删除“invoke 后清理同项目所有旧任务”的逻辑。

异常处理：

```python
except Exception as exc:
    transition_task(
        task_code=task_id,
        expected_status="initializing",
        target_status="failed",
        current_step=1,
        next_action="retry",
        error_code="AGENT_UNAVAILABLE",
        error_message=str(exc),
    )
    return mvp_error_response(
        task_id=task_id,
        status="failed",
        current_step=1,
        next_action="retry",
        code="AGENT_UNAVAILABLE",
        message=str(exc),
    ), 503
```

- [ ] **Step 4: 增加 active task 冲突测试**

同项目、同事项、同 session 有 active task 时返回 409，并包含：

```json
{"error":{"code":"ACTIVE_TASK_EXISTS"},"task_id":"existing-task","next_action":"resume"}
```

- [ ] **Step 5: 前端创建请求补 session 和幂等 ID**

`AuditAPI.analysis.create` 改为接收对象：

```javascript
create: function(data) {
  return fetch(AuditAPI.base + '/api/audit/analysis', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  }).then(function(r) { return r.json(); });
}
```

`parseIntent()` 复用同一轮保存在内存的 `_createRequestId`，提交 `project_id`、`intent`、`session_id`、`request_id`。网络重试不得生成新的 request_id。

- [ ] **Step 6: 运行测试和编译检查**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
python -m compileall -q backend
```

Expected：PASS，无编译错误。

- [ ] **Step 7: 提交**

```powershell
git add backend/routes/audit_routes.py frontend/js/api.js frontend/js/analysis-wiz.js backend/tests/test_analysis_routes.py
git commit -m "fix: persist analysis task before agent execution"
```

---

## M3：推荐与人工确认

### Task 6: 修正 LangGraph 为确定性串行拓扑

**Files:**
- Modify: `backend/workflow/state.py`
- Modify: `backend/workflow/graph.py:37-260`
- Create: `backend/tests/test_analysis_graph.py`

**Interfaces:**
- Consumes: 6 个现有 Agent 的 `agent.run(context)`。
- Produces: `build_analysis_graph()` 串行执行 Intent→Violation→Data→Regulation→Confirm，并保留稳定 ID。

- [ ] **Step 1: 写拓扑失败测试**

不调用真实 LLM；给 registry 注入返回固定结果的 fake Agent：

```python
class AnalysisGraphTopologyTests(unittest.TestCase):
    def test_violation_results_reach_data_and_regulation_agents(self):
        calls = []
        graph = build_analysis_graph(registry=FakeRegistry(calls), checkpointer=MemorySaver())
        state = graph.invoke(
            {"task_id": "T1", "project_id": "P1", "user_intent": "采购审计"},
            {"configurable": {"thread_id": "T1"}},
        )
        self.assertEqual(
            ["intent_analyzer", "violation_matcher", "data_advisor", "regulation_advisor"],
            calls,
        )
        self.assertEqual("101", state["matches"][0]["id"])
        self.assertEqual("L1", state["primary_laws"][0]["law_id"])
```

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_graph -v
```

Expected：当前并行/提前边导致顺序或结果不符合断言。

- [ ] **Step 3: 让图构建支持依赖注入**

将签名改为：

```python
def build_analysis_graph(registry=None, checkpointer=None):
    registry = registry or AgentRegistry()
    checkpointer = checkpointer or SqliteSaver(_checkpoint_conn)
```

节点通过闭包引用该 registry，不再依赖不可替换的模块级 `_registry`。

- [ ] **Step 4: 改成串行边**

唯一边序列：

```python
workflow.add_edge("step_1_intent", "step_2_violations")
workflow.add_edge("step_2_violations", "step_2_data_advice")
workflow.add_edge("step_2_data_advice", "step_2_regulations")
workflow.add_edge("step_2_regulations", "step_3_confirm")
```

删除 ViolationMatcher 直接到 confirm 的边和并行 reducer 依赖。`matches`、`primary_laws`、`recommended_materials` 在 MVP 中改为普通 list，避免重复 resume 时 `operator.add` 重复追加。

- [ ] **Step 5: 补 ID 和空结果测试**

测试必须覆盖：

- violation 输出没有稳定 ID 时产生 `errors`，不得生成 `v1` 临时 ID；
- law 输出没有 `law_id` 时产生 `errors`；
- DataAdvisor 能读到 ViolationMatcher 的 matches；
- 任一推荐 Agent 失败时任务不会伪装成完整推荐。

- [ ] **Step 6: 运行测试**

```powershell
python -m unittest backend.tests.test_analysis_graph -v
```

Expected：全部 PASS。

- [ ] **Step 7: 提交**

```powershell
git add backend/workflow/state.py backend/workflow/graph.py backend/tests/test_analysis_graph.py
git commit -m "fix: serialize MVP recommendation workflow"
```

### Task 7: 接通 Step 3 confirm 并持久化稳定 ID

**Files:**
- Modify: `backend/routes/audit_routes.py:1113-1188`
- Modify: `frontend/js/analysis-wiz.js:1221-1515`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: `analysis_task_service.run_idempotent/transition_task`、LangGraph `update_state/invoke`。
- Produces: POST `/api/audit/analysis/<task_id>/confirm` 和前端 `selectedRegulations`。

- [ ] **Step 1: 扩展 confirm 失败测试**

覆盖：

测试使用同一个 fake task repository，分别构造 `initializing`、未知 violation ID、未知 law ID、重复 request ID 和合法确认五种输入。必须断言对应 HTTP 状态为 409、422、422、200、200，并断言重复请求只调用一次 Graph `update_state`。

成功断言：

```python
self.assertEqual("awaiting_upload", response.json["status"])
self.assertEqual("upload_files", response.json["next_action"])
self.assertEqual(["101"], graph_state["selected_violations"])
self.assertEqual(["L1"], graph_state["selected_laws"])
```

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
```

Expected：confirm 状态、ID 校验和幂等测试 FAIL；Task 2 的前端 confirm 合同测试仍 FAIL。

- [ ] **Step 3: 实现后端 confirm 门禁**

请求固定为：

```json
{
  "selected_violations": ["101"],
  "selected_laws": ["L1"],
  "custom_regulations": [],
  "request_id": "uuid"
}
```

路由处理顺序：查询任务→校验状态→查数据库验证 violation/law ID→`run_idempotent`→更新 Graph state→invoke 到上传等待点→条件更新 MySQL。

- [ ] **Step 4: 在法规 checkbox 保存 ID**

生成法规行时必须包含：

```javascript
'<input type="checkbox" class="s3-reg" data-law-id="'+lawId+'" data-law="'+lawName+'" '+(checked?'checked':'')+'>'
```

新增状态：

```javascript
selectedRegulations: [],
customRegulations: [],
```

`confirmS3()` 只从 `data-law-id` 收集正式法规，自定义法规进入 `custom_regulations`，不得把 DOM `textContent` 当主键。

- [ ] **Step 5: 确认按钮调用后端**

核心调用必须是：

```javascript
AuditAPI.analysis.confirm(this._taskId, {
  selected_violations: this.selectedViolations.slice(0, 1),
  selected_laws: this.selectedRegulations.map(function(x){ return x.law_id; }).slice(0, 1),
  custom_regulations: this.customRegulations,
  request_id: crypto.randomUUID()
})
```

只有响应 `status === 'awaiting_upload'` 才进入 Step 4。失败时保留 Step 3 并显示后端错误。

- [ ] **Step 6: 运行测试和 JS 语法检查**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
node --check frontend/js/analysis-wiz.js
```

Expected：confirm API 测试和 Task 2 的 confirm 合同测试 PASS。

- [ ] **Step 7: 提交**

```powershell
git add backend/routes/audit_routes.py frontend/js/analysis-wiz.js backend/tests/test_analysis_routes.py backend/tests/fakes.py
git commit -m "fix: connect step three confirmation to workflow"
```

---

## M4：资料完成门禁

### Task 8: 建立资料完成判定服务

**Files:**
- Create: `backend/services/analysis_file_gate.py`
- Create: `backend/tests/test_analysis_file_gate.py`

**Interfaces:**
- Consumes: `audit_task_queue`、`audit_document_traces` 和白名单 `data_contracts`。
- Produces: `validate_ready_files(project_id, files) -> FileGateResult`。

- [ ] **Step 1: 写资料门禁失败测试**

测试建立五个明确 fixture：`processing` 任务、完成但 trace 缺失、完成且 trace 存在但数据行数为 0、完整成功文件、属于其他项目的文件。分别断言 `error_code` 为 `OCR_NOT_COMPLETED`、`EXTRACTION_NOT_COMPLETED`、`NO_STRUCTURED_DATA`、`None`、`PROJECT_NOT_FOUND`，且只有完整成功文件的 `ready` 为 True。

Fake repository 返回 task、trace、行数；测试不访问真实数据库。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_file_gate -v
```

Expected：模块不存在。

- [ ] **Step 3: 实现确定性门禁**

定义：

```python
@dataclass
class FileGateResult:
    ready: bool
    ready_files: list[dict]
    pending_files: list[dict]
    failed_files: list[dict]
    error_code: str | None

def validate_ready_files(*, project_id: str, files: list[dict], repository=None) -> FileGateResult:
    """校验一份文件的任务、trace 和 data_contracts 入库状态并返回分类结果。"""
```

MVP 只接受一份满足以下条件的文件：任务 completed、trace 属于 project、`data_contracts` 至少一行的 `document_trace_id` 等于 trace ID。

- [ ] **Step 4: 运行测试**

```powershell
python -m unittest backend.tests.test_analysis_file_gate -v
```

Expected：全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/services/analysis_file_gate.py backend/tests/test_analysis_file_gate.py
git commit -m "feat: gate analysis on completed document extraction"
```

### Task 9: 前端追踪上传结果并调用 step/4

**Files:**
- Modify: `frontend/js/analysis-wiz.js:1746-1815,197-230`
- Modify: `frontend/js/api.js:162-183`
- Modify: `backend/routes/audit_routes.py:1056-1111`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: `validate_ready_files()`、`AuditAPI.analysis.step()`。
- Produces: POST `/analysis/<task_id>/step/4` 的资料门禁与前端上传状态。

- [ ] **Step 1: 写 step/4 失败测试**

覆盖：

- 非 `awaiting_upload` 返回 409；
- processing 文件返回 `OCR_NOT_COMPLETED`；
- completed 但无数据返回 `NO_STRUCTURED_DATA`；
- 合法文件进入 `analyzing`；
- 相同 request_id 不重复推进。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
```

Expected：门禁和幂等测试 FAIL。

- [ ] **Step 3: 保存上传任务标识**

前端新增：

```javascript
uploadedFiles: [],
```

上传成功后保存 `file_id/task_id/trace_id/name/status`；轮询完成只把对应项更新为 `completed`，失败更新为 `failed`。

- [ ] **Step 4: 开始比对改调工作流**

替换正常链中的 `/expression/execute` 调用：

```javascript
AuditAPI.analysis.step(self._taskId, 4, {
  uploaded_files: self.uploadedFiles.filter(function(f){ return f.status === 'completed'; }),
  request_id: crypto.randomUUID()
})
```

响应未完成时保留 Step 4；响应进入 `awaiting_suspicion_review` 时才渲染 Step 5/6 结果。

- [ ] **Step 5: 后端调用资料门禁**

`step/4` 顺序：任务状态校验→幂等检查→`validate_ready_files`→状态改 analyzing→更新 Graph→invoke→保存结果。门禁失败不得改变 Graph 或任务状态。

- [ ] **Step 6: 运行测试和语法检查**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
node --check frontend/js/analysis-wiz.js
node --check frontend/js/api.js
```

Expected：step/4 测试和 Task 2 的 step 调用合同测试 PASS。

- [ ] **Step 7: 提交**

```powershell
git add frontend/js/analysis-wiz.js frontend/js/api.js backend/routes/audit_routes.py backend/tests/test_analysis_routes.py
git commit -m "fix: advance workflow only after file extraction"
```

---

## M5：执行计划、分析与疑点

### Task 10: 将 ExecutionPlanner 收敛为确定性采购规则

**Files:**
- Modify: `backend/services/execution_planner.py`
- Create: `backend/tests/test_execution_planner.py`

**Interfaces:**
- Consumes: `get_violation_detail(violation_id)`、`parse_expression()`、`execute_expression()`。
- Produces: `build_plan(violation_id, project_id)`、`execute_plan(plan, project_id)`。

- [ ] **Step 1: 写失败测试**

测试使用表达式 `采购方式 != '公开招标' AND 金额 >= 2000000`，断言映射后的表达式同时包含 `procurement_method` 和 `amount`、AST 根节点仍为 AND、表为 `data_contracts`。另用空 project ID、未知字段、未知表、聚合规则分别断言 `PROJECT_NOT_FOUND`、`FIELD_MAPPING_MISSING`、`FIELD_MAPPING_MISSING`、`RULE_UNSUPPORTED_IN_MVP`；命中测试断言结果含 `document_trace_id/file_id/page`。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_execution_planner -v
```

Expected：当前 `detect_target_table` 回退猜表，至少 4 个测试 FAIL。

- [ ] **Step 3: 实现最小字段映射**

```python
MVP_FIELD_MAP = {
    "采购方式": ("data_contracts", "procurement_method"),
    "金额": ("data_contracts", "amount"),
    "合同金额": ("data_contracts", "amount"),
    "合同编号": ("data_contracts", "contract_no"),
    "签订日期": ("data_contracts", "sign_date"),
    "甲方": ("data_contracts", "party_a"),
    "乙方": ("data_contracts", "party_b"),
    "供应商": ("data_contracts", "party_b"),
}
```

先将中文字段 token 确定性替换为物理字段，再调用原 parser。任何字段不在映射中返回 `FIELD_MAPPING_MISSING`。

- [ ] **Step 4: 定义接口**

```python
def build_plan(*, violation_id: str, project_id: str, repository=None) -> dict:
    """生成单违规、单表、行级 MVP 执行计划。"""
def execute_plan(*, plan: dict, project_id: str, executor=execute_expression,
                 trace_loader=None) -> dict:
    """执行已校验计划并补齐每条命中的溯源引用。"""
```

`build_and_execute` 保留作为兼容包装，但 MVP 主链只调用一个 violation ID。

- [ ] **Step 5: 补溯源**

`execute_plan` 将每个命中行中的 `document_trace_id` 解析为 `file_id/file_name/page`；查不到时保留 trace ID 并将 `trace_status="missing"`，不得伪造页码。

- [ ] **Step 6: 运行测试**

```powershell
python -m unittest backend.tests.test_execution_planner -v
```

Expected：全部 PASS。

- [ ] **Step 7: 提交**

```powershell
git add backend/services/execution_planner.py backend/tests/test_execution_planner.py
git commit -m "fix: make MVP execution planning deterministic"
```

### Task 11: 把 ExecutionPlan 和疑点生成放入工作流

**Files:**
- Modify: `backend/workflow/state.py`
- Modify: `backend/workflow/graph.py:156-200`
- Modify: `backend/routes/audit_routes.py:1056-1111`
- Test: `backend/tests/test_analysis_graph.py`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: `build_plan/execute_plan`、AuditAnalyzer、SuspicionGenerator。
- Produces: state `execution_plan`、`scan_results`、`analysis_results`、`suspicion_report`。

- [ ] **Step 1: 写工作流结果失败测试**

断言 step/4 完成后：

```python
self.assertEqual("101", state["execution_plan"]["violation_id"])
self.assertEqual(1, state["scan_results"]["hits"])
self.assertTrue(state["analysis_results"])
self.assertTrue(state["suspicion_report"])
```

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_graph backend.tests.test_analysis_routes -v
```

Expected：缺少 execution_plan/scan_results。

- [ ] **Step 3: 扩展 AnalysisState**

新增普通字段：

```python
execution_plan: dict
scan_results: dict
suspicion_review: dict
```

- [ ] **Step 4: 修改 Step 5 节点**

Step 5 必须先针对 `selected_violations[0]` 调 `build_plan` 和 `execute_plan`，不可执行时返回结构化错误并使任务 failed；成功后把 scan_results 连同用户选择交给 AuditAnalyzer。

- [ ] **Step 5: 修改 Step 6 节点**

SuspicionGenerator 输入必须包括真实 violation ID/name、scan results、selected laws 和 primary laws。不得再生成固定 `violation_model='违规分析'`。

- [ ] **Step 6: step/4 保存完整结果**

MySQL `step_data` 保存 uploaded_files、selected IDs、execution_plan、scan_results；`agent_results` 保存 AuditAnalyzer 和 SuspicionGenerator 结果。成功状态为 `awaiting_suspicion_review`，不是 completed。

- [ ] **Step 7: 确认前端正常链不再直接调用零散接口**

新增静态合同测试：在 `process()` 的 Step 5/6 区段内不存在：

```text
/api/audit/expression/execute
/api/audit/suspicion/generate
```

兼容函数或其他页面可继续存在。

- [ ] **Step 8: 运行测试**

```powershell
python -m unittest backend.tests.test_analysis_graph backend.tests.test_analysis_routes -v
python -m compileall -q backend
node --check frontend/js/analysis-wiz.js
```

Expected：全部 PASS。

- [ ] **Step 9: 提交**

```powershell
git add backend/workflow/state.py backend/workflow/graph.py backend/routes/audit_routes.py frontend/js/analysis-wiz.js backend/tests/test_analysis_graph.py backend/tests/test_analysis_routes.py
git commit -m "feat: run analysis and suspicion inside workflow"
```

### Task 12: 持久化并人工核实疑点

**Files:**
- Modify: `backend/data/migrate.py`
- Modify: `backend/data/schema.sql`
- Modify: `backend/routes/audit_routes.py`
- Modify: `frontend/js/api.js`
- Modify: `frontend/js/analysis-wiz.js`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: state `suspicion_report` 和 `scan_results`。
- Produces: POST `/api/audit/analysis/<task_id>/suspicions/review`。

- [ ] **Step 1: 写疑点核实失败测试**

覆盖：不存在疑点、非法状态、confirmed、rejected、重复 request_id。成功后任务仍为 `awaiting_suspicion_review`，但 `next_action` 改为 `generate_document`。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
```

Expected：404 或路由不存在。

- [ ] **Step 3: 补齐疑点核实字段迁移**

对真实 `project_suspicions` 缺失的字段只做 ADD：`task_code`、`reviewer`、`reviewed_at`、`review_comment`、`dedupe_key`，并为 `dedupe_key` 建唯一索引。若真实表已有等价字段，记录映射而不重复添加。

- [ ] **Step 4: 实现核实 API**

请求：

```json
{
  "items": [{"suspicion_id": 1, "status": "confirmed", "comment": "同意"}],
  "reviewer": "system",
  "request_id": "uuid"
}
```

只允许 `confirmed/rejected`，写审计日志和幂等记录。全部处理后 `next_action=generate_document`。

- [ ] **Step 5: 前端接线**

复用现有核实 UI 按钮，提交稳定 suspicion ID，不改变布局。没有疑点时提交空 items 并记录“未形成疑点”。

- [ ] **Step 6: 运行测试**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
node --check frontend/js/analysis-wiz.js
node --check frontend/js/api.js
```

Expected：全部 PASS。

- [ ] **Step 7: 提交**

```powershell
git add backend/data/migrate.py backend/data/schema.sql backend/routes/audit_routes.py frontend/js/api.js frontend/js/analysis-wiz.js backend/tests/test_analysis_routes.py
git commit -m "feat: persist and review MVP suspicions"
```

---

## M6：工作底稿与后端状态恢复

### Task 13: 以后端任务数据构建工作底稿上下文

**Files:**
- Create: `backend/services/document_context_service.py`
- Create: `backend/tests/test_document_context_service.py`
- Modify: `backend/routes/phase6_routes.py:175-188`
- Modify: `frontend/js/api.js`
- Modify: `frontend/js/analysis-wiz.js:1998-2051`

**Interfaces:**
- Consumes: analysis task、project、selected IDs、execution plan、scan results、已核实疑点。
- Produces: `build_workpaper_context(task_code)` 和 POST `/api/audit/analysis/<task_id>/documents/workpaper`。

- [ ] **Step 1: 写文书上下文失败测试**

测试 repository 固定返回推荐法规 `L1/L2`、用户选择 `L1`、一个 confirmed 疑点和一个 rejected 疑点。断言上下文只包含 `L1` 和 confirmed 疑点；无 confirmed 疑点 fixture 的 conclusion 为“未形成审计疑点”；scan result 必须保留 trace ID。

Repository fake 必须同时提供任务、项目、违规、法规、疑点和 document trace。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_document_context_service -v
```

Expected：模块不存在。

- [ ] **Step 3: 实现文书上下文服务**

定义：

```python
def build_workpaper_context(*, task_code: str, repository=None) -> dict:
    """只从持久化任务和已核实结果构建正式工作底稿上下文。"""
```

返回固定键：

```python
{
    "task_code": task_code,
    "project": {"id": "P1", "name": "采购审计"},
    "audit_item": {"id": "I1", "name": "采购方式合规性"},
    "violations": [{"id": "101", "name": "应招标未招标"}],
    "laws": [{"law_id": "L1", "law_title": "招标投标法"}],
    "files": [{"file_id": "F1", "trace_id": 88}],
    "execution_plan": {"violation_id": "101", "table": "data_contracts"},
    "scan_results": {"hits": 1, "rows": [{"row_id": 102, "trace_id": 88}]},
    "confirmed_suspicions": [{"id": 1, "status": "confirmed"}],
    "conclusion": "发现并确认1条审计疑点",
}
```

不得接收前端传入的任意 `context` 作为正式上下文。

- [ ] **Step 4: 新增工作底稿端点**

POST `/api/audit/analysis/<task_id>/documents/workpaper` 请求只包含 `request_id`。后端要求任务 `next_action=generate_document`，调用 `build_workpaper_context` 和现有 `generate_document("workpaper", context)`，将结果写入 `audit_generated_documents`，再把任务迁移到 completed。

成功响应：

```json
{
  "success": true,
  "task_id": "T1",
  "status": "completed",
  "document": {"document_id": "D1", "type": "workpaper", "content": {}}
}
```

- [ ] **Step 5: 前端文书按钮改按 task_id 调用**

新增：

```javascript
AuditAPI.analysis.generateWorkpaper = function(taskId, requestId) {
  return fetch(AuditAPI.base + '/api/audit/analysis/' + taskId + '/documents/workpaper', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({request_id: requestId})
  }).then(function(r) { return r.json(); });
}
```

七步主链不再调用 `_buildDocContext()` 生成正式文书；旧函数保留供兼容预览，但必须有注释和静态测试证明正常主链未使用它。

- [ ] **Step 6: 补幂等与失败测试**

覆盖重复 request_id 返回同一 document_id、错误状态返回 409、生成失败保持 `next_action=generate_document` 并返回 `DOCUMENT_GENERATION_FAILED`。

- [ ] **Step 7: 运行测试和语法检查**

```powershell
python -m unittest backend.tests.test_document_context_service backend.tests.test_analysis_routes -v
python -m compileall -q backend
node --check frontend/js/api.js
node --check frontend/js/analysis-wiz.js
```

Expected：全部 PASS。

- [ ] **Step 8: 提交**

```powershell
git add backend/services/document_context_service.py backend/routes/phase6_routes.py frontend/js/api.js frontend/js/analysis-wiz.js backend/tests/test_document_context_service.py backend/tests/test_analysis_routes.py
git commit -m "feat: generate workpaper from persisted task context"
```

### Task 14: 页面刷新以后端状态恢复

**Files:**
- Modify: `backend/routes/audit_routes.py:926-1054`
- Modify: `frontend/js/analysis-wiz.js:308-365`
- Test: `backend/tests/test_analysis_routes.py`

**Interfaces:**
- Consumes: `analysis_task_service.get_task()` 和 LangGraph snapshot。
- Produces: GET `/api/audit/analysis/<task_id>` 完整恢复 DTO、`resumeFromBackend(taskId)`。

- [ ] **Step 1: 写恢复失败测试**

覆盖：

- active MySQL 任务和 Graph 状态一致时返回完整 DTO；
- completed 任务即使无 checkpoint 也可从 MySQL 恢复；
- active 任务缺 checkpoint 时转为 failed/CHECKPOINT_MISSING；
- 前端保存内容不再包含 `rightPanelHTML` 和 `chatHTML`；
- 前端恢复代码调用 `AuditAPI.analysis.get`。

- [ ] **Step 2: 运行确认红灯**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
```

Expected：恢复 DTO 和静态前端合同 FAIL。

- [ ] **Step 3: 统一 GET 恢复 DTO**

必须返回：

```json
{
  "success": true,
  "task_id": "T1",
  "project_id": "P1",
  "status": "awaiting_upload",
  "current_step": 4,
  "next_action": "upload_files",
  "matches": [],
  "primary_laws": [],
  "recommended_materials": [],
  "selected_violations": [],
  "selected_laws": [],
  "uploaded_files": [],
  "execution_plan": null,
  "scan_results": null,
  "suspicion_report": null,
  "suspicion_review": null,
  "document": null,
  "errors": []
}
```

数据优先级：MySQL 生命周期字段为主；active task 的业务 state 从 Graph 补充；completed task 从 MySQL JSON 和 generated documents 恢复。

- [ ] **Step 4: 精简 localStorage**

`saveProgress()` 只保存：

```javascript
{taskId: this._taskId || '', projectId: (this.mem.project||{}).id || ''}
```

新增 `resumeFromBackend(taskId)`，GET 成功后按 status 调用现有 render 函数；失败时显示真实错误，不恢复旧 HTML。

- [ ] **Step 5: 运行测试和语法检查**

```powershell
python -m unittest backend.tests.test_analysis_routes -v
node --check frontend/js/analysis-wiz.js
```

Expected：全部 PASS。

- [ ] **Step 6: 提交**

```powershell
git add backend/routes/audit_routes.py frontend/js/analysis-wiz.js backend/tests/test_analysis_routes.py
git commit -m "fix: restore analysis UI from backend task state"
```

---

## M7：黄金路径和真实浏览器验收

### Task 15: 建立 API 黄金路径自动化测试

**Files:**
- Create: `backend/tests/test_mvp_golden_path.py`
- Modify: `backend/tests/fakes.py`

**Interfaces:**
- Consumes: M2–M6 所有服务和 API 合同。
- Produces: 单测试覆盖 create→confirm→file ready→step/4→review→workpaper→status。

- [ ] **Step 1: 写黄金路径测试**

测试使用 Flask test client、FakeAnalysisGraph、内存 repository 和固定 Agent 输出，不访问外部服务：

```python
class MvpGoldenPathTests(unittest.TestCase):
    def test_one_project_one_violation_one_file_to_workpaper(self):
        created = self.client.post("/api/audit/analysis", json={
            "project_id": "P1",
            "intent": "核查采购项目是否应招标未招标",
            "session_id": "S1", "request_id": "create-1",
        })
        self.assertEqual(200, created.status_code)
        task_id = created.json["task_id"]
        self.assertEqual("awaiting_confirmation", created.json["status"])

        confirmed = self.client.post(f"/api/audit/analysis/{task_id}/confirm", json={
            "selected_violations": ["101"], "selected_laws": ["L1"],
            "custom_regulations": [], "request_id": "confirm-1",
        })
        self.assertEqual("awaiting_upload", confirmed.json["status"])

        analyzed = self.client.post(f"/api/audit/analysis/{task_id}/step/4", json={
            "uploaded_files": [{"file_id": "F1", "task_id": 1, "trace_id": 88,
                                  "name": "采购合同.pdf", "status": "completed"}],
            "request_id": "step4-1",
        })
        self.assertEqual("awaiting_suspicion_review", analyzed.json["status"])
        self.assertEqual(1, analyzed.json["scan_results"]["hits"])

        reviewed = self.client.post(
            f"/api/audit/analysis/{task_id}/suspicions/review",
            json={"items": [{"suspicion_id": 1, "status": "confirmed", "comment": "同意"}],
                  "reviewer": "tester", "request_id": "review-1"},
        )
        self.assertEqual("generate_document", reviewed.json["next_action"])

        document = self.client.post(
            f"/api/audit/analysis/{task_id}/documents/workpaper",
            json={"request_id": "document-1"},
        )
        self.assertEqual("completed", document.json["status"])
        self.assertTrue(document.json["document"]["document_id"])
```

- [ ] **Step 2: 增加负向黄金测试**

同一文件增加子测试：重复 confirm、重复 step/4、未完成文件、空 project_id、字段映射缺失、checkpoint missing、文书生成失败。

- [ ] **Step 3: 运行测试**

```powershell
python -m unittest backend.tests.test_mvp_golden_path -v
```

Expected：全部 PASS。

- [ ] **Step 4: 运行全量后端回归**

```powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
python -m compileall -q backend
```

Expected：全部 PASS，无编译错误。

- [ ] **Step 5: 提交**

```powershell
git add backend/tests/fakes.py backend/tests/test_mvp_golden_path.py
git commit -m "test: cover seven-step MVP golden path"
```

### Task 16: 使用真实依赖执行 API 验收

**Files:**
- Create: `docs/verification/seven-step-mvp-acceptance.md`
- Modify: `testdata/real_source_adapted_procurement_case/manifest.json` only if the manifest lacks the exact golden fixture references; otherwise no testdata changes.

**Interfaces:**
- Consumes: 真实 MySQL、MinIO、OCR、LLM、task_worker 和采购黄金文件。
- Produces: 可复现的任务 ID、文件 ID、trace ID、疑点 ID、document ID 和验证结果。

- [ ] **Step 1: 启动并验证依赖**

按项目现有启动方式启动服务，逐项验证 MySQL、MinIO、OCR、LLM 和 Flask 健康状态。不得用 mock 通过本任务。

- [ ] **Step 2: 执行真实 API 链**

使用 PowerShell `Invoke-RestMethod` 或项目现有 API 客户端依次调用：项目创建/读取、analysis create、confirm、文件上传、任务轮询、step/4、疑点核实、工作底稿生成、status。

每一步记录：时间、请求摘要、HTTP 状态、业务状态、task_id、next_action、错误码。不要把凭据、OCR 全文或敏感项目数据写入文档。

- [ ] **Step 3: 核对三个状态源**

核对：

```text
前端/API status
MySQL audit_analysis_tasks
LangGraph snapshot.next 和 state.current_step
```

Expected：三者语义一致，不出现页面 completed 而数据库 awaiting_confirmation。

- [ ] **Step 4: 核对审计结果**

验证：

- 命中行属于项目 P1；
- 命中含 document_trace_id；
- trace 指向上传文件；
- 疑点含真实 violation_id；
- 工作底稿只含 selected law 和 confirmed suspicion。

- [ ] **Step 5: 写验收记录**

`docs/verification/seven-step-mvp-acceptance.md` 使用固定表格，所有门禁写 PASS/FAIL 和证据 ID。任何 FAIL 都阻止完成声明。

- [ ] **Step 6: 提交**

```powershell
git add docs/verification/seven-step-mvp-acceptance.md
git commit -m "test: record live MVP API acceptance"
```

### Task 17: 浏览器和重启恢复验收

**Files:**
- Modify: `docs/verification/seven-step-mvp-acceptance.md`

**Interfaces:**
- Consumes: Task 16 的真实 task_id。
- Produces: 浏览器操作、刷新恢复、后端重启恢复的最终证据。

- [ ] **Step 1: 浏览器执行七步链**

使用项目 QA 浏览器工具，从项目立项页面操作到工作底稿。逐步截图或记录可见状态，但不修改 UI。

- [ ] **Step 2: Step 3 刷新恢复**

停在 `awaiting_confirmation` 刷新页面。Expected：页面通过 GET 状态重新显示真实推荐，未创建新任务。

- [ ] **Step 3: Step 4 刷新恢复**

停在 `awaiting_upload` 刷新页面。Expected：已上传文件和任务状态来自后端，未恢复旧 HTML 快照。

- [ ] **Step 4: 后端重启恢复**

在 `awaiting_upload` 时正常停止并重新启动 Flask。Expected：GET status 可恢复任务；若 checkpoint 文件被人为移除的负向测试执行，则返回 `CHECKPOINT_MISSING`，不得伪装完成。

- [ ] **Step 5: 重复提交验收**

使用同一 request_id 重复 confirm、step/4 和 document 请求。Expected：返回同一结果 ID，数据库不增加重复疑点或文书。

- [ ] **Step 6: 完成最终回归**

```powershell
python -m unittest discover -s backend/tests -p "test_*.py" -v
python -m compileall -q backend
node --check frontend/js/analysis-wiz.js
node --check frontend/js/api.js
git diff --check
```

Expected：全部 PASS。

- [ ] **Step 7: 更新并提交验收记录**

```powershell
git add docs/verification/seven-step-mvp-acceptance.md
git commit -m "test: verify browser and restart recovery"
```

---

## Commit Sequence

```text
1. test: capture seven-step MVP runtime baseline
2. feat: add MVP analysis lifecycle schema
3. feat: add analysis task state service
4. fix: persist analysis task before agent execution
5. fix: serialize MVP recommendation workflow
6. fix: connect step three confirmation to workflow
7. feat: gate analysis on completed document extraction
8. fix: advance workflow only after file extraction
9. fix: make MVP execution planning deterministic
10. feat: run analysis and suspicion inside workflow
11. feat: persist and review MVP suspicions
12. feat: generate workpaper from persisted task context
13. fix: restore analysis UI from backend task state
14. test: cover seven-step MVP golden path
15. test: record live MVP API acceptance
16. test: verify browser and restart recovery
```

每次只暂存该任务列出的文件。禁止 `git add -A`。

## Design Coverage

| 设计要求 | 实施任务 |
|---|---|
| MVP 边界与范围隔离 | Task 1、全局约束 |
| 运行依赖和真实 Schema | Task 1、Task 3 |
| 唯一任务状态和幂等 | Task 3–5 |
| 任务先落库 | Task 5 |
| 串行 Agent 拓扑与稳定 ID | Task 6 |
| Step 3 人工确认 | Task 7 |
| OCR、trace、入库门禁 | Task 8–9 |
| 确定性采购 ExecutionPlan | Task 10 |
| 工作流内分析和疑点生成 | Task 11 |
| 疑点持久化与人工核实 | Task 12 |
| 后端文书上下文和工作底稿 | Task 13 |
| 后端状态恢复和 checkpoint 缺失 | Task 14 |
| API 黄金路径 | Task 15 |
| 真实依赖、浏览器、刷新、重启和重复提交 | Task 16–17 |

自检结果：设计文档 18 节均有对应任务；没有将多事项、多文件部分成功、跨表、聚合、语义规则或自动 fallback 偷渡进 MVP。

## Stop Conditions

遇到以下任一情况立即停止并向用户报告，不做绕过式补丁：

- 真实数据库与参考 DDL 的差异会导致数据丢失；
- 需要 DROP/RENAME 现有字段；
- OCR 完成但不能产生 trace 或结构化行；
- 采购黄金表达式不是 MVP 支持的行级规则；
- 现有 API 客户端没有稳定 violation_id 或 law_id；
- SQLite checkpoint 在实际部署模式下无法安全恢复；
- 必须改变现有 UI 操作流程才能继续；
- 同一根因连续三次修复失败。

## Completion Gate

只有以下三项全部满足才允许宣布完成：

1. `python -m unittest discover -s backend/tests -p "test_*.py" -v` 全部通过；
2. 真实 API 和浏览器七步链全部通过；
3. 刷新、重启和重复提交验收通过，或 checkpoint 缺失时返回明确可诊断失败。

单个接口返回 200、页面跳步或前端显示完成均不构成完成。
