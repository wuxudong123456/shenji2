# 审计实务工坊 — 设计文档

> 对照 REQUIREMENTS.md 逐项设计实现方案

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                  OpenSquilla 网关 (:18791)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ Vue 3    │ │ WebSocket│ │ REST API │ │ 审计扩展路由   │  │
│  │ 控制台   │ │ RPC      │ │ /api/    │ │ /api/audit/   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 核心引擎: TurnRunner → SquillaRouter → Provider → Tools│  │
│  │ 记忆: SQLite+向量(项目级隔离) │ 沙箱: 文件/Shell隔离  │  │
│  │ MCP │ Skills │ Agents │ Search │ Scheduler │ Channels│  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 审计扩展层: 6 Agent │ 三大工坊服务 │ 溯源引擎 │ 表达式 │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
           │              │              │
    ┌──────┴─────┐ ┌─────┴─────┐ ┌──────┴──────┐
    │ MinIO      │ │ LLM 网关   │ │ MySQL       │
    │ :9100      │ │ :8765/8767│ │ 164/tt      │
    │ 项目Buckets│ │ OCR :5005  │ │ 15+ 张表    │
    └────────────┘ └───────────┘ └─────────────┘
```

## 2. 数据库设计 — 复用现有表 + 最少新增

### 2.1 直接复用 audit_law 库现有表 (不改结构)

| 现有表 | 复用为 | 覆盖需求 |
|--------|--------|---------|
| `sys_core_law_allaudit` | 法规依据库 | DK-02 法规全文检索 |
| `sys_core_law_subject_type` | 审计事项分类树 | DK-02 分类筛选 |
| `sys_core_law_subject_type_law` | 分类↔法规关联 | DK-02 分类关联 |
| `tools_clause_relation` | 条款分析 | DK-04 7类条款 |
| `tools_regulation_relation` | 法规关系链 | DK-03 上位法/下位法/相关法 |
| `sys_user` / `sys_role` / `sys_menu` | 用户/角色/菜单 | PG-08 系统设置 |
| `sys_dict_type` / `sys_dict_data` | 字典配置 | 通用字典 |
| `sys_config` | 系统配置 | 系统参数 |

### 2.2 不修改 audit_issues（共享知识库，不属于项目）

`audit_issues` 已被以下 4 表替代，不再使用：

| 现有表 (audit_law) | 用途 | 关系 |
|---|------|------|
| `sys_audititem` | 审计事项分类树 | id/pid/path_ids/level 树形结构 |
| `sys_audititem_meta` | 审计事项内容 | name/pro_name/pro_content/author |
| `sys_audititem_qualitative` | 定性依据 | audititem_id → law_id + law_items_paragraphs |
| `sys_audititem_punish` | 处罚依据 | audititem_id → law_id + law_items_paragraphs |

### 2.3 新建违规行为库 (tt 库)

独立建表，关联审计事项分类：

```sql
CREATE TABLE tt.audit_violations (
  id int AUTO_INCREMENT PRIMARY KEY,
  violation_code varchar(50) COMMENT '违规行为编码',
  violation_title text COMMENT '违规行为名称',
  audititem_id varchar(32) COMMENT '关联sys_audititem审计事项分类',
  category_path varchar(500) COMMENT '分类路径',
  severity varchar(20) DEFAULT 'medium' COMMENT 'high/medium/low',
  expression_text text COMMENT '违规表达式伪SQL (DK-06)',
  description text COMMENT '违规描述',
  source_file varchar(255) COMMENT '来源文件',
  author varchar(200) COMMENT '来源单位',
  import_batch varchar(100),
  is_reviewed tinyint DEFAULT 0,
  review_status varchar(20),
  creator varchar(64),
  create_time datetime DEFAULT CURRENT_TIMESTAMP,
  update_time datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted bit(1) DEFAULT b'0',
  KEY idx_code (violation_code),
  KEY idx_audititem (audititem_id),
  KEY idx_review (is_reviewed, review_status)
) COMMENT '违规行为库(DK-01)—关联sys_audititem审计事项分类';

### 2.3 新增表 (tt 库，仅 6 张)

```sql
-- 1. 项目
CREATE TABLE tt.audit_projects (
  id varchar(32) NOT NULL PRIMARY KEY,
  name varchar(200) NOT NULL,
  description text,
  audit_period varchar(100),
  minio_bucket varchar(100) COMMENT 'MinIO bucket名称',
  status varchar(20) DEFAULT 'draft',
  creator varchar(64), updater varchar(64),
  create_time datetime DEFAULT CURRENT_TIMESTAMP,
  update_time datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted bit(1) DEFAULT b'0'
) COMMENT '审计项目';

-- 2. 溯源锚点
CREATE TABLE tt.audit_document_traces (
  id int AUTO_INCREMENT PRIMARY KEY,
  project_id varchar(32) NOT NULL,
  issue_id int COMMENT '关联audit_issues.id',
  file_name varchar(500),
  minio_path varchar(1000),
  ocr_version int DEFAULT 1,
  ocr_content longtext,
  page_number int,
  position_anchor text COMMENT '位置锚点(段落/坐标)',
  ontosku_template varchar(500),
  extracted_fields JSON,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id),
  KEY idx_issue (issue_id)
) COMMENT '文档溯源—OCR结果可追溯到原始文件页码';

-- 3. AI 对话
CREATE TABLE tt.audit_conversations (
  id int AUTO_INCREMENT PRIMARY KEY,
  session_id varchar(100) NOT NULL COMMENT 'OpenSquilla session_id',
  project_id varchar(32),
  page varchar(100) COMMENT '来源页面',
  title varchar(500),
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_session (session_id),
  KEY idx_project (project_id)
) COMMENT 'AI对话记录';

-- 4. 分析任务
CREATE TABLE tt.audit_analysis_tasks (
  id int AUTO_INCREMENT PRIMARY KEY,
  project_id varchar(32) NOT NULL,
  title varchar(500),
  step tinyint DEFAULT 1 COMMENT '当前步骤1-6',
  step_data JSON COMMENT '各步骤数据',
  agent_results JSON COMMENT '6 Agent返回结果',
  status varchar(20) DEFAULT 'draft',
  result text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  updated_at datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_project (project_id)
) COMMENT '六步智能分析任务';

-- 5. 模板库 (1511 YAML → MySQL)
CREATE TABLE tt.audit_templates (
  id int AUTO_INCREMENT PRIMARY KEY,
  name varchar(500) NOT NULL UNIQUE,
  domain varchar(50),
  category varchar(100),
  doc_type varchar(200),
  description text,
  guideline text,
  output_fields JSON,
  tags JSON,
  is_active tinyint DEFAULT 1,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_domain (domain),
  KEY idx_category (category)
) COMMENT 'OntoSKU 1511审计模板';

-- 6. 数据工坊 — 6 张表覆盖 19 子类 (核心列+JSON扩展)
CREATE TABLE tt.data_contracts (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int COMMENT '溯源锚点', template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  party_a varchar(500), party_b varchar(500),
  amount decimal(20,2), currency varchar(10),
  sign_date date, effective_date date, expiry_date date,
  contract_no varchar(200), procurement_method varchar(100),
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '合同协议类';

CREATE TABLE tt.data_finance (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int, template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  account_name varchar(500), account_no varchar(100),
  debit_amount decimal(20,2), credit_amount decimal(20,2),
  voucher_no varchar(100), voucher_date date,
  bank_name varchar(500), currency varchar(10),
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '财务凭证/票据/账簿类';

CREATE TABLE tt.data_legal_docs (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int, template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  case_no varchar(200), issuing_body varchar(500),
  doc_date date, effective_date date,
  legal_basis text, verdict text,
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '法律文书/审查报告/规章制度类';

CREATE TABLE tt.data_registers (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int, template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  register_type varchar(200), item_name varchar(500),
  quantity decimal(20,2), unit varchar(50),
  responsible_person varchar(200), register_date date,
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '登记台账/清单名册/记录留痕类';

CREATE TABLE tt.data_credentials (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int, template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  cert_type varchar(200), cert_no varchar(200),
  holder varchar(500), issue_date date, expire_date date,
  issuing_body varchar(500),
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '资质证照/业务单据/影像图件类';

CREATE TABLE tt.data_general (
  id int AUTO_INCREMENT PRIMARY KEY, project_id varchar(32) NOT NULL,
  document_trace_id int, template_name varchar(500),
  doc_name varchar(500), doc_type varchar(200),
  category varchar(200), title varchar(500),
  summary text, issuing_body varchar(500),
  doc_date date,
  extra_fields JSON, raw_text text,
  created_at datetime DEFAULT CURRENT_TIMESTAMP,
  KEY idx_project (project_id), KEY idx_trace (document_trace_id)
) COMMENT '其他杂项/数据表格/政策文件/历史档案/数据信息类';
```

### 2.4 表汇总

| 位置 | 表数 | 表名 |
|------|------|------|
| audit_law (复用) | 9 | **sys_audititem 四表(法规依据库)**, sys_core_law_allaudit(法律全文), tools_clause_relation, tools_regulation_relation, sys_core_law_subject_type, sys_core_law_subject_type_law, sys_user/role/menu, sys_dict_*, sys_config |
| tt (新增) | 13 | audit_projects, audit_document_traces, audit_conversations, audit_analysis_tasks, audit_templates, audit_violations, data_contracts~general(6张), project_suspicions |

**合计新增 13 张表，法规依据直接用 sys_audititem 四表**

## 3. 后端设计 — 审计扩展模块

### 3.1 新增模块目录

```
src/opensquilla/audit/           # 审计扩展
├── __init__.py                  # 注册扩展
├── db.py                        # MySQL 连接（pymysql, 连接池）
├── routes.py                    # 审计 REST API
├── agents/                      # 6 个 Agent
│   ├── __init__.py
│   ├── base.py                  # Agent 基类
│   ├── intent_analyzer.py       # DA-01
│   ├── violation_matcher.py     # DA-02-1
│   ├── data_advisor.py          # DA-02-2
│   ├── regulation_advisor.py    # DA-02-3 ★
│   ├── audit_analyzer.py        # DA-05
│   └── suspicion_generator.py   # DA-06
├── services/
│   ├── minio_service.py         # MinIO bucket 管理 (DW-01)
│   ├── ocr_service.py           # MinerU OCR 调用 (DW-03)
│   ├── ontosku_service.py       # OntoSKU 元数据抽取 (DW-05)
│   ├── trace_service.py         # 溯源锚点服务 (DW-06 NF-01)
│   ├── expression_engine.py     # 违规表达式解析执行 (DK-06)
│   └── nl2sql_service.py        # 智能问数 NL→伪SQL (DD-03)
└── templates/                   # Prompt 模板
    ├── intent_analyzer.txt
    ├── violation_matcher.txt
    ├── regulation_advisor.txt
    └── ...
```

### 3.2 REST API 路由 (routes.py)

```
项目管理:
  POST   /api/audit/projects                    ← 创建项目 + MinIO bucket (DW-01)
  GET    /api/audit/projects                    ← 列表
  DELETE /api/audit/projects/{id}               ← 删除

文件管理:
  POST   /api/audit/projects/{id}/upload         ← 上传→OCR→OntoSKU (DW-02~05)
  GET    /api/audit/projects/{id}/files          ← 文件列表+解析状态 (DW-08)
  GET    /api/audit/documents/{id}/trace         ← 溯源锚点 (DW-06 NF-01)
  POST   /api/audit/documents/{id}/reparse       ← 重新推理 (DW-07)

数据工坊:
  GET    /api/audit/projects/{id}/data           ← 数据表列表 (DD-01)
  GET    /api/audit/data/{table}/rows            ← 数据浏览+筛选 (DD-02 DD-05)
  POST   /api/audit/data/query                   ← 智能问数 (DD-03 DD-04)
  GET    /api/audit/data/{table}/export          ← 导出 CSV (DD-06)

知识工坊:
  GET    /api/audit/knowledge/violations          ← 违规行为检索 (DK-01)
  GET    /api/audit/knowledge/regulations         ← 法规检索 (DK-02)
  GET    /api/audit/knowledge/regulation/{id}/graph ← 法规关系链 (DK-03)
  GET    /api/audit/knowledge/clauses/{law_id}    ← 条款分析 (DK-04)
  GET    /api/audit/knowledge/cases               ← 案例库 (DK-05)
  POST   /api/audit/expression/execute            ← 执行违规表达式 (DK-06)
  POST   /api/audit/suspicion/generate            ← 生成疑点报告 (DK-08)

智能分析:
  POST   /api/audit/analysis                     ← 创建分析任务 (DA-01)
  GET    /api/audit/analysis/{id}                ← 查询状态
  POST   /api/audit/analysis/{id}/step/{n}       ← 执行步骤 (DA-02~06)
  POST   /api/audit/analysis/{id}/confirm        ← 人工确认断点 (DA-03)

Agent 管理 (AG-01~06):
  GET    /api/audit/agents                       ← Agent 列表
  POST   /api/audit/agents                       ← 创建/编辑 Agent
  PUT    /api/audit/agents/{id}                  ← 更新 Agent 配置
  DELETE /api/audit/agents/{id}                  ← 删除
```

### 3.3 违规表达式引擎设计 (DK-06)

伪 SQL 语法示例：
```
采购合同.采购方式 = '询价'
  AND 采购合同.金额 > 1000000
  AND 采购合同.签订日期 BETWEEN '2026-03-01' AND '2026-03-31'
```

处理流程：
```
用户伪SQL → LLM解析（表名→字段映射，值→类型推断）
  → 生成 AST（AND/OR/BETWEEN/=/!=/>/</IN/LIKE）
  → AST → 对目标表逐行求值
  → 返回 {total, hits, rows: [{row, matched: bool, fields: [...]}]}
  → 前端渲染表达式树 + 扫描动画
```

### 3.4 溯源链设计 (NF-01)

```
疑点报告
  └→ audit_suspicion_reports.suspicion_items[].source → 数据工坊行
      └→ data_xxx.document_trace_id → audit_document_traces
          └→ {ocr_content, page_number, position_anchor}
              └→ MinIO 原始 PDF 特定页面
```

## 4. 前端设计

前端保持 AuditWorkbench 现有 13 页 + 全局框架，部署在 OpenSquilla 网关 `/control/` 下：

| 页面 | 对接 API | 关键组件 |
|------|---------|---------|
| index.html | 综合门户 | 仪表盘 + 最近分析 |
| projects.html | /api/audit/projects | 项目 CRUD |
| analysis.html | /api/audit/analysis | 六步向导 ★ |
| knowledge.html | /api/audit/knowledge/* | 三库联动 |
| dataworkshop.html | /api/audit/data/* | 数据浏览+智能问数 |
| docworkshop.html | /api/audit/projects/{id}/* | 文件上传+模板 |
| lawqa.html | /api/chat (OpenSquilla) | 法规问答 |
| qualification.html | /api/audit/suspicion | 审计定性 |
| documents.html | /api/audit/expression | 文书生成 |
| review.html | /api/audit/analysis | 审理复核 |
| toolbox.html | /api/audit/ocr | 工具箱 |
| settings.html | /api/audit/agents | 系统设置+Agent管理 |
| workspace.html | /api/audit/conversations | 我的空间 |

**前端架构**：
- 全局框架：`js/app.js` (导航/侧边栏/主题/任务)
- API 层：`js/api.js` (指向同源 `/api/audit/` 和 OpenSquilla `/api/chat`)
- 组件：`LawSelector` (法规选择器) / `FileUploader` (文件上传) / `ExpressionEngine` (表达式可视化) / `TraceAnchor` (溯源定位)

## 5. 部署设计

```
192.168.3.164
├── /data/opensquilla_rc3/      # OpenSquilla 网关 + 审计扩展
│   ├── opensquilla-main/       # RC4 源码 + src/opensquilla/audit/
│   ├── config.toml             # 含 MySQL/MinIO/LLM 配置
│   ├── state/                  # Memory 项目级隔离
│   └── start.sh                # 启动脚本
├── MinIO :9100                 # audit-project-{id} buckets
├── MySQL :3306                 # tt 库 (15+ 张表)
└── 已有服务复用:
    ├── 189:8765  LLM 文本
    ├── 189:8767  LLM 多模态
    ├── 189:5005  MinerU OCR + OntoSKU
    └── audit_law 库 (法规数据源)
```
