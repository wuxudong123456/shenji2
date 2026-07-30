# AGENTS.md — AI 编码助手入口指引

> **这个文件是给 AI 编码助手（Claude Code、Codex、Cursor、Copilot 等）看的。**
> 它告诉你：这是什么项目、做到哪了、现在要做什么、怎么做。
> **每个新的 AI 会话都应该先读这个文件。**

---

## 1. 项目身份

**审计实务工坊 (AuditWorkbench)** — 面向中国政府审计人员的 AI 辅助审计分析平台。

核心业务流程：审计人员输入审计意图（自然语言）→ AI 匹配违规模型 → 推荐法规依据 → 上传审计材料 → OCR 解析 → 模板匹配 → 提取结构化字段 → 执行违规表达式 → 生成疑点报告。

**技术栈**：Python/Flask 后端 + 纯 HTML/CSS/JS 前端 + MinIO 对象存储 + MySQL + LLM（OpenAI 兼容接口）

---

## 2. 当前状态（2026-07-29）

### ✅ 已实现

| 模块 | 文件 | 状态 |
|------|------|------|
| Flask API 服务 | `backend/app.py` (:5000) | ✅ 运行中 |
| MinIO 存储 | `backend/services/minio_client.py` | ✅ 已对接 :9100 |
| OCR 双引擎 | `backend/services/ocr_client.py` | ✅ MinerU(:5005) + LiteParse(:5006) |
| LLM 客户端 | `backend/services/llm_client.py` | ✅ OpenAI 兼容 → deepseek-v4-flash :8765 |
| YAML 模板引擎 | `backend/services/template_service.py` | ✅ 加载 1000+ 审计模板 |
| 文档分类+字段提取 | `backend/services/extraction_service.py` | ✅ LLM 分类 + 结构化提取 |
| MySQL 连接池 | `backend/services/db.py` | ✅ 多数据库切换（tt / audit_law） |
| 13 张业务表 DDL | `backend/data/schema.sql` | ✅ 17 张表（含 Phase 5-6 新增） |
| YAML 审计模板 | `backend/templates/profiles/audit/` | ✅ 1000+ 模板 |
| **6 个 AI Agent** | `backend/agents/` | ✅ BaseAgent + AgentRegistry + YAML 驱动 |
| **Agent 子类示范** | `backend/agents/audit_analyzer.py` | ✅ 动态数据查询 Prompt |
| **Agent 管理 API** | `backend/routes/agent_routes.py` | ✅ CRUD + DB 持久化 |
| **LangGraph 工作流** | `backend/workflow/graph.py` + `state.py` | ✅ 8 节点 + 并行 + 人工确认断点 |
| **13 个 API 路由** | `backend/routes/audit_routes.py` | ✅ /api/audit/* 全部就绪 |
| **知识图谱** | `backend/services/knowledge_service.py` + `regulation_graph.py` | ✅ 法规检索 + 关系图 + 审计事项树 |
| **违规表达式引擎** | `backend/services/expression_parser.py` + `expression_engine.py` | ✅ 伪SQL→AST→逐行扫描 |
| **FAISS 语义搜索** | `backend/services/vector_store.py` | ✅ 法规+违规双索引 |
| **4 个 MCP Server** | `backend/mcp_servers/` | ✅ knowledge / vector / minio / expression |
| **后台任务系统** | `backend/services/task_manager.py` + `task_worker.py` | ✅ 异步队列 + 重试 |
| **案例库** | `backend/data/migrate_cases.sql` + API | ✅ 三向关联 |
| **文书生成** | `backend/services/document_service.py` | ✅ 取证单/底稿/报告/复核意见书 |
| **前端全部页面 UI** | `frontend/*.html` + `frontend/js/*.js` | ✅ 14 页 + 导航框架 |
| **前端 API 对接** | `frontend/js/api.js` + `analysis.js` | ✅ mock 已替换为真实 API 调用 |
| Electron 桌面壳 | `desktop/main.js` | ✅ 加载网关 :18791 |
| 配置 | `.env` | ✅ MinIO/MySQL/OCR/LLM |

### ⚠️ 进行中 / 待完成

| 项目 | 说明 |
|------|------|
| OpenSquilla 架构迁移 | 当前为独立 Flask 应用，DESIGN.md 规划为 OpenSquilla 插件，迁移方案已制定（Batch 5） |
| MCP 运行时工具注入 | Agent YAML 声明的工具尚未在 LLM 调用时自动注入（需 OpenSquilla MCP Manager 或自行实现 tool-calling 循环） |
| 前端 analysis-wiz.js | 部分 mock 数据已替换，违反/法规数据库待对接真实 API |

### ⚠️ Dead Code

以下 3 个文件引用了不存在的 `shared.*` 包（原 OntoSKU 遗留）：
- `backend/templates/classifier.py` — **不要修改**，实际分类逻辑在 `services/extraction_service.py`
- `backend/templates/profile_loader.py` — **不要修改**，实际模板加载在 `services/template_service.py`
- `backend/templates/prompt_builder.py` — **不要修改**

---

## 3. 文档阅读顺序

**每个新 AI 会话应严格按此顺序阅读：**

```
第1步: AGENTS.md（本文件）            ← 你现在在读
第2步: docs/REQUIREMENTS_GAP.md       ← 64 项缺口，含优先级和依赖关系
第3步: docs/IMPLEMENTATION_PLAN.md    ← 6 Phase 执行计划，具体任务清单
第4步: docs/DESIGN.md                 ← 系统设计（数据库/SQL/API/Agent/部署）
第5步: docs/REQUIREMENTS.md           ← 需求规格（功能需求 ID 对应 DESIGN.md）
第6步: docs/DESIGN_PLAN.md            ← 产品设计（视觉/交互/确认模型/溯源）
第7步: backend/app.py                 ← 现有 Flask 路由（了解已有 API）
第8步: frontend/js/api.js             ← 前端调用的 API 端点列表（目标状态）
```

**不要跳过**：即使你认为已经理解了项目，也至少读完第 1-3 步。第 1-3 步包含了多轮深度讨论得出的关键架构决策。

---

## 4. 当前开发任务

**大部分 Phase 1-4 已完成，Phase 5-6 部分完成。当前焦点：**

### 当前优先事项

1. **OpenSquilla 架构迁移**（Batch 5）：将独立 Flask 应用迁移为 OpenSquilla 插件
2. **MCP 工具注入**：在 Agent 运行时注入 MCP 工具实现真实 function-calling
3. **前端 analysis-wiz.js 对接**：替换剩余的违反/法规 mock 数据
4. **端到端测试**：全流程验证（意图→违规匹配→法规推荐→确认→上传→分析→疑点报告）

### 已完成的 Phase

- ✅ Phase 1（基础设施）：MySQL 连接池、13+ 张表 DDL、数据导入
- ✅ Phase 2（知识图谱）：法规检索、关系图、违规查询、审计事项树
- ✅ Phase 3（Agent 系统）：BaseAgent、AgentRegistry、6 Agent YAML、Agent 管理 API
- ✅ Phase 4（工作流 + API）：LangGraph 图、表达式引擎、13 个 API 端点
- ⚠️ Phase 5（前端对接）：核心 analysis.js 已完成，analysis-wiz.js 部分完成
- ⚠️ Phase 6（增强功能）：FAISS、案例库、文书生成已完成；MCP 注入待完成

### 不要做的

- ❌ 不要修改 `backend/templates/classifier.py`、`profile_loader.py`、`prompt_builder.py`（死代码）
- ❌ 不要重写前端框架（`frontend/js/app.js` AuditWorkbench 框架保持不变）
- ❌ 不要改变现有 Flask 路由（`/api/files/*`、`/api/templates/*` 保持兼容）
- ❌ 不要删除 mock 数据（等 API 实现后再替换，mock 是前端开发的参考）

---

## 5. 架构关键决策（不要重新讨论）

以下决策已经过多轮分析确认，**直接执行，不要重新设计：**

### 5.1 三层代码关系

```
设计文档(docs/)    → 描述完整架构（OpenSquilla 网关 + 6 Agent）
前端(frontend/)    → 调用 /api/audit/* 端点（目标状态，当前用 mock 降级）
后端(backend/)     → 当前暴露 /api/files/* + /api/templates/*（独立 Flask）
```

**前后端 API 不对接是已知的、故意的——Phase 4 会通过 `routes/audit_routes.py` 统一。**

### 5.2 Agent 边界三原则

| 原则 | 含义 |
|------|------|
| **输入来源** | 来自上游 Agent → 串行；独立 → 可并行 |
| **输出消费者** | 给下游 Agent → 流水线节点；给用户看 → 终点节点 |
| **MCP 工具集** | 不重叠 = 职责清晰；高度重叠 = 考虑合并 |

6 个 Agent 的定义见 `IMPLEMENTATION_PLAN.md` Phase 3 和 `DESIGN.md` §3.1。

### 5.3 MCP 集成模式

```
OpenSquilla 网关
├── MCP Manager（已有，网关内置）
├── 审计扩展层 → Agent→MCP 绑定（待实现）
│   └── 配置在 agents/agents.yaml，运行时注入
└── 权限隔离：每个 Agent 只能看到自己绑定的工具
```

### 5.4 知识图谱三层架构

```
法规层: 12,016部法规 → 31,317条关系链 → 119,210条条款(7类特征)
审计层: 审计事项分类树 → 2,195违规模型 → 引用法规条款
案例层: 案例 → 违规行为 → 法规 → 同类案例关联
```

存储：MySQL 关系表（精确查询 + 关系遍历）+ FAISS 向量索引（语义搜索，Phase 6）。

### 5.5 数据传递策略：引用而非全量

Agent 之间传递的是 **ID 引用**（`violation_id`、`law_id`），不是全文。下游 Agent 通过 MCP 实时查询完整内容。避免 LangGraph SharedState 上下文爆炸。

---

## 6. 文件结构约定

### 新建文件清单

```
backend/
├── services/
│   ├── db.py                      ← P1 任务 1.1: MySQL 连接池
│   ├── knowledge_service.py       ← P2 任务 2.1+2.3: 法规检索+违规查询
│   ├── regulation_graph.py        ← P2 任务 2.2: 法规关系图查询 ★核心
│   ├── expression_parser.py       ← P4 任务 4.1: 伪SQL→AST解析器
│   └── expression_engine.py       ← P4 任务 4.2: 表达式执行引擎
├── agents/
│   ├── __init__.py
│   ├── agents.yaml                ← P3 任务 3.2: Agent 定义+MCP绑定
│   ├── base.py                    ← P3 任务 3.1: BaseAgent 基类
│   ├── registry.py                ← P3 任务 3.2: AgentRegistry
│   ├── intent_analyzer.py
│   ├── violation_matcher.py
│   ├── data_advisor.py
│   ├── regulation_advisor.py
│   ├── audit_analyzer.py
│   └── suspicion_generator.py
├── workflow/
│   ├── __init__.py
│   ├── state.py                   ← P4 任务 4.3: AnalysisState 定义
│   └── graph.py                   ← P4 任务 4.3: LangGraph 状态图
├── routes/
│   ├── __init__.py
│   └── audit_routes.py            ← P4 任务 4.4: /api/audit/* 路由
├── data/
│   ├── schema.sql                 ← P1 任务 1.2: 13张表DDL
│   ├── import_templates.py        ← P1 任务 1.3: 数据导入脚本
│   └── verify_data_sources.py     ← P1 任务 1.4: 验证脚本
└── prompts/                       ← 可延后（先用 agents.yaml 内嵌 prompt）
    ├── intent_analyzer.txt
    └── ...

docs/
└── dev-specs/                     ← P0 先行: 开发规格文档
    ├── 02-api-routes.md
    ├── 01-agent-base.md
    └── 03-knowledge-graph-api.md
```

### 现有文件不要修改（除非明确说明）

| 文件 | 规则 |
|------|------|
| `backend/app.py` | 只在 Phase 4 新增一行 `register_audit_routes(app)` |
| `backend/config.py` | 如需新增配置项，追加到文件末尾 |
| `frontend/js/app.js` | 不修改导航框架 |
| `frontend/js/api.js` | Phase 5 对齐端点路径 |
| `frontend/js/analysis.js` | Phase 5 替换 mock 为真实 API |
| `frontend/js/knowledge.js` | Phase 5 替换 mock 为真实 API |
| `backend/templates/classifier.py` | **永不修改**（死代码） |
| `backend/templates/profile_loader.py` | **永不修改**（死代码） |

---

## 7. 开发原则

1. **每次只做一个 Phase**。完成一个 Phase 的所有任务并验证通过后，再进入下一个。
2. **每个任务独立验证**。写一个任务 → 测试 → 确认通过 → 提交 → 下一个。
3. **先读文档再写代码**。不要跳过文档阅读步骤直接写代码。
4. **不要重复造轮子**。`services/llm_client.py`、`services/ocr_client.py`、`services/minio_client.py`、`services/template_service.py` 都已是可用的——直接 import，不要重写。
5. **中文注释和变量名**。业务逻辑代码使用中文注释。API 字段名与前端保持一致（中文键名）。
6. **复刻现有代码风格**。新文件匹配已有文件的命名规范、注释密度、导入风格。
