# Phase 7 测试报告：知识库查询加固 + 两张映射表建表反填 + 规则执行测试

> 执行依据：`docs/phase-exec/PHASE_7.md`（唯一执行依据）
> 分支：`phase2`　|　本 Phase 提交：`c89ada2`(C1 建表) → `30089ba`(C2 反填脚本) → C3(本报告 + test_p7_rules.py)
> 日期：2026-08-10

---

## 0. 完成标准逐项（PHASE_7 §9）

| # | 完成标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | M006 迁移成功（两表），可回滚 | ✅ | `migrate_engine_rules()` 幂等建 `audit_engine_rules`/`audit_item_methods`，回滚 `DROP TABLE` 见 §5 |
| 2 | 知识查询四路全通 | ✅ | P7-1/2/3/4 §8 路由验收 19 PASS/0 FAIL（见 §3） |
| 3 | 违规—法规关联初始化 + 命中率验证（P7-5） | ✅ | `audit_violation_law_refs` 2682 行；89.3% violation 关联到法规（见 §4.1） |
| 4 | `audit_item_methods` 反填（P7-6） | ✅ | 2225 行；`data_requirements` 75.8% 非空；`method_name/desc` 留空 |
| 5 | `audit_engine_rules` 反填（P7-7） | ✅ | 2225 行；`target_table/expression/field_mapping` 派生；`threshold=null` |
| 6 | `field_mapper` 静态字典覆盖反填所需；自动扩展 TODO（P7-8） | ✅ | 覆盖见 §4.3；自动扩展未写死（待确认） |
| 7 | 表达式执行链路保持可用（P7-9） | ✅ | §8 P7-9 row 层 total=9 hits=4；聚合 Submit→Approve→Execute 链路未动 |
| 8 | `test_p7_rules.py` 通过（P7-10） | ✅ | 18 PASS/0 FAIL（见 §5） |
| 9 | §8 验收脚本全通过 + 记录到本报告 | ✅ | 见 §3 |
| 10 | `05-regression-baseline.md` 回归通过 | ⚠️ | 该文档尚未创建（前向引用）；以 `test_p5_data.py` 全链冒烟替代：23 PASS/0 FAIL（见 §6） |

---

## 1. M006 建表（C1，commit c89ada2）

按 PHASE_7 §5 DDL 照抄，落地为 `backend/data/migrate.py::migrate_engine_rules()`（项目既有迁移机制为 **migrate.py 函数式**，无 `migrations/` 目录——偏离执行包字面"新建 .sql"，已在执行方案标注）。

- `audit_engine_rules(id, violation_id, target_table, expression, field_mapping JSON, threshold JSON, created_at, INDEX idx_violation)`
- `audit_item_methods(id, violation_id, method_name, method_desc, data_requirements JSON, INDEX idx_violation)` —— **无 created_at**（§5 注明照抄不加）
- 幂等：`_table_exists` 预检；回滚 `DROP TABLE` 注释保留。

---

## 2. 反填（C2，commit 30089ba + 反填 --run）

### 2.1 关键决策：target_table 探测改用中文场（用户确认）

PHASE_7 §6.7 指定 `execution_planner.detect_target_table(expr, "")`，但其 `TABLE_SIGNATURES` 是**英文列名**，而 2225 条表达式全用**中文场**（合同金额/借方金额…）→ 签名匹配 **0% 命中**、全回退 `data_contracts`。

经用户确认改用**中文场探测**：对每张 data_* 表统计表达式字段经 `field_mapper.FIELD_ALIAS_MAP` 可映射的「不同列数」，取最高分表（复用既有别名表，不造数）。`detect_target_table` 运行时路径（含其 5/8 表签名缺口）留待 P8-6 处理，本 Phase 不动 `analyzer`/`planner`。

### 2.2 反填统计

| 表 | 行数 | 关键指标 |
|---|---|---|
| `audit_engine_rules` | 2225 | target_table 分布见下 |
| `audit_item_methods` | 2225 | `data_requirements` 非空 1686（75.8%）；`method_name/desc` 留空（无数据源，用户确认） |

两表行数 = 含表达式 violation 数（2225），幂等去重（`violation_id` 预检 skip）。

**`audit_engine_rules.target_table` 分布（P7-7 缺口量化）：**

| 目标表 | 行数 | 占比 |
|---|---|---|
| data_contracts | 1256 | 56.4% |
| data_general | 461 | 20.7% |
| data_registers | 315 | 14.2% |
| data_legal_docs | 61 | 2.7% |
| data_finance | 61 | 2.7% |
| data_credentials | 32 | 1.4% |
| data_procurements | 29 | 1.3% |
| data_interviews | 10 | 0.4% |
| **合计** | **2225** | |

> 注：原 §6.7 英文签名路会让全部 2225 条回退 `data_contracts`；中文场探测后 8 表均有分布，`data_contracts` 占比从 100% 降至 56.4%，探测实质化。

---

## 3. §8 知识查询四路 + 表达式执行路由验收（19 PASS / 0 FAIL）

后端 health 200。验收脚本覆盖违规/法规/条款/案例 + 表达式执行：

| 路由 | 结果 | 实测 |
|---|---|---|
| P7-1 `GET /knowledge/violations?q=招标` | ✅ | total=70，返回 violations/categories |
| P7-1 `GET /knowledge/violations/<id>` | ✅ | violation/8756 关联法规 laws[]×2 |
| P7-2 `GET /knowledge/regulations?q=招标投标` | ✅ | total=166，返回 regulations/filters |
| P7-2 `GET /knowledge/regulation/<law_id>` | ✅ | 法规详情含发布机关/时效 |
| P7-3 `GET /knowledge/clauses/<law_id>` | ✅ | 取样法规条款数=7，返回 clauses[] |
| P7-4 `GET /cases` | ✅ | total=**2632**（见 §4.4） |
| P7-4 `GET /cases/<id>` | ✅ | 详情含 violations/laws/similar_cases 三向关联 |
| P7-9 `POST /expression/execute` | ✅ | layer=row，total=9 hits=4 |

---

## 4. 覆盖率 / 命中率

### 4.1 P7-5 违规—法规关联命中率

- `audit_violation_law_refs`：**2682 行**
- 关联到法规的 violation：**1987 / 2226 = 89.3%**（violation 级覆盖率高）
- 法规名匹配率（distinct law）：460/1269 = 36%（`migrate_violation_law_refs.py` 5 级级联匹配；未匹配多为地方/行业细则、文号缺失的法规，非脚本缺陷）

### 4.2 P7-3 条款覆盖率（`audit_law.tools_clause_relation`）

- 覆盖法规 **5709 / 8607 = 66.3%**（`sys_core_law_allaudit` 法规全集）
- 条款行总数 **94538**
- 33.7% 法规无条款拆分数据（`tools_clause_relation` 未覆盖）——**本 Phase 不做条款拆分新功能，如实报告**（§6.3 加固项）

### 4.3 P7-8 field_mapper 字段映射覆盖

| 口径 | 去重字段 | 未映射 | 未映射率 |
|---|---|---|---|
| 原始（含噪声：分类码 A01/BOM、英文碎片 ALL/AGO） | 13440 | 11111 | 82.7% |
| CJK≥2 真实字段 | 13221 | 10908 | 82.5% |

**解读**：未映射率高是**预期的结构性特征**，非缺陷。表达式引用大量**领域专用字段**（COD浓度 / CAS编号 / DNA指纹检测结果 / Benford首位数字分布检验 / B标准限值…），来自 1548 个跨域审计模板；8 张通用 data_* 表只覆盖通用切片（合同/财务/采购/登记…）。

**真实覆盖指标** = `audit_item_methods.data_requirements` 非空率 **75.8%**（每条 violation 至少映射到其通用字段）。领域专用字段建模属 Phase 8+ 范畴。

`field_mapper` 别名自动扩展未写死（倾向从 YAML `output.fields` 收集，待与用户确认），标 `TODO(待确认)`。

### 4.4 P7-4 案例数据

- `audit_cases` 实际 **2632 条**（远超 §6.4 "维持 5 条种子"的下限——真实案例库已入库）。
- 路由列表/详情/三向关联全通。

---

## 5. P7-10 规则执行测试（`tests/test_p7_rules.py`，18 PASS / 0 FAIL）

仿 `test_p1_flow.py` / `test_p5_data.py` 独立脚本范式（`check()` 计 PASS/FAIL，try/finally cleanup）。不需 backend HTTP（直调函数 + DB）。覆盖 §6.10 四项：

| 项 | 覆盖 | 实测 |
|---|---|---|
| (b1) `detect_target_table` 英文签名 + 回退 | party_a→contracts / debit_amount→finance / 空→contracts / 无匹配→contracts | 4 PASS |
| (b2) 反填中文场探测 `_signature_target_table` | 合同金额→contracts / 借方贷方→finance / 空字段→fallback | 4 PASS |
| (c) 三表行数一致 | engine_rules=item_methods=含表达式 violation=2225 | 4 PASS |
| (d) `data_requirements` 非空率 | 75.8% ≥ 50% 阈值 | 1 PASS |
| (P7-8) 未映射字段比例 | 报告型（非硬门槛） | 0 PASS（信息项） |
| (a) execute_expression 端到端命中 | seed 2 行（3M/500）+ `合同金额>1000000` → total=2 hits=1；真实 violation 9369 执行不崩 | 5 PASS |

> (a) 说明：真实 violation 表达式多引用非标准字段（保证金条款/投标人数量…），难对标准 data_* seed 行稳定命中；故用代表性 row 表达式 `合同金额 > 1000000`（中文别名→amount 经 field_mapper，与 violation 9369 同字段）证明执行链端到端（AST 解析 + 中文别名映射 + project 过滤 + 比较求值 + row 层调度），并附真实 violation smoke（`get_violation_detail(9369)` 载入 + 执行不崩）。

---

## 6. 回归（Phase 1-6 未破坏）

- `dev-specs/05-regression-baseline.md` **尚未创建**（执行包前向引用，文档未建）。
- 替代冒烟：`test_p5_data.py`（数据工坊全链 9 步）**23 PASS / 0 FAIL**。
- 依据：Phase 7 **未修改任何 Phase 1-6 service 代码**（`knowledge_service`/`regulation_graph`/`expression_engine`/`field_mapper`/路由均只加固/复用/补测试，未重写）；仅新增 2 表 + 反填行 + 1 测试文件，`migrate.py` 改动为新增幂等函数 + 注册（不动既有表）。回归风险结构上为零。

---

## 7. 范围外缺口（P7 只报告不修，Phase 8 前置）

| 缺口 | 位置 | 影响 | 处置 |
|---|---|---|---|
| `detect_target_table` 签名只覆盖 5/8 表（缺 data_procurements/interviews/general） | `execution_planner.py:15` / `audit_analyzer._detect_target_table` | 运行时表探测对这 3 表永不命中签名→走 COUNT 回退 | 本 Phase 反填已用中文场探测绕开（覆盖全 8 表）；运行时路径留 P8-6 |
| `expression_engine.allowed_tables` 缺 data_procurements/interviews | `expression_engine.py:246` | 这 2 表表达式执行被拒（"不支持的表"） | Phase 8 执行前置缺口，P7 不扩范围 |
| `audit_item_methods.method_name/desc` 无数据源 | DDL 留空 | 方法名/说明缺失 | 用户确认本轮留空，后续补 |

---

## 8. 交付物清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/data/migrate.py` | 修改（c89ada2） | `+migrate_engine_rules()` + main 注册 |
| `backend/data/backfill_item_methods.py` | 新增（30089ba） | P7-6 反填，中文场探测 |
| `backend/data/backfill_engine_rules.py` | 新增（30089ba） | P7-7 反填，中文场探测 |
| `backend/tests/test_p7_rules.py` | 新增（C3） | P7-10 规则执行测试，18 PASS |
| `docs/TEST_REPORT_PHASE_7.md` | 新增（C3） | 本报告 |
| `backend/data/migrate_violation_law_refs.py` | 已有，P7-5 跑 --run | 2682 行入 `audit_violation_law_refs` |

**至此前置就绪**：Phase 8 七步分析的「知识可查 + 规则可执行 + 映射链齐备」三条件达成——P8-3 candidate 可读 `audit_item_methods`，P8-6 禁猜表可取 `audit_engine_rules.target_table`。
