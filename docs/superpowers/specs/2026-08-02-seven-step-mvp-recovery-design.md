# 智能分析七步主链 MVP 故障恢复设计

> 日期：2026-08-02  
> 状态：设计已确认，待实施计划  
> 目标：在不改变 UI 结构、不新增 Agent 的前提下，恢复一条可运行、可恢复、可验收的七步真实主链。

## 1. MVP 边界

本轮只保证以下黄金路径：

```text
单项目
→ 单审计事项
→ 至少一个违规模型
→ 至少一部法规
→ 至少一份成功解析并入库的资料
→ 完成立项、推荐、确认、上传、比对、疑点、文书
→ 页面刷新后可按后端状态恢复
```

本轮不处理：

- 多审计事项并行；
- 多文件部分成功；
- 多违规并发；
- 跨表自动分析；
- 复杂聚合和语义表达式；
- OpenSquilla 迁移；
- MCP 工具注入；
- 其他页面 mock 替换；
- UI 布局改造；
- 新 Agent；
- 自动删除或批量清理历史任务。

当前未提交的 `audit_items` 等范围外功能先保存现场并隔离，不纳入本次闭环验收。

## 2. 设计决策

采用“LangGraph 唯一任务主线”方案。

```text
前端
  → create
  → confirm
  → upload
  → step/4
  → status
  → suspicion review
  → document
      ↓
LangGraph
  Step 1 IntentAnalyzer
  Step 2 ViolationMatcher
  Step 2B DataAdvisor
  Step 2C RegulationAdvisor
  Step 3 人工确认
  Step 4 等待资料完成
  Step 5 ExecutionPlanner + AuditAnalyzer
  Step 6 SuspicionGenerator
  END
```

正常任务中，前端不直接调用 `/expression/execute` 和 `/suspicion/generate`。现有端点保留兼容，但由工作流内部服务复用或供其他页面使用。

MVP 不启用自动 `direct_fallback`。Agent 或工作流失败时明确记录失败并允许重试，避免同一任务产生两套状态和重复副作用。

## 3. 运行前基线

实施前必须只读核验：

- MySQL、MinIO、OCR、LLM、task_worker 可用；
- SQLite checkpoint 目录可写；
- `audit_projects`、`audit_analysis_tasks`、`audit_document_traces`、`audit_task_queue`、`audit_violations`、`project_suspicions` 的真实结构；
- 六张 `data_*` 表的真实结构；
- `audit_analysis_tasks.task_code` 是否存在；
- 违规和法规推荐是否含稳定 ID；
- 采购审计表达式的实际格式和可解析比例；
- OCR 完成后是否确实产生 trace 和结构化数据行。

`backend/data/schema.sql` 仅作参考，真实数据库结构是迁移设计的输入。

## 4. 任务状态模型

### 4.1 对外状态

```text
initializing
awaiting_confirmation
awaiting_upload
analyzing
awaiting_suspicion_review
completed
failed
cancelled
```

### 4.2 合法迁移

```text
initializing
  ├→ awaiting_confirmation
  └→ failed

awaiting_confirmation
  ├→ awaiting_upload
  ├→ cancelled
  └→ failed

awaiting_upload
  ├→ analyzing
  ├→ cancelled
  └→ failed

analyzing
  ├→ awaiting_suspicion_review
  └→ failed

awaiting_suspicion_review
  ├→ completed
  └→ failed
```

任务响应至少包含：

```json
{
  "task_id": "",
  "project_id": "",
  "audit_item_id": "",
  "status": "initializing",
  "current_step": 1,
  "execution_mode": "workflow",
  "next_action": "wait",
  "error_code": null,
  "error_message": null
}
```

前端只能执行 `next_action` 允许的操作。

## 5. 数据库迁移

迁移必须通过 `backend/data/migrate.py` 的幂等预检模式执行，不在 Flask 启动时自动建表，不吞异常。

项目表补齐已确认的项目上下文字段。分析任务表根据真实 Schema 至少补齐：

- `task_code`，唯一；
- `audit_item_id`；
- `execution_mode`；
- `current_step`；
- `next_action`；
- `error_code`；
- `error_message`；
- `confirmed_at`；
- `completed_at`。

新增幂等操作表：

```text
audit_task_operations
- id
- task_code
- operation
- request_id
- response_json
- created_at
- UNIQUE(task_code, operation, request_id)
```

MVP 幂等操作包括：create、confirm、step/4、suspicion review、document generation。

迁移执行规则：

1. 查询 `information_schema`；
2. 已存在则跳过；
3. 非预期错误立即停止；
4. 下次运行可从已成功位置继续；
5. 全部完成后执行 Schema 校验；
6. 只有全部目标字段和索引存在才报告成功。

## 6. 任务创建

创建顺序固定为：

```text
校验项目和审计事项
→ 生成 task_id
→ INSERT status=initializing
→ 从数据库读取项目上下文
→ 启动 LangGraph
→ 成功：更新 awaiting_confirmation
→ 失败：更新 failed 和错误信息
```

不得先执行 Agent 再入库。

MVP 限制同项目、同事项、同浏览器会话只有一个 active task。已有 active task 时返回 `409` 和原 `task_id`，前端提供继续或明确取消，不自动取消同项目全部任务。

## 7. LangGraph 拓扑

MVP 优先使用确定性的串行拓扑：

```text
IntentAnalyzer
→ ViolationMatcher
→ DataAdvisor
→ RegulationAdvisor
→ Step 3 Confirm
→ Step 4 Upload
→ Step 5 Analysis
→ Step 6 Suspicion
→ END
```

删除 ViolationMatcher 直接进入 Step 3 的提前边。闭环稳定后可再优化为 DataAdvisor 和 RegulationAdvisor 并行汇合。

## 8. Step 3 人工确认

前端保存并提交：

```json
{
  "selected_violations": ["violation_id"],
  "selected_laws": ["law_id"],
  "custom_regulations": [],
  "request_id": ""
}
```

确认条件：

- 任务处于 `awaiting_confirmation`；
- 至少一个合法 `violation_id`；
- 至少一个合法 `law_id`；
- `request_id` 未处理过。

后端校验 ID，写入 LangGraph state 和 MySQL `step_data`，保存幂等结果，推进到 Step 4，并返回 `awaiting_upload`。只有成功响应后前端才能进入 Step 4。

## 9. 上传与解析门禁

MVP 要求至少一份资料满足：

```text
上传成功
AND audit_task_queue.status = completed
AND document trace 存在
AND 至少一张 data_* 表存在该项目的结构化数据行
```

前端记录 `file_id`、后台任务 ID、trace ID 和状态，但后端必须重新验证，不信任前端的完成标记。

条件未满足时 `step/4` 返回 `409`：

```json
{
  "next_action": "wait_for_files",
  "pending_files": [],
  "failed_files": []
}
```

MVP 只要求一份文件成功；失败文件不参与本轮黄金路径。

## 10. ExecutionPlan

MVP 仅支持：一个违规模型、一个完整行级表达式、一张白名单表、已知字段映射。

```json
{
  "violation_id": 2031,
  "violation_name": "应公开招标未招标",
  "expression": "采购方式 != '公开招标' AND 金额 >= 2000000",
  "execution_type": "row",
  "table": "data_contracts",
  "required_fields": ["采购方式", "金额"],
  "field_mapping": {
    "采购方式": "procurement_method",
    "金额": "amount"
  },
  "available_fields": ["procurement_method", "amount"],
  "executable": true,
  "reason": ""
}
```

执行规则：

- 不拆分 AND/OR，保留完整 AST；
- 表名必须在白名单中；
- `project_id` 不能为空，禁止主链扫描全库；
- 字段缺失则不执行；
- 无法确定表时不猜表、不默认回退；
- 聚合、跨表和语义规则返回 `RULE_UNSUPPORTED_IN_MVP`。

首轮字段映射仅覆盖采购合同黄金数据：

```text
采购方式 → data_contracts.procurement_method
金额/合同金额 → data_contracts.amount
合同编号 → data_contracts.contract_no
签订日期 → data_contracts.sign_date
甲方 → data_contracts.party_a
乙方/供应商 → data_contracts.party_b
```

## 11. Step 5 分析与溯源

`step/4` 通过门禁后：

1. 更新任务为 `analyzing`；
2. 生成 ExecutionPlan；
3. 执行完整表达式；
4. 保存执行计划和命中明细；
5. 调用 AuditAnalyzer；
6. 调用 SuspicionGenerator；
7. 更新为 `awaiting_suspicion_review`。

命中明细至少包含：

```json
{
  "violation_id": 2031,
  "row_id": 102,
  "table": "data_contracts",
  "expression": "",
  "fields": {},
  "document_trace_id": 88,
  "file_id": "doc_88",
  "page": 2
}
```

文件和页码通过 `document_trace_id` 查询，不由前端拼接。

## 12. 疑点核实

MVP 只支持 `confirmed` 和 `rejected` 两种人工结果。

疑点必须持久化：

- task_code；
- project_id；
- violation_id；
- 疑点内容；
- evidence_chain；
- status；
- reviewer；
- reviewed_at；
- review_comment。

确认疑点进入正式文书，排除疑点不进入正式文书。没有确认疑点时允许生成“未形成审计疑点”的结果文书。核实请求必须幂等。

## 13. 文书生成

MVP 只要求成功生成一种文书，默认选择“审计工作底稿”。

文书上下文只能由后端按 `task_id` 构建：

```text
项目上下文
+ 用户确认的违规模型
+ 用户确认的法规
+ 成功解析的资料
+ ExecutionPlan
+ 命中明细
+ 人工确认的疑点
```

前端不得从 DOM 或 localStorage 拼装正式文书上下文。生成结果保存文书 ID，刷新后可以重新查询。

## 14. 页面恢复

localStorage 只保存 `task_id` 和 `project_id`。

页面加载时调用：

```text
GET /api/audit/analysis/<task_id>
```

然后按后端状态重新渲染，不把 `rightPanelHTML` 当作业务状态。

恢复时 MySQL 是对外生命周期记录，LangGraph 是节点执行状态。active 任务需要同时查询 LangGraph。若 checkpoint 缺失，任务更新为 `failed/CHECKPOINT_MISSING`，不得伪装恢复成功。

## 15. 错误模型

统一失败响应：

```json
{
  "success": false,
  "task_id": "",
  "status": "failed",
  "current_step": 4,
  "next_action": "retry",
  "error": {
    "code": "OCR_NOT_COMPLETED",
    "message": "资料尚未完成解析"
  }
}
```

MVP 错误码：

```text
PROJECT_NOT_FOUND
ACTIVE_TASK_EXISTS
INVALID_TASK_STATE
INVALID_VIOLATION_ID
INVALID_LAW_ID
OCR_NOT_COMPLETED
EXTRACTION_NOT_COMPLETED
NO_STRUCTURED_DATA
EXPRESSION_INVALID
FIELD_MAPPING_MISSING
RULE_UNSUPPORTED_IN_MVP
AGENT_UNAVAILABLE
CHECKPOINT_MISSING
DOCUMENT_GENERATION_FAILED
```

mock 只能用于明确标记的预览，不写入正式任务、疑点或文书。

## 16. 实施阶段

### M0：保护现场与运行基线

- 保存并隔离范围外未提交改动；
- 查询真实数据库；
- 验证外部服务和 checkpoint；
- 输出失败基线。

### M1：最小失败测试

- 建立 API 黄金链测试；
- 确认当前在 Step 3 confirm 处失败；
- 后续每个修复都由同一测试推进。

### M2：任务与状态底座

- 数据库迁移；
- 任务先落库；
- 状态机；
- 幂等；
- 统一响应。

### M3：推荐与确认

- LangGraph 串行拓扑；
- 稳定 ID；
- confirm 接线；
- 用户选择持久化。

### M4：资料门禁

- 文件状态关联；
- OCR、提取、入库验证；
- step/4 门禁。

### M5：分析与疑点

- 最小字段映射；
- ExecutionPlan；
- 行级表达式执行；
- 溯源；
- AuditAnalyzer；
- SuspicionGenerator；
- 人工核实。

### M6：文书与恢复

- 后端构建文书上下文；
- 生成工作底稿；
- GET 状态恢复；
- checkpoint 异常处理。

### M7：黄金路径验收

- 使用固定采购审计数据运行 API 和浏览器全链；
- 验证刷新与服务重启场景。

## 17. 验收门禁

以下条件必须全部通过：

- 项目字段保存并可刷新读取；
- 任务先入库再运行 Agent；
- 同项目事项只有一个 active task；
- 返回真实 violation_id 和 law_id；
- confirm 后状态进入 `awaiting_upload`；
- 用户选择同时存在于 LangGraph 和 MySQL；
- 文件未解析完成时拒绝 step/4；
- 文件完成后至少一张 `data_*` 表有项目数据；
- ExecutionPlan 不猜表；
- AND/OR 不被拆分；
- `project_id` 为空时拒绝执行；
- 命中记录可关联 document trace；
- 疑点可以确认或排除；
- 文书只使用最终确认的法规和疑点；
- 正常链不直接调用 expression/suspicion 端点；
- 页面刷新以后端状态恢复；
- 服务重启后可恢复或明确返回可诊断失败；
- 重复 confirm、step/4 不重复执行；
- API 黄金链和浏览器真实链全部通过。

## 18. 完成定义

只有同时满足以下条件才能宣布 MVP 完成：

1. API 主链自动化测试通过；
2. 浏览器真实操作链通过；
3. 服务重启后任务可恢复，或明确返回可诊断失败。

单个接口返回 200、页面跳到下一步、前端显示“完成”，均不构成闭环完成。
