# PHASE_7 执行包：知识库与分析规则准备

> **执行协议**：本文件是 Phase 7 的**唯一执行依据**。执行者只读本文件，不要读主方案全文。
> 前置状态：Phase 1-6 已完成（数据工坊 8 表 + 行溯源 + 权限基线）。
> 铁律：**不复写** `knowledge_service` / `regulation_graph` / `expression_engine` / `field_mapper` 已有功能，只加固；映射表（`audit_engine_rules`/`audit_item_methods`）**必须反填脚本派生，禁止手工造数**；`match_score` 是规则排序权重，非 AI 打分（Phase 8 才用）；本轮**不做向量检索**（决策 9）。

---

## 0. 执行者须知（先读）

- **关键认知：知识库查询接口大半已实现（对接真实 service，非 mock）**：
  - 违规 / 法规 / 条款 / 案例查询路由已通，背后是 `knowledge_service` / `regulation_graph`（法规在 `audit_law` 库，违规/案例在 `tt` 库）；
  - 表达式引擎链路（`expression_parser` → `expression_classifier` → `expression_engine` → `sql_generator`）已通，`POST /expression/execute` 批量执行可用；
  - `field_mapper.get_column_for_expr_field()`（表达式中文场→表列）已有；`execution_planner.detect_target_table()`（表达式→目标表探测）已有。
  - 本 Phase 是**补全两张映射表 + 反填 + 加固 + 补测试**——不是从零搭知识库。
- **只做本 Phase 的事**：知识查询加固、映射链反填、规则执行测试。
  - **不做七步智能分析**（Phase 8）：本 Phase 只保证"知识可查、规则可执行、映射链齐备"，不串七步流程。
  - **不做 match_score 打分**（Phase 8）：本 Phase 不实现违规排序打分，`match_score` 字段留给 Phase 8。
  - **不做向量检索**（决策 9）：`vector_mcp` / `vector_store` 本轮排除，语义检索不在范围。
  - **不重构已有 service**：`knowledge_service` / `regulation_graph` / `expression_engine` 复用，只补缺、加固、加测试。
- **小功能切片**：按第 4 节 P7-1..P7-10 逐个推进，每个测试通过后才进入下一个。
- **数据库变更单独 commit**（M006，两张映射表，见第 5 节）。
- 完成后运行第 8 节验收脚本 + `dev-specs/05-regression-baseline.md`，两条都绿才算 Phase 7 完成。

## 1. 前置条件与决策依赖

| 前置 | 状态 | 说明 |
|---|---|---|
| Phase 1-6 | ✅ | 数据工坊 8 表 + 行溯源 + 权限基线（owner/member，P0-5 全局管理员细节待定） |
| 知识库基础数据 | ✅ | `audit_violations`（实际 ~2077 条；方案表述 2195，以实际库为准）/ `sys_audititem_SLFF` / `sys_core_law_allaudit` 已导入 |
| 违规-法规关联脚本 | ✅ 已有 | `data/migrate_violation_law_refs.py`（4 级匹配），P7-5 跑+验命中率 |
| 决策 9（无向量） | ✅ 已确认 | 本轮排除向量检索，`vector_mcp`/`vector_store` 不动 |
| P7-6 方法数据源 | ✅ 已确认 | 建表 + 反填 `data_requirements`（从 expression 解析）；`method_name`/`method_desc` **无数据源，本轮留空**后续补 |
| P7-4 案例范围 | ✅ 已确认 | 5 条手工种子（`seed_cases.py`）+ 查询全通即可，真实案例库导入不在本 Phase |

## 2. 目标

知识查询全通（违规 / 法规 / 条款 / 案例四路）；违规—法规关联数据初始化并验证命中率；**两张映射表（`audit_engine_rules` / `audit_item_methods`）建表 + 反填初始化**；字段映射加固；表达式规则解析 + 执行链路补测试。**至此 Phase 8 七步分析的「知识 + 规则」前置才算齐备。**

## 3. 核心规则（方案 §九 强制）

### 3.1 双库口径（不混用）

- **法规 / 审计事项 / 条款**在 `audit_law` 库：`sys_core_law_allaudit`（法规）/ `sys_core_law`（法规全量）/ `sys_audititem_SLFF`（审计事项树）/ `tools_clause_relation`（条款关系）。
- **违规 / 案例 / 映射**在 `tt` 库：`audit_violations` / `audit_cases` / `audit_violation_law_refs` / 本 Phase 新增 `audit_engine_rules` / `audit_item_methods`。
- 跨库 JOIN 用全限定表名 + `COLLATE utf8mb4_0900_ai_ci` 解决字符集不一致（现状已这么做，照搬，见 `audit_routes.py:992-998`）。

### 3.2 映射表必须反填初始化（禁止手工造数）

- `audit_engine_rules` / `audit_item_methods` 的数据**必须由反填脚本从 `audit_violations.expression_text` 自动派生**，禁止建空表后手工 INSERT（方案 §七已知坑）。
- `audit_item_methods.method_name` / `method_desc` 因 YAML `violations[]` 无对应字段（只有 expression/description/audit_item/suspicion/regulation 五个），**本轮留空**，不算造数；`data_requirements` 从 expression 解析引用字段派生（用户确认）。

### 3.3 match_score 是规则排序，非 AI 打分

- `audit_engine_rules` 不含 `match_score`；违规候选的排序打分归 Phase 8 Step2（方案 §八 Step2 输出 `match_score`）。本 Phase 只备数据，不打分。

### 3.4 不复写已有服务

- 知识查询复用 `knowledge_service` / `regulation_graph`；表达式执行复用 `expression_engine` / `execution_planner` / `sql_generator`；字段映射复用 `field_mapper`。本 Phase 只**补缺/加固/加测试**，不重写这些模块的已有函数。

## 4. 任务清单（P7-1 .. P7-10，逐个测试）

| # | 小功能 | 现状基础 | 完成标准 |
|---|---|---|---|
| P7-1 | 违规查询 | ✅ `knowledge_service.search_violations/count/detail` + 路由 | 查询/详情（含关联法规）全通；加固（无新建） |
| P7-2 | 法规查询 | ✅ `search_laws/get_law_detail` + 路由 | 检索/详情全通；加固 |
| P7-3 | 条款查询 | 🟡 `regulation_graph.get_law_clauses` + 路由 | 接口全通；**验证 `tools_clause_relation` 数据覆盖率**，空覆盖率列入报告 |
| P7-4 | 案例查询 | 🟡 `phase6_routes` 案例 CRUD + `seed_cases.py` 5 条 | 列表/详情/三向关联全通；维持 5 条种子（用户确认） |
| P7-5 | 违规—法规关联 | 🟡 `migrate_violation_law_refs.py`（4 级匹配） | 跑脚本灌数据；**验证命中率**（匹配/总数）入报告 |
| P7-6 | 违规—审计方法关联 | ❌ 表+脚本均无 | 建表（M006）+ 反填脚本（`data_requirements` 从 expression 解析；method 留空） |
| P7-7 | 违规—目标数据表关联 | ❌ 表无；`detect_target_table` 可复用 | 建表（M006）+ 反填脚本（target_table/expression/field_mapping；threshold 留 null） |
| P7-8 | 字段映射 | 🟡 `get_column_for_expr_field` 已有；`FIELD_ALIAS_MAP` 静态 | 核心复用；**别名自动扩展标 `TODO(待确认)`**（倾向从 YAML `output.fields` 收集，未定不写死） |
| P7-9 | 表达式规则 | 🟡 引擎链路通；零测试 | 行表达式直接执行 + 聚合表达式 Submit→Approve→Execute 链路保持；补单元测试 |
| P7-10 | 规则执行测试 | ❌ 无 | 已知 violation × 已知 data_* 命中正确（`tests/test_p7_rules.py`） |

**涉及文件**：
- `backend/data/migrations/M006_engine_rules.sql`（新增，两表）
- `backend/data/backfill_engine_rules.py`（新增，P7-7 反填）
- `backend/data/backfill_item_methods.py`（新增，P7-6 反填）
- `backend/data/migrate_violation_law_refs.py`（已有，P7-5 跑+验证）
- `backend/services/field_mapper.py`（P7-8 加固；自动扩展 TODO）
- `backend/services/knowledge_service.py` / `regulation_graph.py`（已有，加固/验证）
- `backend/services/expression_engine.py` / `execution_planner.py`（已有，P7-9）
- `backend/routes/audit_routes.py` / `phase6_routes.py`（已有路由，维持现状不重构）
- `backend/tests/test_p7_rules.py`（新增，P7-10）

## 5. 本 Phase DDL（M006，两张映射表，幂等，单独 commit）

> 严格照抄方案 §数据库 ⑩（`docs/审计工坊智能审计系统开发方案.md:585-604`），不增不减列。

```sql
-- ⑩ 智能分析引擎映射（Phase 7，反填初始化）
CREATE TABLE IF NOT EXISTS tt.audit_engine_rules (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  violation_id  INT NOT NULL COMMENT '关联 audit_violations',
  target_table  VARCHAR(100) COMMENT '目标 data_* 表',
  expression    TEXT COMMENT '分析规则伪SQL（缺省引用 violation.expression_text）',
  field_mapping JSON COMMENT '模型字段→表字段映射（复用 field_mapper）',
  threshold     JSON COMMENT '阈值配置',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_violation (violation_id)
) COMMENT '违规模型→分析规则映射（引擎执行）';

CREATE TABLE IF NOT EXISTS tt.audit_item_methods (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  violation_id     INT NOT NULL COMMENT '关联 audit_violations',
  method_name      VARCHAR(200) COMMENT '审计方法名称',
  method_desc      TEXT COMMENT '方法说明',
  data_requirements JSON COMMENT '数据字段要求清单',
  INDEX idx_violation (violation_id)
) COMMENT '违规模型→审计方法→数据字段要求';

-- 回滚（开发期用）
-- DROP TABLE IF EXISTS tt.audit_item_methods;
-- DROP TABLE IF EXISTS tt.audit_engine_rules;
```

> 注：`audit_item_methods` 方案 DDL 无 `created_at`（照抄不加）。`method_name`/`method_desc` 本轮反填留空（无数据源，用户确认）。

## 6. 本 Phase 接口契约（对照现状，直接可用）

### 6.1 违规查询（P7-1，已有，加固）

- `GET /api/audit/knowledge/violations` — 参数 `q/severity/is_reviewed/category/page/per_page`；返回 `violations/total/categories`（`audit_routes.py:953`）。
- `GET /api/audit/knowledge/violations/<id>` — 详情含 `audit_procedure/required_data` + 关联法规 `laws[]`（跨库 JOIN，`audit_routes.py:981`）。
- 加固项：确认 `categories` 下拉、关联法规 `matched` 标记正确。

### 6.2 法规查询（P7-2，已有，加固）

- `GET /api/audit/knowledge/regulations` — 参数 `q/potency_level/timeliness/page/per_page`；返回 `regulations/total/filters`（`audit_routes.py:1014`）。
- `GET /api/audit/knowledge/regulation/<law_id>` — 法规详情（发布机关/文号/施行日期/时效/效力级别）（`audit_routes.py:1040`）。
- `GET /api/audit/knowledge/regulation/<law_id>/graph` — 法规关系图（`audit_routes.py:1050`）。

### 6.3 条款查询（P7-3，已有，验证覆盖率）

- `GET /api/audit/knowledge/clauses/<law_id>` — 返回 `clauses/total`（`audit_routes.py:1058` → `regulation_graph.get_law_clauses`）。
- **加固项**：抽验 N 部法规，统计 `tools_clause_relation` 有条款数据的占比；覆盖率低的法规列入报告（本 Phase 不做条款拆分新功能，只如实报告覆盖率）。

### 6.4 案例查询（P7-4，已有，维持种子）

- `GET /api/audit/cases` — 参数 `q/domain/limit/offset`；返回 `cases/total/domains`（`phase6_routes.py:62`，含关联违规名/法规名聚合）。
- `GET /api/audit/cases/<id>` — 详情 + 三向关联（`violations/laws/similar_cases`）（`phase6_routes.py:112`）。
- `POST /api/audit/cases` — 创建案例（`phase6_routes.py:153`）。
- 维持 5 条种子（`seed_cases.py`），路由维持现状不重构（用户确认范围）。

### 6.5 违规—法规关联（P7-5，跑脚本 + 验命中率）

- 跑 `python data/migrate_violation_law_refs.py`（试运行）→ `--run`（正式）。
- 验证：统计 `audit_violation_law_refs` 命中率（有 `law_id` 的 violation 数 / 总数）；低命中率列入报告。关联数据供 P7-1 详情页 `laws[]` 展示。

### 6.6 违规—审计方法反填（P7-6，新建脚本）

- 新增 `data/backfill_item_methods.py`（仿 `import_violations.py` 范式：dry_run + `--run`）。
- 逻辑：遍历 `audit_violations`，每条 `expression_text` → 解析引用字段（复用 `expression_parser` 提取字段标识，或 `execution_planner` 的字段提取正则）→ 写 `audit_item_methods(violation_id, data_requirements=JSON数组, method_name='', method_desc='')`。
- `data_requirements` 示例：`["amount","procurement_method","sign_date"]`（表达式引用的列，经 `field_mapper.get_column_for_expr_field` 映射成表列名）。
- `method_name`/`method_desc` 留空字符串（无数据源，用户确认）。

### 6.7 违规—目标数据表反填（P7-7，新建脚本）

- 新增 `data/backfill_engine_rules.py`（dry_run + `--run`）。
- 逻辑：遍历 `audit_violations`，每条：
  - `expression` = `violation.expression_text`（方案"缺省引用"）；
  - `target_table` = `detect_target_table(expression, "")`（签名匹配优先，project_id 空只影响回退分支，可接受）；
  - `field_mapping` = 从 expression 解析中文场 → `field_mapper.get_column_for_expr_field` → JSON；
  - `threshold` = `null`（独立阈值规则走 `threshold_rules.yaml` + `threshold_service`，不在此重复）。
- 输出统计：总 violation 数 / 成功反填数 / 签名匹配命中表分布 / 回退 `data_contracts` 数。

### 6.8 字段映射（P7-8，加固 + TODO）

- 复用 `field_mapper.get_column_for_expr_field(field)`（表达式中文场→表列，P7-6/7 反填依赖它）。
- **别名自动扩展**：现状 `FIELD_ALIAS_MAP` 静态字典。方案 P7-8 要求"别名表自动扩展"但未定算法。
- `TODO(待确认)`：倾向"从 YAML `output.fields` 自动收集别名"替代静态字典；**未与用户确认前不写死实现**，本 Phase 先保证静态字典覆盖 P7-6/7 反填所需，自动扩展留作下一轮。

### 6.9 表达式执行（P7-9，已有，补测试）

- `POST /api/audit/expression/execute`（`audit_routes.py:1069`）：
  - 批量：`{violation_ids:[...], project_id}` → `build_and_execute` → `results:[{violation_id, expression, table, executable, total, hits, rows}]`；
  - 单表达式：`{expression, table?, project_id}` → `execute_expression`（table 空自动探测）。
- 聚合表达式 SQL 人工确认链（方案"Submit→Confirm→Execute"）：
  - `GET /api/audit/expression-sql/pending`、`POST /expression-sql/<cid>/approve`、`/reject`（`audit_routes.py:1121+`）。
- 阈值独立链路（不混淆）：`POST /threshold/check`（批量扫描）、`POST /threshold-table`（业务阈值×法规对照），走 `threshold_rules.yaml` + `threshold_service`。
- 加固项：补单元测试（见 P7-10），覆盖行表达式直接执行 + 表探测回退。

### 6.10 规则执行测试（P7-10，新建）

- 新增 `backend/tests/test_p7_rules.py`（仿 `test_p1_flow.py`）。
- 覆盖：(a) 已知 violation × 已知 data_* 行 → 命中正确；(b) `detect_target_table` 签名匹配 + 回退；(c) 反填后 `audit_engine_rules`/`audit_item_methods` 行数 = violation 数（含表达式者）；(d) `data_requirements` 非空率。

## 7. 已知坑与对策

| 坑 | 对策 |
|---|---|
| 方案表述 2195 violations，实际 ~2077 | 以实际库为准；反填脚本统计实际数，不硬编码 2195 |
| 双库字符集不一致（`tt` vs `audit_law`） | 跨库 JOIN 全限定表名 + `COLLATE utf8mb4_0900_ai_ci`（现状已做，照搬） |
| `tools_clause_relation` 覆盖率未知（P7-3） | 抽验统计覆盖率，低的列入报告；本 Phase 不做条款拆分新功能 |
| `audit_item_methods.method_name/desc` 无数据源 | 用户确认本轮留空（不造数）；`data_requirements` 从 expression 派生 |
| `detect_target_table` 签名匹配失败回退需 project_id | 反填传 `""`：签名命中优先，未命中回退 `data_contracts`（统计回退数入报告） |
| `audit_engine_rules.threshold` vs `threshold_rules.yaml` 易混 | 反填 `threshold=null`；可执行阈值规则独立走 `threshold_service` + `threshold_rules.yaml`，两套不重叠 |
| `field_mapper` 别名静态、不同步模板 | 本 Phase 只保证静态字典覆盖反填所需；自动扩展 `TODO(待确认)`，不写死 |
| 案例路由在 `phase6_routes.py` 内联 SQL、无 service | 维持现状不重构（用户确认范围）；如 Phase 8 需 service 再议 |
| 表达式引擎零测试 | P7-10 补 `test_p7_rules.py` |

## 8. 验收脚本（curl，直接可跑）

```bash
BASE=http://localhost:5000/api/audit
# 前置：Phase 1-6 完成；知识库基础数据已导入；项目 $PID 至少一张 data_* 表有行

# P7-1 违规查询
curl -s "$BASE/knowledge/violations?q=招标&page=1&per_page=5" | python -m json.tool
curl -s "$BASE/knowledge/violations/1" | python -m json.tool
# 断言：列表返回 violations/total/categories；详情含 laws[] 关联法规

# P7-2 法规查询
curl -s "$BASE/knowledge/regulations?q=招标投标&page=1&per_page=5" | python -m json.tool
curl -s "$BASE/knowledge/regulation/<law_id>" | python -m json.tool
# 断言：检索返回 regulations/total/filters；详情含发布机关/时效

# P7-3 条款查询（记录覆盖率）
curl -s "$BASE/knowledge/clauses/<law_id>" | python -m json.tool
# 断言：返回 clauses 数组；抽验 N 部法规统计条款覆盖率入报告

# P7-4 案例查询
curl -s "$BASE/cases?domain=政府采购审计" | python -m json.tool
curl -s "$BASE/cases/1" | python -m json.tool
# 断言：列表含 5 条种子；详情含 violations/laws/similar_cases 三向关联

# P7-5 违规-法规关联（先跑脚本）
cd backend && python data/migrate_violation_law_refs.py          # 试运行
cd backend && python data/migrate_violation_law_refs.py --run    # 正式
# 断言：统计命中率（有 law_id 的 violation / 总数）入报告

# P7-6 反填审计方法
cd backend && python data/backfill_item_methods.py              # 试运行
cd backend && python data/backfill_item_methods.py --run        # 正式
# 断言：audit_item_methods 行数 = 含表达式 violation 数；data_requirements 非空率入报告

# P7-7 反填引擎规则
cd backend && python data/backfill_engine_rules.py              # 试运行
cd backend && python data/backfill_engine_rules.py --run        # 正式
# 断言：audit_engine_rules 行数 = 含表达式 violation 数；target_table 分布入报告

# P7-9 表达式执行（批量 + 单表达式）
curl -s -X POST "$BASE/expression/execute" -H "Content-Type: application/json" \
  -d "{\"project_id\":\"$PID\",\"violation_ids\":[<vid1>,<vid2>]}" | python -m json.tool
# 断言：每个 violation 返回 table/executable/total/hits/rows

# P7-10 规则执行测试
cd backend && python -m pytest tests/test_p7_rules.py -v
# 断言：已知 violation × 已知 data_* 命中正确；反填行数对齐；data_requirements 非空率达标
```

## 9. 完成标准（汇总）

- [ ] 数据库 `M006` 迁移执行成功（两张映射表），可回滚
- [ ] 知识查询四路全通：违规（P7-1）/ 法规（P7-2）/ 条款（P7-3，覆盖率入报告）/ 案例（P7-4，5 条种子）
- [ ] 违规—法规关联数据初始化 + 命中率验证（P7-5）
- [ ] `audit_item_methods` 反填：`data_requirements` 从 expression 派生，`method_name/desc` 留空（P7-6）
- [ ] `audit_engine_rules` 反填：`target_table/expression/field_mapping` 派生，`threshold` 留 null（P7-7）
- [ ] `field_mapper` 静态字典覆盖反填所需；自动扩展标 `TODO(待确认)`（P7-8）
- [ ] 表达式执行链路（行表达式 + 聚合 Submit→Approve→Execute）保持可用（P7-9）
- [ ] `test_p7_rules.py` 通过：已知数据命中正确 + 反填行数对齐（P7-10）
- [ ] 8 节验收脚本全部通过（记录到 `docs/TEST_REPORT_PHASE_7.md`，含覆盖率/命中率/反填统计）
- [ ] `05-regression-baseline.md` 回归通过（Phase 1-6 行为未破坏）
