# 知识工坊数据库表结构设计规格书

> **范围**：知识工坊（违规库 / 案例库 / 三向关联）涉及的 `tt` 库 5 张表。
> **风格**：纯表结构规格书。每表含字段定义、键、约束、索引、示例行。
> **数据来源**：192.168.3.164 `tt` 库实测 `SHOW CREATE TABLE` + 真实样本行，非臆造。
> **生成日期**：2026-08-06
> **配套文档**：架构性分析见 [KNOWLEDGE_WORKSHOP_SCHEMA_DESIGN.md](KNOWLEDGE_WORKSHOP_SCHEMA_DESIGN.md)

---

## 1. 总览

知识工坊数据库共 5 张表，全部位于 `tt` 库：

| # | 表名 | 中文名 | 记录数 | 角色 |
|---|------|--------|--------|------|
| T1 | `audit_violations` | 违规行为库 | 2,226 | 违规模型主表（表达式 + 所需数据） |
| T2 | `audit_violation_law_refs` | 违规-法规关联 | 2,627 | 桥表（违规 ↔ 外部法规） |
| T3 | `audit_cases` | 审计案例库 | 2,632 | 案例主表 |
| T4 | `audit_case_violations` | 案例-违规关联 | 2,632 | 桥表 |
| T5 | `audit_case_law_refs` | 案例-法规关联 | 3,143 | 桥表（案例 ↔ 外部法规） |

**表间关系：**

```
audit_violations ──< audit_violation_law_refs >── (外部法规)
        │                                              ↑
        │                                              │
        └──< audit_case_violations >── audit_cases ──< audit_case_law_refs
```

> 法规主数据不在 `tt` 库。T2/T5 的 `law_id` 跨库指向外部 `audit_law.sys_core_law_allaudit.id`（详见 §7 附录）。

---

## 2. 字段表图例

| 列 | 含义 |
|----|------|
| **键** | `PRI` 主键 · `UNI` 唯一索引 · `MUL` 普通索引 · `FK` 外键 |
| **可空** | `NO` NOT NULL · `YES` 允许 NULL |
| 类型末尾的 `〔0900_ai_ci〕` | 该列单独声明排序规则 `utf8mb4_0900_ai_ci`（用于跨库 JOIN 对齐外部库） |

---

## 3. T1 `audit_violations` — 违规行为库

**用途**：每条记录是一个"违规模型"，描述违规行为、检测表达式、所需数据、关联法规与审计事项。
**字符集**：`utf8mb4_unicode_ci` · **引擎**：InnoDB

| # | 字段 | 类型 | 可空 | 键 | 默认 | 说明 |
|---|------|------|------|-----|------|------|
| 1 | `id` | int | NO | PRI | AUTO_INCREMENT | 主键 |
| 2 | `violation_code` | varchar(50) | YES | MUL | | 违规行为编码 |
| 3 | `violation_title` | text | YES | | | 违规行为名称 |
| 4 | `audititem_id` | varchar(32) | YES | MUL | | 关联审计事项分类（→ 外部 `sys_audititem_SLFF`） |
| 5 | `category_path` | varchar(500) | YES | | | 分类路径（`领域/子类`，前端分 Tab 用） |
| 6 | `severity` | varchar(20) | YES | | `medium` | 严重度：high / medium / low |
| 7 | `expression_text` | text | YES | | | 违规表达式伪 SQL（数据比对逻辑） |
| 8 | `audit_procedure` | mediumtext | YES | | | 审计方法步骤（Markdown） |
| 9 | `required_data` | json | YES | | | 审计所需数据 `{items:[{name,material_type,fields}]}` |
| 10 | `description` | text | YES | | | 违规描述 |
| 11 | `source_file` | varchar(255) | YES | | | 来源文件 |
| 12 | `author` | varchar(200) | YES | | | 来源单位 |
| 13 | `import_batch` | varchar(100) | YES | | | 导入批次 |
| 14 | `is_reviewed` | tinyint | YES | MUL | `0` | 是否已审核 |
| 15 | `review_status` | varchar(20) | YES | MUL | | 审核状态 |
| 16 | `creator` | varchar(64) | YES | | | 创建人 |
| 17 | `create_time` | datetime | YES | | CURRENT_TIMESTAMP | 创建时间 |
| 18 | `update_time` | datetime | YES | | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |
| 19 | `deleted` | bit(1) | YES | | b'0' | 软删除标志 |

- **主键**：`id`
- **索引**：`idx_code(violation_code)` · `idx_audititem(audititem_id)` · `idx_review(is_reviewed, review_status)`
- **外键**：无（`audititem_id` 为逻辑关联，未建物理外键）
- **软删除**：查询需带 `deleted = 0`

**示例行**（真实，长字段已截断）：
```
id=8756
violation_code=XV-20260620-0006-01
violation_title=将行政事业性收费违规转为经营服务性收费管理
audititem_id=l5pfr3
category_path=业务类-部门预算执行审计
severity=medium
expression_text=(收费项目明细台账.收费项目名称 IN (行政事业性收费目录清单.收费项目名称) AND 收费项目明细台账.收费性质='经营服务性收费') OR …
required_data={"items":[{"name":"收费项目明细台账","fields":["收费项目名称","收费标准","收费性质","收入金额",…]}]}
```

---

## 4. T2 `audit_violation_law_refs` — 违规-法规关联

**用途**：违规行为与其援引法规的多对多桥表。从 YAML 模板的 regulation JSON 拆解生成。
**字符集**：`utf8mb4_unicode_ci`（`law_id` 列单独为 `0900_ai_ci`） · **引擎**：InnoDB

| # | 字段 | 类型 | 可空 | 键 | 默认 | 说明 |
|---|------|------|------|-----|------|------|
| 1 | `id` | int | NO | PRI | AUTO_INCREMENT | 主键 |
| 2 | `violation_id` | int | NO | FK·UNI | | → `audit_violations.id` |
| 3 | `law_id` | varchar(32)〔0900_ai_ci〕 | NO | UNI·MUL | | → 外部 `audit_law.sys_core_law_allaudit.id` |
| 4 | `law_title` | varchar(500) | YES | | | 法规名称（冗余，便于不跨库显示） |
| 5 | `clause_ref` | varchar(500) | YES | | | 条款引用 |

- **主键**：`id`
- **唯一约束**：`uk_violation_law(violation_id, law_id)`（防重复关联）
- **索引**：`idx_law(law_id)`
- **外键**：`violation_id` → `audit_violations(id)` `ON DELETE CASCADE`

**示例行**：
```
id=9144  violation_id=8756  law_id=a00002272714
law_title=财政部关于加强政府非税收入管理的通知
clause_ref=第二条第（一）项
```

---

## 5. T3 `audit_cases` — 审计案例库

**用途**：历史审计案例，含案情、方法、发现、影响，通过桥表关联违规模型与法规。
**字符集**：`utf8mb4_unicode_ci` · **引擎**：InnoDB

| # | 字段 | 类型 | 可空 | 键 | 默认 | 说明 |
|---|------|------|------|-----|------|------|
| 1 | `id` | int | NO | PRI | AUTO_INCREMENT | 主键 |
| 2 | `title` | varchar(500) | NO | | | 案例标题 |
| 3 | `domain` | varchar(100) | YES | MUL | | 领域（前端分 Tab + 下拉框） |
| 4 | `case_summary` | text | YES | | | 案情摘要 |
| 5 | `audit_method` | text | YES | | | 审计方法（核查手段） |
| 6 | `involved_amount` | decimal(20,2) | YES | | | 涉案金额 |
| 7 | `audit_finding` | text | YES | | | 审计发现（违规表现） |
| 8 | `audit_impact` | text | YES | | | 风险影响 |
| 9 | `source` | varchar(500) | YES | | | 来源 |
| 10 | `created_at` | datetime | YES | MUL | CURRENT_TIMESTAMP | 创建时间（列表排序键） |

- **主键**：`id`
- **索引**：`idx_domain(domain)` · `idx_created_at(created_at)`
- **外键**：无
- **软删除**：无（删除为物理删除，需同步清理 T4/T5 关联）

**示例行**（真实，长字段已截断）：
```
id=7902
title=某市不动产登记中心将法定的不动产登记费与加急服务捆绑，以咨询服务名义开具税务发票，资金存入下属企业账户。
domain=业务类-部门预算执行审计
audit_method=比对登记中心收费项目与行政事业性收费目录；追踪银行流水发现未进财政专户；检查发票类型属税务监制而非财政票据。
involved_amount=NULL
audit_finding=利用行政职权将强制性收费包装成自愿服务收费，收入未缴入财政专户。
audit_impact=截留非税收入，形成账外资金，削弱财政统筹能力。
source=2026-6-20提取模板.xlsx
```

---

## 6. T4 `audit_case_violations` — 案例-违规关联

**用途**：案例与其违规模型的多对多桥表。
**字符集**：`utf8mb4_unicode_ci` · **引擎**：InnoDB

| # | 字段 | 类型 | 可空 | 键 | 默认 | 说明 |
|---|------|------|------|-----|------|------|
| 1 | `id` | int | NO | PRI | AUTO_INCREMENT | 主键 |
| 2 | `case_id` | int | NO | UNI | | → `audit_cases.id` |
| 3 | `violation_id` | int | NO | UNI·MUL | | → `audit_violations.id` |

- **主键**：`id`
- **唯一约束**：`uk_cv(case_id, violation_id)`
- **索引**：`idx_violation(violation_id)`
- **外键**：无（逻辑关联，由应用层维护一致性）

**示例行**：
```
id=7920  case_id=7902  violation_id=8756
```

---

## 7. T5 `audit_case_law_refs` — 案例-法规关联

**用途**：案例与其援引法规的多对多桥表。多数继承自案例关联违规的法规。
**字符集**：`utf8mb4_unicode_ci`（`law_id` 列单独为 `0900_ai_ci`） · **引擎**：InnoDB

| # | 字段 | 类型 | 可空 | 键 | 默认 | 说明 |
|---|------|------|------|-----|------|------|
| 1 | `id` | int | NO | PRI | AUTO_INCREMENT | 主键 |
| 2 | `case_id` | int | NO | MUL | | → `audit_cases.id` |
| 3 | `law_id` | varchar(32)〔0900_ai_ci〕 | NO | MUL | | → 外部 `audit_law.sys_core_law_allaudit.id` |

- **主键**：`id`
- **索引**：`idx_law(law_id)` · `idx_case(case_id)`
- **外键**：无
- **注意**：与 T2 不同，此表**未冗余** `law_title`，跨库 JOIN 失效时无法显示法规名

**示例行**：
```
id=9437  case_id=7902  law_id=a00002272714
```

---

## 8. 三向关联示例

以真实样本说明 5 张表如何串联（案例 7902 的完整关联）：

```
                         T2 audit_violation_law_refs
                         (id=9144)
   T1 audit_violations ────────────────────────────────→ 外部法规
   (id=8756)              violation_id=8756              law_id=a00002272714
       ▲                  law_id=a00002272714             《财政部关于加强政府
       │                  clause_ref=第二条第（一）项        非税收入管理的通知》
       │
T4 audit_case_violations
(id=7920) case_id=7902, violation_id=8756
       │
       ▼
   T3 audit_cases ─────────────────── T5 audit_case_law_refs ──→ 外部法规
   (id=7902)                            (id=9437)               law_id=a00002272714
   不动产登记中心捆绑收费案             case_id=7902
                                        law_id=a00002272714
```

**读取路径**：
- 案例 → 违规：`T3.id` → T4.`case_id` → T4.`violation_id` → T1
- 案例 → 法规：`T3.id` → T5.`case_id` → T5.`law_id` → 外部法规库
- 违规 → 法规：`T1.id` → T2.`violation_id` → T2.`law_id` → 外部法规库

---

## 9. 附录：`law_id` 引用的外部表

T2、T5 的 `law_id` 跨库指向外部只读库，**不属于知识工坊建表范围**，此处仅作引用说明：

| 外部表 | 库 | 用途 |
|--------|----|------|
| `sys_core_law_allaudit` | audit_law | 法规主数据（8,607 行，审计子集），`law_id` 的指向目标 |
| `sys_core_law` | audit_law | 法规全量库（353,069 行），详情查询的回退源 |
| `sys_audititem_SLFF` | audit_law | 审计事项树，T1.`audititem_id` 的指向目标 |

外部表结构详见 [KNOWLEDGE_WORKSHOP_SCHEMA_DESIGN.md](KNOWLEDGE_WORKSHOP_SCHEMA_DESIGN.md) §6。
