# 方案B：法规数据源切换到 auditkm_factory — 影响面清单

> **目标**: 将项目法规体系从 `audit_law` 库切换到 `auditkm_factory` 库（法规更全、条款更完整）
> **状态**: 影响面清单（待确认后再实施）
> **日期**: 2026-08-05

---

## 一、两个库的差异（实测）

| audit_law 表 | auditkm_factory 同表 | 差异 |
|---|---|---|
| `sys_core_law_allaudit` (3,591) | **6,0777 条** | 17 倍 |
| `sys_core_law` (353,069) | 351,065 | 相当 |
| `tools_clause_relation` (94,538) | **740,176 条** | 7.8 倍 |
| `tools_regulation_relation` (64,097) | 75,189 | 1.17 倍 |
| `sys_audititem_qualitative` (134,009) | 167,892 | 1.25 倍 |
| `sys_audititem_punish` (81,115) | 120,605 | 1.49 倍 |
| `tools_law_summary` (33,756) | 41,389 | 1.23 倍 |
| **`sys_audititem_SLFF` (1,983)** | **✗ 不存在** | ⚠️ 审计事项树 |
| **`sys_audititem_audititem_meta_SLFF`** | **✗ 不存在** | ⚠️ 连接表 |

### ⚠️ 最关键发现

**auditkm_factory 没有审计事项树（`sys_audititem_SLFF`）**。违规行为的 `audititem_id`（86% 挂树）关联的就是这张表。**这意味着法规体系可以切，审计事项树切不了**，必须形成"法规在 auditkm_factory、审计事项在 audit_law"的混合形态。

**且 auditkm_factory 的审计事项体系与 audit_law 完全不同**（已实测）：
- `audit_law.sys_audititem_SLFF.id` = `00zw18` / `01.01.001` 式
- `auditkm_factory.sys_audititem_qualitative.audititem_id` = 19 位雪花 ID（`1553263734567305217`）
- 抽样 200 个 auditkm_factory 的 audititem_id，**0 个**能在 audit_law.SLFF 找到

→ 这意味着 `sys_audititem_qualitative/punish`（定性/处罚依据）**也不能切到 auditkm_factory**，因为其 audititem_id 是 auditkm_factory 自己的审计事项体系，与违规挂载的 audit_law 树对不上。

### law_id 体系不通用

- audit_law 法规 id（`a0000227`）与 auditkm_factory（`a00000173662`）**不是同一体系**，仅 15% 重叠
- tt 库 668 个去重 law_id：87% 能在 auditkm_factory 法规库命中、70% 在 audit_law 命中

---

## 二、需要修改的后端代码（8 处）

| # | 文件 | 改动内容 |
|---|------|---------|
| 1 | `services/knowledge_service.py` | 法规搜索/详情/效力级别/时效性 的 `database="audit_law"` → `auditkm_factory`；**审计事项树查询保留 audit_law** |
| 2 | `services/regulation_graph.py` | 法规关系链 `tools_regulation_relation` + 条款 `tools_clause_relation` → auditkm_factory |
| 3 | `services/vector_store.py` | 法规向量索引 `sys_core_law_allaudit` → auditkm_factory |
| 4 | `services/threshold_extractor.py` | 法规匹配 `sys_core_law_allaudit` → auditkm_factory |
| 5 | `agents/regulation_advisor.py` | 法规引用数据源 → auditkm_factory |
| 6 | `agents/suspicion_generator.py` | 法规引用数据源 → auditkm_factory |
| 7 | `routes/phase6_routes.py` | case_detail 法规 JOIN → auditkm_factory |
| 8 | `mcp_servers/knowledge_mcp.py` | 间接调用 service，若 service 改则自动跟随（需验证） |

### 审计事项树相关（**全部保留在 audit_law**）

`knowledge_service.py` 的 `get_audititem_children/tree/search_audititems`、`get_audititem_law_refs`、`get_law_audititems`、`import_excel.py` 的 audititem 匹配 → **继续用 audit_law.sys_audititem_SLFF + sys_audititem_qualitative/punish**

> 已验证：auditkm_factory 的 qualitative/punish 用 19 位雪花 ID 审计事项体系，与违规挂载的 audit_law 树（`00zw18` 式）完全不同，**不能切换**。

---

## 三、tt 库数据影响

| 表 | 影响 |
|----|------|
| `audit_violation_law_refs`（2,627 条） | ⚠️ **law_id 全部重新匹配**为 auditkm_factory 体系（按 law_title 匹配，94% 成功率） |
| `audit_case_law_refs`（3,143 条） | ⚠️ **law_id 全部重新匹配**（继承违规的法规） |
| `audit_violations`（2,226 条） | 不影响（无 law_id） |
| `audit_cases`（2,632 条） | 不影响 |
| 历史 `audit_analysis_tasks` / `audit_conversations` | ⚠️ 旧的 audit_law law_id **永久失效**（JSON 里存的） |

---

## 四、前端影响

| 功能 | 影响 |
|------|------|
| 知识工坊法规库 | 数据源变 auditkm_factory，结果从 3588 → 6 万条，搜索体验变化 |
| 法规关系链 / 条款 | 跟随后端切换 |
| 违规详情法规依据 | law_id 变 auditkm_factory 体系，法规名/文号显示正常（法规库有） |
| 前端代码 | **基本不用改**（API 响应字段名不变，law_id 只当字符串用） |

---

## 五、实施步骤（确认后执行）

```
Step 1  备份: mysqldump tt.audit_violation_law_refs audit_case_law_refs
Step 2  后端 8 处代码改 database 指向 auditkm_factory
Step 3  重匹配 law_id: 用 auditkm_factory.sys_core_law_allaudit 按 law_title 重新匹配
        → 更新 tt.audit_violation_law_refs / audit_case_law_refs 的 law_id
Step 4  删 FAISS 法规缓存 law_index.pkl，重启后端重建
Step 5  验证: 法规搜索/详情/关系链/条款/违规法规依据
```

---

## 六、风险评估

| 风险 | 等级 | 说明 |
|------|------|------|
| **审计事项树跨库** | 🔴 高 | auditkm_factory 无 sys_audititem_SLFF，审计事项体系与法规体系分居两库，代码需长期区分 |
| **qualitative/punish 的 audititem_id 体系** | 🔴 高（已确认） | 实测 auditkm_factory 用 19 位雪花 ID，与违规挂载的 audit_law 树（`00zw18` 式）完全不同，**不能切换** |
| 历史 law_id 永久失效 | 🟡 中 | 旧分析任务的法规引用失效，不可逆 |
| 法规搜索质量变化 | 🟡 中 | auditkm_factory 法规多但可能含更多非审计法规，搜索结果变多 |
| 条款原文匹配率 | 🟢 低 | 提升到 42% 左右（条款表只覆盖一半法规），非 100% |
| 前端回归 | 🟢 低 | 字段名不变，风险小 |

---

## 七、结论 & 待确认

方案B是**全项目法规数据源切换**，不是改表这么简单：

1. **审计事项树跨库问题是最大障碍（已确认）**——auditkm_factory 没有 `sys_audititem_SLFF`，且其 qualitative/punish 用 19 位雪花 ID 审计事项体系，与违规挂载的 audit_law 树（`00zw18` 式）**完全不同**。必须接受"法规在 auditkm_factory、审计事项在 audit_law"的长期混合形态，且审计事项的定性/处罚依据查询**不能切**。

2. **条款原文匹配率只提升到 ~42%**（条款表覆盖一半法规），投入产出比需要评估。

3. **历史数据 law_id 永久失效**。

### 核心判断

方案B能带来的实质收益是：**条款原文显示率 36% → 42%**，以及法规库从 3588 → 6 万条（搜索范围更大，但可能混入非审计法规）。代价是：
- 8 个后端文件数据源切换 + 长期混合架构
- 审计事项体系与法规体系永久分居两库
- 历史 law_id 永久失效
- 回归测试范围大

**投入产出比需要你权衡。** 如果核心诉求只是"条款原文更全"，方案A（加条款表，46% 匹配率，改动最小）可能更合适。
