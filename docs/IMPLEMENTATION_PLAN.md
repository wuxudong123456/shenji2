# 审计实务工坊 — 实施方案

> 基于 [REQUIREMENTS_GAP.md](REQUIREMENTS_GAP.md) 的 64 项缺口和 6 次深度讨论结论，制定可执行的开发计划。
> 最后更新：2026-07-29

---

## 目录

1. [实施总览](#一实施总览)
2. [Phase 1: 基础设施（第 1-2 周）](#二phase-1-基础设施第-1-2-周)
3. [Phase 2: 知识图谱骨架（第 2-3 周）](#三phase-2-知识图谱骨架第-2-3-周)
4. [Phase 3: Agent 系统（第 3-5 周）](#四phase-3-agent-系统第-3-5-周)
5. [Phase 4: 工作流 + API 对接（第 5-7 周）](#五phase-4-工作流--api-对接第-5-7-周)
6. [Phase 5: 前端真实数据对接（第 7-8 周）](#六phase-5-前端真实数据对接第-7-8-周)
7. [Phase 6: 增强功能（第 8 周+）](#七phase-6-增强功能第-8-周后)
8. [依赖关系图](#八依赖关系图)
9. [里程碑检查清单](#九里程碑检查清单)

---

## 一、实施总览

### 1.1 时间线

```
第1周     第2周     第3周     第4周     第5周     第6周     第7周     第8周+
████████████████████████████████████████████████████████████████████████████
│← Phase1 →│← Phase2 →│←─────── Phase3 ──────→│← Phase4 →│←Phase5→│←P6→│
│ 基础设施  │ 知识图谱  │     Agent 系统        │ 工作流+API│ 前端对接│增强 │
│           │ 骨架      │                       │           │        │    │
└───────────┴──────────┴───────────────────────┴──────────┴────────┴────┘

全部 6 个 Phase，预计 8 周完成核心功能，增强功能持续迭代。
```

### 1.2 交付产物总览

| Phase | 新增文件 | 核心产物 |
|-------|---------|---------|
| Phase 1 | 3 个 | MySQL 连接池、13 张表 DDL、数据导入脚本 |
| Phase 2 | 2 个 | 法规检索服务、法规关系图查询服务 |
| Phase 3 | 10 个 | Agent 基类、AgentRegistry、6 个 Agent、agents.yaml |
| Phase 4 | 5 个 | LangGraph 工作流、13 个 API 路由、表达式解析器、表达式引擎 |
| Phase 5 | 6 个 | 6 个前端 JS 模块改造（替换 mock → 真实 API） |
| Phase 6 | 6 个 | FAISS 索引、MCP Server、文书生成、案例库、溯源面板、法规问答 |

### 1.3 开发前的准备

在写第一行代码之前，先写 3 份开发规格文档（存放在 `docs/dev-specs/`）：

| 序号 | 文档 | 产出 | 前置 | 需时 |
|------|------|------|------|------|
| Spec-A | `02-api-routes.md` | 13 个 API 端点的完整 JSON Schema | 无 | 2 天 |
| Spec-B | `01-agent-base.md` | 6 个 Agent 的输入/输出 Schema | Spec-A | 2 天 |
| Spec-C | `03-knowledge-graph-api.md` | 知识图谱接口 + SQL + 返回结构 | Spec-A | 1 天 |

---

## 二、Phase 1: 基础设施（第 1-2 周）

**目标**：搭建数据底座，让后续所有模块有地方存数据、有地方查数据。

### 2.1 任务清单

#### 任务 1.1：MySQL 连接池

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/db.py`（新建） |
| **依赖** | `pymysql` ✅已在 requirements.txt |
| **工作量** | ~80 行 Python |
| **产出** | `get_connection()` 返回连接池连接；`query(sql, params)` 快捷查询 |

```python
# backend/services/db.py 核心结构
from pymysql import connect
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
from config import Config

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
            user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DATABASE,
            charset='utf8mb4', cursorclass=DictCursor,
            mincached=2, maxcached=10, maxconnections=20,
        )
    return _pool

def query(sql, params=None):
    """执行 SELECT 并返回 list[dict]"""
    conn = get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()

def execute(sql, params=None):
    """执行 INSERT/UPDATE/DELETE"""
    conn = get_pool().connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()
```

#### 任务 1.2：执行 13 张表 DDL

| 项 | 内容 |
|----|------|
| **文件** | `backend/data/schema.sql`（新建） |
| **来源** | [DESIGN.md](DESIGN.md) §2.3 的 13 张 CREATE TABLE 语句 |
| **工作量** | 整理已有 SQL + 补充缺失表 |
| **产出** | 一键执行的建表脚本 |

**13 张表清单**：
```
tt.audit_projects          — 审计项目
tt.audit_templates         — 审计模板（YAML → MySQL）
tt.audit_violations        — 违规行为库
tt.audit_violation_law_refs  — 违规↔法规关联
tt.audit_violation_templates — 违规↔模板关联
tt.audit_document_traces   — 溯源锚点
tt.audit_conversations     — AI 对话记录
tt.audit_analysis_tasks    — 智能分析任务
tt.audit_cases             — 审计案例
tt.audit_case_violations   — 案例↔违规关联
tt.audit_case_law_refs     — 案例↔法规关联
tt.audit_case_relations    — 案例间相似关系
tt.project_suspicions      — 疑点记录
```

#### 任务 1.3：YAML → MySQL 数据导入

| 项 | 内容 |
|----|------|
| **文件** | `backend/data/import_templates.py`（新建） |
| **依赖** | 任务 1.1 + 1.2 |
| **工作量** | ~150 行 Python |
| **产出** | 一键将 1000+ YAML 模板导入 `tt.audit_templates` + `tt.audit_violations` + 关联表 |

**导入逻辑**：
```python
# 伪代码
for yaml_file in TEMPLATES_DIR.rglob("*.yaml"):
    data = yaml.safe_load(yaml_file)
    # 1. 插入模板元数据 → tt.audit_templates
    template_id = insert_template(data)
    
    # 2. 提取 violations[] → 插入 tt.audit_violations
    for v in data.get("violations", []):
        violation_id = insert_violation(v, template_id)
        # 3. 提取 regulation JSON → 插入关联表
        for law in parse_regulation_json(v["regulation"]):
            insert_violation_law_ref(violation_id, law)
```

#### 任务 1.4：验证外部数据源

| 项 | 内容 |
|----|------|
| **文件** | `backend/data/verify_data_sources.py`（新建） |
| **工作量** | ~50 行 Python |
| **产出** | 验证脚本，确认以下数据可访问 |

**验证清单**：
```
□ audit_law.sys_core_law_allaudit     — 行数 ≥ 12,016
□ audit_law.tools_regulation_relation — 行数 ≥ 31,317
□ audit_law.tools_clause_relation     — 行数 ≥ 119,210
□ audit_law.sys_audititem             — 树形结构完整
□ MinIO :9100                         — bucket 可创建/读写
□ LLM :8765/v1/models                 — 健康检查通过
□ OCR :5005/health                    — 健康检查通过
```

### 2.2 Phase 1 完成标准

```
□ MySQL 连接池正常工作
□ 13 张表全部建好，在 MySQL 中可查
□ 1000+ YAML 模板数据导入完成，tt.audit_templates + tt.audit_violations 有数据
□ audit_law 库的法规数据验证通过
□ MinIO / LLM / OCR 健康检查全部通过
```

---

## 三、Phase 2: 知识图谱骨架（第 2-3 周）

**目标**：建立法规查询服务，让后续 Agent 能查到法、能展开法规关系链。

### 3.1 任务清单

#### 任务 2.1：法规全文检索服务

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/knowledge_service.py`（新建，下半部分） |
| **依赖** | Phase 1 的 MySQL 连接池 |
| **工作量** | ~120 行 Python |
| **核心函数** | `search_laws(query, potency_level, timeliness, limit)` |

```python
def search_laws(
    query: str,
    potency_level: str = None,   # "法律" / "行政法规" / "部门规章" / "地方性法规"
    timeliness: str = None,      # "现行有效" / "已废止"
    limit: int = 50,
) -> list[dict]:
    """法规全文检索。
    
    SQL: SELECT id, name, doc_no, potency_level, timeliness, publish_date
         FROM audit_law.sys_core_law_allaudit
         WHERE (name LIKE '%{query}%' OR pro_content LIKE '%{query}%')
         [AND potency_level = ?] [AND timeliness = ?]
         ORDER BY CASE WHEN name LIKE '%{query}%' THEN 0 ELSE 1 END
         LIMIT ?
    """
```

#### 任务 2.2：法规关系图查询服务 ★ 核心

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/regulation_graph.py`（新建） |
| **依赖** | 任务 2.1 |
| **工作量** | ~200 行 Python |
| **核心函数** | `get_regulation_graph(law_id)` |

```python
def get_regulation_graph(law_id: str) -> dict:
    """获取法规完整关系图。
    
    查询步骤:
    1. 查主法信息
    2. 查上位法（递归最多 3 层）
    3. 查下位法（以本法规为上位法的法规）
    4. 查相关法（双向查询 tools_regulation_relation）
    5. 查历史版本
    6. 组装为统一响应结构
    
    响应结构:
    {
      "center": { "id", "name", "doc_no", "potency_level", "timeliness", "publish_date" },
      "superior_chain": [{ "id", "name", "potency_level", "relation": "superior" }],
      "inferior": [{ "id", "name", "potency_level", "relation": "inferior" }],
      "related": [{ "id", "name", "relation": "related" }],
      "history_versions": [{ "id", "name", "timeliness": "已废止", "relation": "history_version" }],
      "total_relations": 15
    }
    """
```

#### 任务 2.3：违规行为查询服务

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/knowledge_service.py`（上半部分） |
| **依赖** | Phase 1 的数据导入 |
| **工作量** | ~100 行 Python |
| **核心函数** | `search_violations(query)` + `get_violation_detail(violation_id)` |

### 3.2 Phase 2 完成标准

```
□ search_laws("招标") 返回匹配法规列表
□ get_law_fulltext(law_id) 返回法规全文
□ get_regulation_graph(law_id) 返回完整关系树（上位法/下位法/相关法/历史版本）
□ search_violations("化整为零") 返回匹配违规模型
□ get_violation_detail(violation_id) 返回违规详情 + 表达式 + 引用法规
```

---

## 四、Phase 3: Agent 系统（第 3-5 周）

**目标**：实现 6 个 AI Agent，每个完成审计流水线上的一个专业任务。

### 4.1 任务清单

#### 任务 3.1：Agent 基类

| 项 | 内容 |
|----|------|
| **文件** | `backend/agents/__init__.py` + `backend/agents/base.py`（新建） |
| **依赖** | Phase 2（知识图谱服务作为 MCP 数据源） |
| **工作量** | ~150 行 Python |

```python
# backend/agents/base.py
from dataclasses import dataclass
from services.llm_client import call_llm, call_llm_json

@dataclass
class McpToolBinding:
    server: str        # MCP Server 名称
    tools: list[str]   # 允许的工具列表

@dataclass 
class AgentDefinition:
    agent_id: str
    name: str
    system_prompt: str
    model: str
    temperature: float
    max_tokens: int
    mcp_bindings: list[McpToolBinding]
    input_mapping: list[str]   # 从 AnalysisState 中提取哪些字段
    output_schema: dict        # 输出 JSON Schema

class BaseAgent:
    def __init__(self, definition: AgentDefinition):
        self.def = definition
    
    def run(self, state: dict) -> dict:
        """核心方法：输入 AnalysisState → 输出结构化结果"""
        # 1. 从 state 提取本 Agent 需要的字段
        # 2. 渲染 System Prompt
        # 3. 准备 MCP 工具（如果有绑定）
        # 4. 调用 LLM（call_llm_json）
        # 5. 验证输出符合 output_schema
        # 6. 返回结果
```

#### 任务 3.2：Agent Registry + 配置文件

| 项 | 内容 |
|----|------|
| **文件** | `backend/agents/registry.py` + `backend/agents/agents.yaml`（新建） |
| **依赖** | 任务 3.1 |
| **工作量** | `registry.py` ~80 行 + `agents.yaml` ~150 行 |

```yaml
# backend/agents/agents.yaml 结构示例
agents:
  intent_analyzer:
    name: "意图分析专家"
    model: "deepseek-v4-flash"
    temperature: 0.1
    max_tokens: 2048
    mcp_tools: []
    system_prompt: |
      你是一名专业的国家审计人员...
    output_schema:
      type: object
      properties:
        domain: { type: string, description: "审计领域" }
        item: { type: string, description: "审计事项" }
        # ...

  violation_matcher:
    name: "违规匹配专家"
    model: "deepseek-v4-flash"
    mcp_tools:
      - server: "knowledge-service"
        tools: ["search_violations", "get_violation_detail"]
    # ...
```

#### 任务 3.3：6 个 Agent 实现

| Agent | 文件 | 核心逻辑 | 工作量 | 依赖 |
|-------|------|---------|--------|------|
| **IntentAnalyzer** | `agents/intent_analyzer.py` | 解析自然语言 → 结构化审计意图 | ~60 行 | base.py |
| **ViolationMatcher** | `agents/violation_matcher.py` | 意图 → 调用 MCP搜索违规库 → 排序 → 返回匹配列表 | ~80 行 | base.py + knowledge_service |
| **DataAdvisor** | `agents/data_advisor.py` | 违规模型 → 调用 MCP搜索模板 → 推荐资料清单 | ~60 行 | base.py + knowledge_service |
| **RegulationAdvisor** | `agents/regulation_advisor.py` | 意图 + 对象层级 → 搜索法规 → 展开关系链 → 层级建议 | ~100 行 | base.py + regulation_graph |
| **AuditAnalyzer** | `agents/audit_analyzer.py` | 结构化数据 + 表达式 → 逐行比对 → 命中分析 | ~120 行 | base.py + expression_engine |
| **SuspicionGenerator** | `agents/suspicion_generator.py` | 分析结果 + 法规 → 结构化疑点报告 | ~80 行 | base.py |

**Agent 2+3+4 可并行开发，Agent 5 依赖 Agent 2+4，Agent 6 依赖 Agent 5。**

### 4.2 Phase 3 完成标准

```
□ BaseAgent.run(state) 能正确调用 LLM 并返回结构化结果
□ AgentRegistry 能从 agents.yaml 加载全部 6 个 Agent 定义
□ 每个 Agent 独立测试通过：给定输入 → 返回符合 Schema 的输出
□ Agent 2 能搜索违规库 → 返回匹配的违规模型
□ Agent 4 能搜索法规 → 展开关系链 → 返回法规树
```

---

## 五、Phase 4: 工作流 + API 对接（第 5-7 周）

**目标**：用 LangGraph 串起 6 个 Agent，通过 API 暴露给前端。

### 5.1 任务清单

#### 任务 4.1：违规表达式解析器

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/expression_parser.py`（新建） |
| **依赖** | 无（纯算法） |
| **工作量** | ~200 行 Python |

```python
# 核心能力
def parse_expression(expr: str) -> ASTNode:
    """解析伪 SQL → AST
    
    输入: '采购方式="询价" AND 金额>1000000 AND 签订日期 BETWEEN "2026-03-01" AND "2026-03-31"'
    输出: {
      type: "AND",
      left: { type: "AND", left: { type: "EQ", field: "采购方式", value: "询价" },
                            right: { type: "GT", field: "金额", value: 1000000 } },
      right: { type: "BETWEEN", field: "签订日期", start: "2026-03-01", end: "2026-03-31" }
    }
    """

def evaluate_ast(ast: ASTNode, row: dict) -> bool:
    """对一行数据执行 AST，返回是否命中"""
```

#### 任务 4.2：表达式执行引擎

| 项 | 内容 |
|----|------|
| **文件** | `backend/services/expression_engine.py`（新建） |
| **依赖** | 任务 4.1 + 数据工坊表 |
| **工作量** | ~150 行 Python |

```python
def execute_expression(expression: str, table: str, project_id: str) -> dict:
    """对指定表的所有行执行违规表达式
    
    返回: {
      "total": 4810,
      "hits": 620,
      "hit_rate": 0.129,
      "rows": [{ "row_id": 5, "matched": true, "fields": {...} }, ...]
    }
    """
```

#### 任务 4.3：LangGraph 工作流

| 项 | 内容 |
|----|------|
| **文件** | `backend/workflow/__init__.py` + `backend/workflow/state.py` + `backend/workflow/graph.py`（新建） |
| **依赖** | Phase 3（6 个 Agent 就绪） |
| **工作量** | state.py ~50 行 + graph.py ~150 行 |
| **新增 pip 包** | `langgraph`、`langgraph-checkpoint` |

```python
# backend/workflow/graph.py 核心结构
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

def build_analysis_graph():
    workflow = StateGraph(AnalysisState)
    
    # 注册 6 个 Agent 节点 + 2 个人工确认节点
    workflow.add_node("step_1_intent", intent_analyzer.run)
    workflow.add_node("step_2_violations", violation_matcher.run)
    workflow.add_node("step_2_data_advice", data_advisor.run)
    workflow.add_node("step_2_regulations", regulation_advisor.run)
    workflow.add_node("step_3_confirm", human_confirm_node)
    workflow.add_node("step_4_ocr", document_processing_node)
    workflow.add_node("step_5_analysis", audit_analyzer.run)
    workflow.add_node("step_6_suspicions", suspicion_generator.run)
    
    # 定义边（流程）
    workflow.set_entry_point("step_1_intent")
    workflow.add_edge("step_1_intent", "step_2_violations")
    workflow.add_edge("step_2_violations", "step_2_regulations")
    workflow.add_edge("step_2_regulations", "step_3_confirm")    # ★ 人工确认断点
    workflow.add_edge("step_3_confirm", "step_4_ocr")
    workflow.add_edge("step_4_ocr", "step_5_analysis")
    workflow.add_edge("step_5_analysis", "step_6_suspicions")
    workflow.add_edge("step_6_suspicions", END)
    
    return workflow.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["step_3_confirm", "step_6_suspicions"]
    )
```

#### 任务 4.4：API 路由

| 项 | 内容 |
|----|------|
| **文件** | `backend/routes/__init__.py` + `backend/routes/audit_routes.py`（新建） |
| **依赖** | 任务 4.3 + Phase 2 |
| **工作量** | ~400 行 Python |

```python
# backend/routes/audit_routes.py — 注册到 Flask app
def register_audit_routes(app):
    """在 app.py 中调用此函数注册所有 /api/audit/* 路由"""
    
    # 项目管理
    app.add_url_rule('/api/audit/projects', 'list_projects', list_projects, methods=['GET'])
    app.add_url_rule('/api/audit/projects', 'create_project', create_project, methods=['POST'])
    # ... 共 13 个端点
```

**需要在 `app.py` 中新增一行**：
```python
# app.py 末尾新增
from routes.audit_routes import register_audit_routes
register_audit_routes(app)
```

### 5.2 Phase 4 完成标准

```
□ 表达式解析器能正确解析 YAML 模板中的所有伪 SQL 语法
□ 表达式引擎能对数据表执行逐行扫描，返回命中/未命中
□ LangGraph 工作流能从 Step 1 走到 Step 6
□ 人工确认断点正常工作（暂停等待 → 收到确认 → 继续）
□ curl POST /api/audit/analysis 返回 task_id
□ curl GET /api/audit/knowledge/violations?q=招标 返回匹配列表
□ curl GET /api/audit/knowledge/regulation/{id}/graph 返回关系树
□ curl POST /api/audit/expression/execute 返回扫描结果
```

---

## 六、Phase 5: 前端真实数据对接（第 7-8 周）

**目标**：前端所有 mock 数据替换为真实 API 调用。

### 6.1 任务清单

| 文件 | 当前状态 | 改造内容 | 工作量 |
|------|---------|---------|--------|
| `js/api.js` | 已有 `AuditAPI` 对象 | 确认所有端点路径与后端一致 | ~30 行修改 |
| `js/knowledge.js` | 8 条硬编码 violations | `this.violations = []` → `AuditAPI.knowledge.violations()` | ~50 行修改 |
| `js/analysis.js` | parseIntent() 返回 mock 数据 | 调用 `POST /api/audit/analysis` 创建任务 | ~80 行修改 |
| `js/analysis-wiz.js` | 硬编码违规/法规 DB | 调用 `AuditAPI.knowledge.*` 和 `AuditAPI.chat.*` | ~100 行修改 |
| `js/knowledge.js` | 法规 tab mock 数据 | 调用 `AuditAPI.knowledge.regulations()` | ~50 行修改 |
| `js/portal.js` | 统计数字 mock | 调用项目/任务 API 获取真实数据 | ~40 行修改 |

### 6.2 Phase 5 完成标准

```
□ 知识工坊页面 — 违规库/法规库从 API 加载真实数据，不再使用 mock
□ 智能分析页面 — 7 步向导每步调用真实 API，AI 解析结果来自 Agent
□ 门户首页 — 项目统计/文档状态从 API 获取真实数据
□ 法规选择器组件正常工作（法规搜索 → 关系树展开 → 条款浏览 → 确认）
□ 表达式引擎可视化正常（执行表达式 → 扫描动画 → 命中高亮 → 结果列表）
```

---

## 七、Phase 6: 增强功能（第 8 周后）

**目标**：从"能用"到"好用"。

### 7.1 任务清单

| 序号 | 功能 | 文件 | 工作量 | 优先级 |
|------|------|------|--------|--------|
| 6.1 | FAISS 向量索引（法规 + 违规语义搜索） | `services/vector_store.py` | ~200 行 | P2 |
| 6.2 | MCP Server 封装（MySQL/MinIO/FAISS） | `mcp_servers/` | ~300 行 | P2 |
| 6.3 | Agent→MCP 运行时绑定 | `agents/registry.py` 扩展 | ~100 行 | P2 |
| 6.4 | 案例库 + 三向关联 | 数据导入 + API | ~300 行 | P2 |
| 6.5 | 文书生成（取证单/底稿/报告） | 模板填充 + 导出 | ~400 行 | P2 |
| 6.6 | 溯源面板前端组件 | `js/trace-anchor.js` | ~200 行 | P2 |
| 6.7 | RAG 法规问答 | API + 前端 | ~400 行 | P3 |
| 6.8 | Agent 管理界面 | settings.html 扩展 | ~300 行 | P3 |

---

## 八、依赖关系图

```
Phase 1: 基础设施
  ├── MySQL 连接池 (db.py)
  ├── 13 张表 DDL (schema.sql)
  ├── YAML→MySQL 导入 (import_templates.py)
  └── 数据源验证 (verify_data_sources.py)
        │
        ▼
Phase 2: 知识图谱骨架
  ├── 法规全文检索 (knowledge_service.py)
  ├── 法规关系图查询 (regulation_graph.py) ★ 核心
  └── 违规行为查询 (knowledge_service.py)
        │
        ├──────────────────────────────┐
        ▼                              ▼
Phase 3: Agent 系统              Phase 4 先行: 表达式解析器
  ├── Agent 基类 (base.py)         (expression_parser.py)
  ├── Agent Registry                   │
  ├── 6 个 Agent                        │
  └── agents.yaml                      │
        │                              │
        └──────────┬───────────────────┘
                   ▼
Phase 4: 工作流 + API
  ├── 表达式执行引擎 (expression_engine.py)
  ├── LangGraph 工作流 (workflow/)
  ├── API 路由 (routes/audit_routes.py)
  └── app.py 注册路由
                   │
                   ▼
Phase 5: 前端真实数据对接
  ├── api.js 对齐
  ├── knowledge.js 改造
  ├── analysis.js 改造
  └── analysis-wiz.js 改造
                   │
                   ▼
Phase 6: 增强功能（并行进行）
  ├── FAISS 向量索引
  ├── MCP Server 封装
  ├── 文书生成
  └── 溯源面板
```

---

## 九、里程碑检查清单

### M1: 数据底座就绪（Phase 1 完成）
```
□ MySQL 连接池正常
□ 13 张表数据可查
□ 1000+ 模板已导入
□ 外部数据源验证通过
```

### M2: 知识可查询（Phase 2 完成）
```
□ 能用关键词搜索法规
□ 能展开法规关系树
□ 能搜索违规模型
```

### M3: Agent 可独立工作（Phase 3 完成）
```
□ 6 个 Agent 各自能独立完成自己的任务
□ Agent 输出符合定义的 Schema
□ Agent 能通过 MCP 工具查询知识图谱
```

### M4: 全流程可运行（Phase 4 完成）
```
□ 从"输入审计意图"到"生成疑点报告"的全流程走通
□ 人工确认断点正常
□ 前端可以通过 API 调用后端
```

### M5: 产品可演示（Phase 5 完成）
```
□ 前端无 mock 数据，全部从 API 加载
□ 7 步分析向导全流程可用
□ 知识工坊三库联动可用
□ 表达式引擎可视化可用
```

### M6: 产品可上线（Phase 6 完成）
```
□ 语义搜索可用
□ MCP 集成可用
□ 文书生成可用
□ 溯源链完整
□ 政府内网部署验证通过
```
