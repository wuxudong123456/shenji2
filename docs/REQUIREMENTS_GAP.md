# 审计实务工坊 — 需求缺口清单

> 对照 [REQUIREMENTS.md](REQUIREMENTS.md) 和 [DESIGN.md](DESIGN.md)，列出已设计但尚未实现的功能模块。
> 最后更新：2026-07-29

---

## 阅读指南

每一行代表一个功能模块。优先级定义：

| 优先级 | 含义 | 判断标准 |
|--------|------|----------|
| **P0** | 阻塞性 — 不做后续无法推进 | 前端 mock 数据无法替换为真实数据、核心流程不通 |
| **P1** | 核心 — 产品可用性的骨架 | 直接影响三大工坊和智能分析流程的完整度 |
| **P2** | 增强 — 让产品从能用变好用 | 知识图谱深度功能、前端体验优化 |
| **P3** | 锦上添花 — 非核心路径 | 辅助工具、管理功能 |

依赖列：`A → B` 表示 A 依赖 B 先完成。

---

## 一、Agent 多智能体系统（P0）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| AG-IMPL-01 | Agent 基类 `base.py` | DESIGN.md §3.1, REQUIREMENTS.md §6 | P0 | LLM 客户端 ✅ | 封装 LLM 调用、Prompt 模板渲染、工具调用循环、输出 Schema 验证 |
| AG-IMPL-02 | IntentAnalyzer Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-01 | P0 | AG-IMPL-01 | 自然语言 → 结构化审计意图。对应前端 analysis.js Step 1 |
| AG-IMPL-03 | ViolationMatcher Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-02 | P0 | AG-IMPL-01, DK-IMPL-01 | 意图 → 匹配违规模型列表。对应前端 analysis.js Step 2 |
| AG-IMPL-04 | DataAdvisor Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-02 | P1 | AG-IMPL-01, DW-IMPL-05 | 匹配的违规模型 → 推荐资料收集清单 |
| AG-IMPL-05 | RegulationAdvisor Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-02 | P0 | AG-IMPL-01, KG-IMPL-02 | 意图+审计对象层级 → 推荐法规+关系链展开。对应前端 analysis.js Step 3 |
| AG-IMPL-06 | AuditAnalyzer Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-05 | P0 | AG-IMPL-01, DK-IMPL-04 | 结构化数据 + 表达式 + 法规 → 逐行比对分析。对应前端 analysis.js Step 5-6 |
| AG-IMPL-07 | SuspicionGenerator Agent | DESIGN.md §3.1, REQUIREMENTS.md DA-06 | P1 | AG-IMPL-01, AG-IMPL-06 | 分析结果 + 法规依据 → 结构化疑点报告 |
| AG-IMPL-08 | Agent Registry 注册表 | DESIGN.md §3.2, REQUIREMENTS.md AG-01~05 | P1 | AG-IMPL-01 | Agent 定义的管理中心：注册/查询/更新/启用禁用 |
| AG-IMPL-09 | Agent 管理界面（前端） | DESIGN.md §4, REQUIREMENTS.md AG-06 | P2 | AG-IMPL-08 | settings.html 中可视化创建/编辑/启用/禁用 Agent |

## 二、LangGraph 工作流编排（P0）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| WF-IMPL-01 | 共享状态定义 `state.py` | DESIGN.md §3.1, REQUIREMENTS.md DA-01~06 | P0 | — | 定义 AnalysisTask 的完整状态结构：step, step_data, agent_results, confirmations |
| WF-IMPL-02 | 工作流图 `graph.py` | DESIGN.md §3.1, REQUIREMENTS.md DA-01~06 | P0 | WF-IMPL-01, AG-IMPL-02~07 | LangGraph 状态图：6 节点 + 人工确认断点(interrupt_before) + 并行节点(Step2) |
| WF-IMPL-03 | 工作流 API 端点 | DESIGN.md §3.2, REQUIREMENTS.md DA-01~06 | P0 | WF-IMPL-02 | POST /api/audit/analysis (创建), GET /api/audit/analysis/{id} (查询), POST /api/audit/analysis/{id}/step/{n} (执行步骤), POST /api/audit/analysis/{id}/confirm (人工确认) |

## 三、后端 API 路由对接（P0）

> **这是最关键的一步：让前端 api.js 能连上后端，替换所有 mock 数据。**

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| API-IMPL-01 | `/api/audit/projects` CRUD | DESIGN.md §3.2 | P0 | MySQL 表 `tt.audit_projects` | 项目管理：创建/列表/删除/详情 |
| API-IMPL-02 | `/api/audit/projects/{id}/upload` | DESIGN.md §3.2, REQUIREMENTS.md DW-02~05 | P0 | API-IMPL-01, OCR ✅, 模板引擎 ✅ | 上传 → OCR → 模板匹配 → 元数据提取 全链路 |
| API-IMPL-03 | `/api/audit/projects/{id}/files` | DESIGN.md §3.2, REQUIREMENTS.md DW-08 | P1 | API-IMPL-02 | 文件列表 + 解析状态 |
| API-IMPL-04 | `/api/audit/documents/{id}/trace` | DESIGN.md §3.2, REQUIREMENTS.md DW-06 NF-01 | P1 | API-IMPL-02 | 溯源锚点查询 |
| API-IMPL-05 | `/api/audit/data/{table}/rows` | DESIGN.md §3.2, REQUIREMENTS.md DD-01~02 | P1 | 数据工坊 6 张表 | 结构化数据浏览 + 筛选 |
| API-IMPL-06 | `/api/audit/data/query` | DESIGN.md §3.2, REQUIREMENTS.md DD-03~04 | P1 | API-IMPL-05, LLM ✅ | 智能问数：自然语言 → 伪SQL → 执行 |
| API-IMPL-07 | `/api/audit/knowledge/violations` | DESIGN.md §3.2, REQUIREMENTS.md DK-01 | P0 | DK-IMPL-01 | 违规行为检索（替换 knowledge.js 的 8 条 mock） |
| API-IMPL-08 | `/api/audit/knowledge/regulations` | DESIGN.md §3.2, REQUIREMENTS.md DK-02 | P0 | KG-IMPL-01 | 法规检索（替换 knowledge.js 的 mock） |
| API-IMPL-09 | `/api/audit/knowledge/regulation/{id}/graph` | DESIGN.md §3.2, REQUIREMENTS.md DK-03 | P0 | KG-IMPL-02 | 法规关系链（替换 knowledge.js 的 mock） |
| API-IMPL-10 | `/api/audit/knowledge/clauses/{law_id}` | DESIGN.md §3.2, REQUIREMENTS.md DK-04 | P2 | KG-IMPL-01 | 条款分析 |
| API-IMPL-11 | `/api/audit/expression/execute` | DESIGN.md §3.2, REQUIREMENTS.md DK-06 | P0 | DK-IMPL-04 | 执行违规表达式（替换 analysis.js 的 mock） |
| API-IMPL-12 | `/api/audit/suspicion/generate` | DESIGN.md §3.2, REQUIREMENTS.md DK-08 | P1 | AG-IMPL-07 | 生成疑点报告 |
| API-IMPL-13 | `/api/chat` | DESIGN.md §4 (lawqa.html) | P2 | LLM ✅ | RAG 法规问答（替换 lawqa.html 的 mock） |

## 四、违规表达式引擎（P0）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| DK-IMPL-01 | 违规行为库 MySQL 建表 + 数据导入 | DESIGN.md §2.3, REQUIREMENTS.md DK-01 | P0 | MySQL 连接 | 建 `tt.audit_violations`，从 YAML 模板的 violations[] 字段提取 2195 条违规模型导入 |
| DK-IMPL-02 | 违规行为 API CRUD | DESIGN.md §3.2, REQUIREMENTS.md DK-01 | P1 | DK-IMPL-01 | 违规行为的增删改查 + 审核流程(is_reviewed) |
| DK-IMPL-03 | 伪 SQL 解析器 | DESIGN.md §3.3, REQUIREMENTS.md DK-06 | P0 | — | 解析 `字段名 OP 值 AND/OR ...` 语法 → AST |
| DK-IMPL-04 | 表达式执行引擎 | DESIGN.md §3.3, REQUIREMENTS.md DK-06~07 | P0 | DK-IMPL-03, 数据工坊 6 张表 | AST → 对目标表逐行求值 → 返回命中/未命中结果 |
| DK-IMPL-05 | 表达式可视化（前端） | DESIGN.md §4, REQUIREMENTS.md DK-07 | P2 | DK-IMPL-04 | 逻辑树渲染 + 扫描动画 + 命中高亮 + 实时统计 |

## 五、知识工坊 — 知识图谱（P0~P1）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| KG-IMPL-01 | 法规全文检索 | DESIGN.md §2.1, REQUIREMENTS.md DK-02 | P0 | MySQL `audit_law` 库可访问 | 对接 `sys_core_law_allaudit` (12,016条)，按效力级别/时效性/区域类型筛选 |
| KG-IMPL-02 | 法规关系图查询服务 `regulation_graph.py` | DESIGN.md §2.1 §5, REQUIREMENTS.md DK-03 | P0 | KG-IMPL-01 | 封装 `tools_regulation_relation` 查询：给定 law_id → 返回 superior/related/history_version 完整关系树 |
| KG-IMPL-03 | 条款分析查询服务 | DESIGN.md §2.1, REQUIREMENTS.md DK-04 | P2 | KG-IMPL-01 | 封装 `tools_clause_relation` 查询：给定 law_id → 返回 7 类条款特征 |
| KG-IMPL-04 | 审计事项分类查询 | DESIGN.md §2.2, REQUIREMENTS.md DK-01 | P1 | MySQL `audit_law` 库 | 封装 `sys_audititem` 四表：分类树 + 定性/处罚依据关联 |
| KG-IMPL-05 | 案例库建表 + API | DESIGN.md §2.3 §4, REQUIREMENTS.md DK-05 | P2 | DK-IMPL-01, KG-IMPL-01 | 建 `tt.audit_cases` 及关联表，实现三向关联查询 |

## 六、资料工坊 — 数据表 + 溯源（P1）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| DW-IMPL-01 | 6 张结构化数据表建表 | DESIGN.md §2.3 | P0 | MySQL 连接 | `tt.data_contracts/finance/legal_docs/registers/credentials/general` |
| DW-IMPL-02 | 项目 Bucket 自动创建 | DESIGN.md §3.2, REQUIREMENTS.md DW-01 | P1 | MinIO ✅ | 创建项目 → 自动建立独立 MinIO bucket |
| DW-IMPL-03 | 上传→OCR→模板提取→入库 全链路 | DESIGN.md §3.2, REQUIREMENTS.md DW-02~05 | P0 | OCR ✅, 模板引擎 ✅, DW-IMPL-01 | 文件上传 → MinerU OCR → 模板匹配 → LLM 提取 → 写入对应 data_xxx 表 |
| DW-IMPL-04 | 溯源锚点建表 + 查询服务 | DESIGN.md §2.3 §3.4, REQUIREMENTS.md DW-06 NF-01 | P1 | DW-IMPL-03 | 建 `tt.audit_document_traces`，存储 OCR 版本/页码/坐标/提取字段 JSON |
| DW-IMPL-05 | 模板 YAML→MySQL 同步 | DESIGN.md §2.3, REQUIREMENTS.md DW-05 | P1 | DW-IMPL-01 | 将 1000+ YAML 模板导入 `tt.audit_templates` |
| DW-IMPL-06 | 重新推理 | DESIGN.md §3.2, REQUIREMENTS.md DW-07 | P2 | DW-IMPL-03 | 切换模板重新提取 |

## 七、MCP 集成（P1~P2）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| MCP-IMPL-01 | MCP Server: MySQL 审计库 | REQUIREMENTS.md AG-02 | P1 | MySQL 表就绪 | 封装为 MCP Server，暴露 search_laws / get_violations / query_data 等工具 |
| MCP-IMPL-02 | MCP Server: FAISS 语义搜索 | REQUIREMENTS.md AG-02 | P1 | FAISS 索引建成 | 封装为 MCP Server，暴露 semantic_search 工具 |
| MCP-IMPL-03 | MCP Server: MinIO 文件操作 | REQUIREMENTS.md AG-02 | P2 | MinIO ✅ | 封装为 MCP Server，暴露 upload/download/list 工具 |
| MCP-IMPL-04 | Agent→MCP 绑定配置 | DESIGN.md §1, REQUIREMENTS.md AG-02~04 | P1 | AG-IMPL-08, MCP-IMPL-01~03 | YAML 配置文件 + AgentRegistry 加载逻辑 |
| MCP-IMPL-05 | 运行时工具注入 | — | P1 | MCP-IMPL-04, WF-IMPL-02 | Workflow 调用 Agent 时自动加载其绑定的 MCP 工具 |

## 八、FAISS 向量检索引擎（P1）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| VEC-IMPL-01 | 法规向量索引 | REQUIREMENTS.md DK-02, DESIGN_PLAN.md | P1 | KG-IMPL-01 | 对 `sys_core_law_allaudit` 标题+内容做 embedding → FAISS 索引 |
| VEC-IMPL-02 | 违规模型向量索引 | REQUIREMENTS.md DK-01 | P1 | DK-IMPL-01 | 对违规模型名称+描述做 embedding → FAISS 索引 |
| VEC-IMPL-03 | 条款向量索引 | REQUIREMENTS.md DK-04 | P2 | KG-IMPL-01 | 对条款文本做 embedding → FAISS 索引 |
| VEC-IMPL-04 | Embedding 服务 | — | P1 | LLM 网关 ✅ | 调用 LLM 网关的 embedding 接口生成向量 |

## 九、前端功能补全（P2）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| FE-IMPL-01 | knowledge.js 对接真实 API | DESIGN.md §4 | P0 | API-IMPL-07~09 | 违规库/法规库/案例库从 mock → 真实 API |
| FE-IMPL-02 | analysis.js 对接 Agent API | DESIGN.md §4 | P0 | API-IMPL-11, WF-IMPL-03 | 7 步向导从 mock → 真实 Agent 工作流 |
| FE-IMPL-03 | 法规选择器组件 `regulation-selector.js` | DESIGN_PLAN.md | P1 | API-IMPL-09 | 法规关系树展开/条款浏览/勾选确认 |
| FE-IMPL-04 | 表达式引擎可视化组件 | DESIGN_PLAN.md | P2 | DK-IMPL-05 | 逻辑树 + 扫描动画 + 命中高亮 |
| FE-IMPL-05 | 溯源面板组件 `TraceAnchor` | DESIGN_PLAN.md | P2 | API-IMPL-04 | 📍溯源按钮 → 原始文档高亮定位 |
| FE-IMPL-06 | 文书生成页面 | DESIGN.md §4, REQUIREMENTS.md PG-05 | P2 | AG-IMPL-07 | 取证单/底稿/报告/复核意见书模板填充 + 导出 |
| FE-IMPL-07 | RAG 法规问答页面 | DESIGN.md §4, REQUIREMENTS.md PG-03 | P2 | API-IMPL-13 | 多轮对话 + 法规来源引用 |
| FE-IMPL-08 | 审计定性页面 | DESIGN.md §4, REQUIREMENTS.md PG-04 | P2 | KG-IMPL-02, DK-IMPL-01 | 问题描述 → 违规匹配 → 法规依据链 → 同类案例 → 导出意见书 |

## 十、数据库初始化（P0）

| ID | 功能 | 设计文档出处 | 优先级 | 依赖 | 说明 |
|----|------|-------------|--------|------|------|
| DB-IMPL-01 | MySQL 连接池配置 | DESIGN.md §3.1 | P0 | MySQL 实例可访问 | `backend/services/db.py` — pymysql 连接池 |
| DB-IMPL-02 | 新建表 DDL（13 张表） | DESIGN.md §2.3 | P0 | DB-IMPL-01 | 执行 `tt` 库建表 SQL |
| DB-IMPL-03 | 法规数据验证 | DESIGN.md §2.1 | P0 | MySQL `audit_law` 库可访问 | 确认 9 张复用表数据完整可查询 |
| DB-IMPL-04 | 模板 YAML → MySQL 导入脚本 | DESIGN.md §2.3 | P1 | DB-IMPL-02 | 将 1000+ YAML 的 name/description/output_fields/violations 导入 `tt.audit_templates` |
| DB-IMPL-05 | 违规表达式 → MySQL 导入脚本 | DESIGN.md §2.1 | P0 | DB-IMPL-02 | 从 YAML 模板 violations[] 提取 2195 条导入 `tt.audit_violations` |

---

## 实施依赖关系图

```
Phase 1: 基础设施 (必须先做，约 2-3 周)
──────────
DB-IMPL-01 (MySQL连接)
  ├── DB-IMPL-02 (13张表DDL)
  ├── DB-IMPL-03 (法规数据验证)
  ├── DB-IMPL-04 (模板导入)
  └── DB-IMPL-05 (违规行为导入)

Phase 2: 知识图谱骨架 (约 2 周)
──────────
KG-IMPL-01 (法规全文检索)
  ├── KG-IMPL-02 (法规关系图查询) ★ 核心
  └── KG-IMPL-04 (审计事项分类)

DK-IMPL-01 (违规行为库)
  └── DK-IMPL-02 (违规行为API)

Phase 3: Agent 系统 (约 3-4 周)
──────────
AG-IMPL-01 (Agent基类)
  ├── AG-IMPL-02 (IntentAnalyzer)
  ├── AG-IMPL-03 (ViolationMatcher) ← 需 DK-IMPL-01
  ├── AG-IMPL-05 (RegulationAdvisor) ← 需 KG-IMPL-02
  ├── AG-IMPL-04 (DataAdvisor)
  ├── AG-IMPL-06 (AuditAnalyzer)
  └── AG-IMPL-07 (SuspicionGenerator)

WF-IMPL-01~03 (LangGraph工作流) ← 需全部Agent就绪

Phase 4: API 对接 (约 2-3 周)
──────────
API-IMPL-01~13 (REST API)
  └── 前端替换 mock 数据 ← 需 API 全部就绪

Phase 5: MCP 集成 (约 2 周)
──────────
MCP-IMPL-01~03 (MCP Server)
  └── MCP-IMPL-04~05 (Agent→MCP绑定)

Phase 6: 前端补全 + 向量搜索 (约 3-4 周)
──────────
VEC-IMPL-01~04 (FAISS索引)
FE-IMPL-01~08 (前端真实数据对接)
DK-IMPL-03~04 (表达式引擎)
```

---

## 统计

| 类别 | P0 (阻塞) | P1 (核心) | P2 (增强) | 合计 |
|------|-----------|-----------|-----------|------|
| Agent 多智能体 | 5 | 3 | 1 | 9 |
| LangGraph 工作流 | 3 | 0 | 0 | 3 |
| API 路由对接 | 5 | 6 | 2 | 13 |
| 违规表达式引擎 | 2 | 1 | 2 | 5 |
| 知识图谱 | 3 | 2 | 1 | 6 |
| 资料工坊 | 2 | 3 | 1 | 6 |
| MCP 集成 | 0 | 4 | 1 | 5 |
| FAISS 向量 | 0 | 3 | 1 | 4 |
| 前端补全 | 2 | 1 | 5 | 8 |
| 数据库初始化 | 3 | 2 | 0 | 5 |
| **总计** | **25** | **25** | **14** | **64** |
