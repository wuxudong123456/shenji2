# 清岳区采购案例六事项可执行链修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留现有六个审计事项，通过真实文档重新抽取、确定性跨文档规则和事项级规则绑定，使六项均可执行，并稳定形成与案例答案一致的六个最终疑点。

**Architecture:** 通用行级表达式继续使用现有 `expression_engine`；采购项目所需的聚合、日期先后、比例、重复号码和供应商关联判断进入新的确定性跨文档规则执行器。两类执行器由统一注册表调度并输出同一结果契约，Step⑤无需识别实现差异。LLM只解释确定性结果和撰写疑点，不参与金额、日期、重复号码计算。

**Tech Stack:** Python 3.12、Flask、MySQL、现有 MinerU/OntoSKU 抽取链、纯 HTML/JavaScript 前端、现有 unittest/HTTP 验收脚本。

## Global Constraints

- 不修改 `backend/templates/classifier.py`、`profile_loader.py`、`prompt_builder.py`。
- 不改变现有 `/api/files/*`、`/api/templates/*` 兼容行为。
- 不修改 D 盘案例原件；只重新处理现有 PDF。
- 不用人工 INSERT 伪造分析结果；所有命中必须来自项目原始文档对应的数据行。
- 不用 LLM 替代金额、日期、比例、重复号码、联系方式比较。
- 所有写库脚本必须支持 `--dry-run`、项目级备份、幂等重跑和项目级回滚。
- 四川省货物、服务公开招标数额标准使用 4,000,000 元，不沿用测试规则中的 2,000,000 元。
- 最终验收：7个确定性检查形成6个去重疑点；每个疑点至少包含一个 `document_trace_id`。

---

## 文件结构

**新增：**

- `backend/services/rule_engine_registry.py`：统一注册、预检并调度行级/跨文档执行器。
- `backend/services/procurement_audit_rules.py`：7个采购确定性检查，仅查询项目级结构化数据。
- `backend/services/audit_item_rule_service.py`：按审计事项读取和维护可信规则绑定。
- `backend/data/seed_procurement_audit_rules.py`：幂等创建规则、法规关联和清岳项目事项绑定。
- `backend/data/reprocess_project_documents.py`：项目级备份、重抽取、验证、回滚。
- `backend/tests/test_procurement_audit_rules.py`：7个规则单元测试。
- `backend/tests/test_qingyue_reprocess.py`：分类、字段抽取和溯源测试。
- `backend/tests/test_qingyue_six_items_e2e.py`：六事项端到端验收。

**修改：**

- `backend/data/migrate.py`、`backend/data/schema.sql`：给 `audit_engine_rules` 增加执行器元数据。
- `backend/services/task_worker.py`：加强采购案例文件名优先分类和重抽取幂等性。
- `backend/services/field_mapper.py`：补合同、供应商、交付验收、发票付款字段映射。
- `backend/services/execution_planner.py`：改为调用统一规则注册表。
- `backend/routes/audit_routes.py`：增加事项规则查询；预检和扫描返回统一状态。
- `backend/agents/suspicion_generator.py`：按 `finding_key` 合并检查结果。
- `frontend/js/api.js`：增加事项规则查询调用。
- `frontend/js/analysis-wiz.js`：优先加载当前事项绑定规则，展示缺失资料和确定性执行结果。

---

### Task 1: 固化失败基线和结果契约

**Files:**
- Create: `backend/tests/test_procurement_audit_rules.py`
- Create: `backend/tests/test_qingyue_six_items_e2e.py`

**Interfaces:**
- Consumes: 当前 `build_and_execute(violation_ids, project_id)` 输出。
- Produces: 统一规则结果字段：`rule_code`、`violation_id`、`finding_key`、`executor_type`、`executable`、`total`、`hits`、`rows`、`evidence_refs`、`error`。

- [ ] **Step 1:** 建立7条检查的期望表：

  | 规则 | 事项 | 预期 | 最终疑点 |
  |---|---|---|---|
  | `GP-PLAN-001` | 事项1 | 三批合同合计4,188,800元且同属440万元年度项目 | `F01_SPLIT_TENDER` |
  | `GP-METHOD-001` | 事项2 | 年度项目超过400万元仍采用询价 | `F01_SPLIT_TENDER` |
  | `GP-SUPPLIER-001` | 事项3 | S01、S02联系电话和邮箱相同 | `F06_SUPPLIER_LINK` |
  | `GP-CONTRACT-001` | 事项4 | B02送货2025-05-18早于签约2025-05-20 | `F02_BACKDATED_CONTRACT` |
  | `GP-CONTRACT-002` | 事项4 | 追加166,752元÷1,389,600元=12% | `F03_EXCESS_ADDITION` |
  | `GP-ACCEPT-001` | 事项5 | B03验收2025-07-18早于送货和安装 | `F05_ACCEPTANCE_DATE` |
  | `GP-FINANCE-001` | 事项6 | 发票 `TEST-510025000002` 被两张凭证引用 | `F04_DUPLICATE_INVOICE` |

- [ ] **Step 2:** 写失败测试，要求每条结果带真实 `document_trace_id` 和原始文件名。
- [ ] **Step 3:** 写端到端失败测试，断言六个事项均至少有一个 `executable=true` 的绑定规则。
- [ ] **Step 4:** 运行：

  ```powershell
  backend\.venv\Scripts\python.exe -m unittest backend.tests.test_procurement_audit_rules -v
  backend\.venv\Scripts\python.exe -m unittest backend.tests.test_qingyue_six_items_e2e -v
  ```

  预期：因规则注册表和跨文档执行器尚不存在而失败。

---

### Task 2: 修复文档分类和字段抽取

**Files:**
- Modify: `backend/services/task_worker.py`
- Modify: `backend/services/field_mapper.py`
- Create: `backend/tests/test_qingyue_reprocess.py`

**Interfaces:**
- Consumes: 文件名、OCR Markdown、OntoSKU提取字段。
- Produces: `(table_name, row_dict, extra_fields)`；扩展字段保留在 `extra_fields`，核心字段落标准列。

- [ ] **Step 1:** 为案例文件名建立确定性分类测试：
  - `设备采购合同` → `data_contracts`
  - `报价函/市场询价/采购需求/采购审批` → `data_procurements`
  - `送货清单/安装调试/验收报告/固定资产登记` → `data_registers`
  - `发票/付款申请/银行回单/记账凭证` → `data_finance`
- [ ] **Step 2:** 补字段映射并测试：
  - 合同：`contract_no`、`party_a`、`party_b`、`amount`、`sign_date`、`procurement_method`。
  - 采购/供应商：项目名、批次、供应商、报价金额、电话、邮箱、采购方式。
  - 履约：批次、送货日期、安装日期、验收日期、设备数量、资产登记日期。
  - 财务：发票代码、发票号码、发票金额、付款申请号、凭证号、付款日期、收款方、引用发票号码。
- [ ] **Step 3:** 对无法映射到标准列的字段使用稳定中文键写入 `extra_fields`，不得只留在 `raw_text`。
- [ ] **Step 4:** 保证同一 `document_trace_id` 重抽取时先删除旧数据行，再插入新行并重建证据引用。
- [ ] **Step 5:** 运行 `test_qingyue_reprocess`，预期所有分类、金额单位、日期和票号断言通过。

---

### Task 3: 增加跨文档确定性规则执行器

**Files:**
- Create: `backend/services/procurement_audit_rules.py`
- Create: `backend/services/rule_engine_registry.py`
- Modify: `backend/services/execution_planner.py`
- Test: `backend/tests/test_procurement_audit_rules.py`

**Interfaces:**
- Produces: `execute_rule(rule_code: str, project_id: str, config: dict) -> dict`。
- Produces: `precheck_rule(rule_code: str, project_id: str, config: dict) -> dict`。
- `build_and_execute()` 对调用方保持现有签名。

- [ ] **Step 1:** 实现只读事实加载器，所有SQL必须带 `WHERE project_id=%s`。
- [ ] **Step 2:** 实现 `GP-PLAN-001`：按年度项目/采购类别聚合三份合同，比较4,000,000元阈值。
- [ ] **Step 3:** 实现 `GP-METHOD-001`：将年度预算和采购方式关联判断，不把每份资料分别计为疑点。
- [ ] **Step 4:** 实现 `GP-SUPPLIER-001`：标准化电话和邮箱后分组，至少两个不同供应商共用联系方式才命中。
- [ ] **Step 5:** 实现 `GP-CONTRACT-001/002`：按批次关联合同、送货和追加付款，分别判断日期和10%比例。
- [ ] **Step 6:** 实现 `GP-ACCEPT-001`：按批次比较验收、送货、安装日期。
- [ ] **Step 7:** 实现 `GP-FINANCE-001`：按发票号码分组，至少被两个不同凭证或付款申请引用才命中。
- [ ] **Step 8:** 统一结果中写入证据行、文件名、`document_trace_id`、计算过程和 `finding_key`。
- [ ] **Step 9:** 注册表同时支持：
  - `expression`：继续调用现有 `execute_expression`。
  - `procurement_cross_doc`：调用采购规则执行器。
- [ ] **Step 10:** 运行规则单元测试，预期7条检查全部可执行并按上表命中。

---

### Task 4: 迁移规则元数据并绑定六个事项

**Files:**
- Modify: `backend/data/migrate.py`
- Modify: `backend/data/schema.sql`
- Create: `backend/data/seed_procurement_audit_rules.py`
- Create: `backend/services/audit_item_rule_service.py`

**Interfaces:**
- `get_item_rules(project_id: str, item_id: int) -> list[dict]`。
- `audit_engine_rules` 新增：`executor_type`、`executor_key`、`rule_version`、`result_group_key`。

- [ ] **Step 1:** 写幂等迁移，新增字段默认保持旧规则为 `expression`，不破坏已有记录。
- [ ] **Step 2:** 以固定 `violation_code` 幂等创建7条采购规则，写入 `audit_engine_rules`；阈值写入 `threshold` JSON。
- [ ] **Step 3:** 将7条规则分别绑定到清岳项目事项182—187，事项4绑定两条。
- [ ] **Step 4:** 给规则绑定真实法规ID和条款；无条款原文时标记“待人工核实”，不伪造法规正文。
- [ ] **Step 5:** 种子脚本支持：

  ```powershell
  backend\.venv\Scripts\python.exe backend\data\seed_procurement_audit_rules.py --dry-run --project 3bf1fcf4fafb
  backend\.venv\Scripts\python.exe backend\data\seed_procurement_audit_rules.py --apply --project 3bf1fcf4fafb
  ```

- [ ] **Step 6:** 验证六事项映射数量为 `1,1,1,2,1,1`，无重复绑定。

---

### Task 5: 统一预检、扫描和疑点去重

**Files:**
- Modify: `backend/routes/audit_routes.py`
- Modify: `backend/agents/suspicion_generator.py`
- Test: `backend/tests/test_qingyue_six_items_e2e.py`

**Interfaces:**
- Add: `GET /api/audit/projects/{project_id}/items/{item_id}/violations`。
- Existing: `POST /api/audit/violations/preflight` 增加 `executor_type` 和缺失资料说明。
- Existing: `POST /api/audit/analysis/{task_id}/scan` 保持兼容。

- [ ] **Step 1:** 事项规则接口只返回桥表绑定的可信规则，主规则优先。
- [ ] **Step 2:** 预检分别返回：`hittable`、`missing_data`、`unsupported`；跨文档规则不再被伪SQL解析器误判语法错。
- [ ] **Step 3:** 扫描端点通过注册表执行规则，禁止在确定性扫描路径调用LLM。
- [ ] **Step 4:** 疑点生成前按 `finding_key` 聚合：`GP-PLAN-001` 与 `GP-METHOD-001` 合并为一个拆分采购疑点。
- [ ] **Step 5:** 涉及金额从确定性结果直接带入：4,188,800元、166,752元等，不允许输出“未量化”。
- [ ] **Step 6:** 疑点描述使用“模型发现/待核实”，法规和审计定性仍需人工确认。

---

### Task 6: 修复前端事项级推荐与执行反馈

**Files:**
- Modify: `frontend/js/api.js`
- Modify: `frontend/js/analysis-wiz.js`

**Interfaces:**
- Consumes: 事项规则接口和统一预检结果。
- Produces: Step②绑定规则清单、Step⑤逐规则结果和缺失资料提示。

- [ ] **Step 1:** 启动事项时优先请求该事项绑定规则，不再仅靠全库关键词推荐。
- [ ] **Step 2:** 将状态展示为：
  - `✓ 可执行`：资料齐全且执行器支持。
  - `△ 缺资料`：明确列出缺少合同、验收或财务结构化记录。
  - `× 不支持`：执行器未实现，不允许勾选。
- [ ] **Step 3:** 当前事项的主规则默认勾选，辅助规则由用户选择；不相关规则不得混入。
- [ ] **Step 4:** Step⑤展示计算过程、命中记录数、涉及金额和证据文件，聚合规则不显示“7条全部命中”的误导文案。
- [ ] **Step 5:** Step⑥展示6个去重疑点，并可从每个疑点跳转到原始文档溯源。

---

### Task 7: 安全重处理清岳项目59份PDF

**Files:**
- Create: `backend/data/reprocess_project_documents.py`

**Interfaces:**
- CLI: `--project`、`--dry-run`、`--backup`、`--apply`、`--verify`、`--rollback <backup>`。

- [ ] **Step 1:** 备份该项目的 `audit_document_traces`、8张 `data_*` 表、字段来源和证据引用到带时间戳JSON；记录行数与SHA-256。
- [ ] **Step 2:** `--dry-run` 列出待重处理的59份PDF、预期分类和目标表，不写库。
- [ ] **Step 3:** `--apply` 按 trace 逐份重抽取；单份失败不删除该份旧数据，并记录失败清单。
- [ ] **Step 4:** `--verify` 至少核验：
  - 3份采购合同进入 `data_contracts`，金额和签约日期正确。
  - 9份报价函可取得供应商、电话和邮箱。
  - 3组送货、安装、验收日期可关联。
  - 19份发票支付凭证进入 `data_finance` 或拥有等价结构化记录。
  - 每个关键事实都有 `document_trace_id`。
- [ ] **Step 5:** 任一关键事实缺失即停止规则验收；可用 `--rollback` 恢复项目级备份。

---

### Task 8: 全流程验收与回归

**Files:**
- Test: `backend/tests/test_qingyue_six_items_e2e.py`
- Modify: `docs/` 中对应测试说明（仅在实现完成后更新实际结果）。

- [ ] **Step 1:** 运行现有表达式、数据、七步流程回归测试，确认旧项目不受影响。
- [ ] **Step 2:** 对六个事项分别从项目页点击“启动分析”，验证聚焦事项ID正确传到任务。
- [ ] **Step 3:** 验证事项规则数为 `1,1,1,2,1,1`，7条全部 `executable=true`。
- [ ] **Step 4:** 执行Step⑤，验证7个检查的计算值与本计划Task 1期望表完全一致。
- [ ] **Step 5:** 执行Step⑥，验证最终恰好6个疑点，且：
  - 拆分采购疑点金额4,188,800元；
  - 超比例追加金额166,752元、比例12%；
  - 重复发票号码为 `TEST-510025000002`；
  - 每个疑点均有原文件和溯源锚点。
- [ ] **Step 6:** 验证无结果与错误状态区分明确：零命中不等于执行失败，缺资料不等于未发现问题。
- [ ] **Step 7:** 将实测截图、API响应摘要、数据库计数和测试日志保存为验收证据。

---

## 实施顺序与检查点

1. Task 1—2：先证明真实文档能进入正确表；否则不做规则层。
2. Task 3—4：完成确定性规则与事项绑定；检查点为7条规则全部通过单元测试。
3. Task 5—6：接通API和前端；检查点为每个事项只显示其可信规则。
4. Task 7—8：备份后重处理当前项目并端到端验收。

## 回滚范围

- 代码：在独立工作树/分支实施，不影响当前主工作区，验收前不合并。
- 数据：只操作 `project_id=3bf1fcf4fafb` 的项目数据和标记为 `qingyue-procurement-rules-v1` 的规则。
- 案例文件：D盘原件只读，不删除、不改名、不覆盖。
- 若重抽取失败，使用备份JSON按原主键/trace恢复；规则种子按 `violation_code` 和 `import_batch` 删除即可撤销。

## 完成定义

- 六个事项都能从项目页独立启动并加载绑定规则。
- 七个确定性检查全部可执行，不出现“语法错”或不明“缺字段”。
- 七个检查合并生成六个案例预设疑点。
- 金额、日期、票号和比例与案例答案完全一致。
- 每个疑点至少包含一个真实 `document_trace_id` 和原始文件名。
- 旧表达式规则和其他项目回归测试通过。
- LLM不可用时Step⑤仍能完成确定性扫描；只有解释/报告生成受影响。
