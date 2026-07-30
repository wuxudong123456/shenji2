# 审计实务工坊 — 详细设计规格说明书

> 版本 1.0.0 | 基于 OpenSquilla 0.5.0rc4 | 2026-07-17

## 目录

1. [系统架构](#1-系统架构)
2. [全局框架](#2-全局框架)
3. [首页仪表盘](#3-首页仪表盘)
4. [审计项目管理](#4-审计项目管理)
5. [智能分析向导](#5-智能分析向导)
6. [三大工坊](#6-三大工坊)
7. [智能工具](#7-智能工具)
8. [系统设置](#8-系统设置)
9. [AI 智能体](#9-ai-智能体)
10. [基础设施](#10-基础设施)
11. [API 接口](#11-api-接口)
12. [数据流与关联](#12-数据流与关联)

---

## 1. 系统架构

```
浏览器 (Chrome/Edge) 
    │  http://192.168.3.164:18791/control/static/audit/
    ▼
OpenSquilla 网关 (:18791)
├── StaticFileServer → /control/static/audit/ (HTML/CSS/JS)
├── REST API → /api/audit/* (审计业务)
├── Chat API → /api/chat (AI 对话)
├── WebSocket → /ws (实时推送)
└── Control UI → /control/ (Vue 管理台)
    │
    ├── MySQL (192.168.3.164:3306)
    │   ├── audit_law 库 — 法规数据 (复用12表)
    │   └── tt 库 — 业务数据 (新建13表)
    │
    ├── MinIO (192.168.3.164:9100)
    │   └── audit-project-{id} buckets
    │
    ├── LLM Gateway (192.168.3.189:8765/8767)
    │
    └── OCR/OntoSKU (192.168.3.189:5005)
```

### 技术栈

| 层 | 技术 |
|---|------|
| 前端 | Vanilla HTML5 + CSS3 + ES6 JS, Bootstrap Icons 1.11 |
| 网关 | Python Starlette ASGI, WebSocket |
| AI 运行时 | OpenSquilla Agent Runtime |
| 数据库 | MySQL 8.0 (pymysql) |
| 对象存储 | MinIO (minio-py) |
| LLM | OpenAI-compatible API (DeepSeek V4) |
| OCR | MinerU, OntoSKU |

---

## 2. 全局框架

### 2.1 导航栏 (Navbar)

**文件**: `js/app.js → AuditWorkbench.renderNavbar()`

**UI 元素**:
```
┌──────────────────────────────────────────────────────────┐
│ [🏢] 审计实务工坊 v1.0.0    [📁 当前项目] [⏳] [?] [👤 审计员 ▼] │
└──────────────────────────────────────────────────────────┘
```

- **品牌区**: 图标 + 名称 + 版本号，点击回到首页
- **项目指示器**: 显示当前激活项目名（`aw_proj` localStorage），点击跳转项目页
- **任务徽标**: 后台任务计数（OCR/分析进度），红点待处理 / 绿点已完成
- **帮助**: 首次访问弹出引导遮罩，介绍6大Agent和4大能力
- **用户菜单**: 审计员信息 · 个人资料 · 修改密码 · 退出

### 2.2 侧边栏 (Sidebar)

**结构**:
```
┌──────────────┐
│ 📊 首页       │
│ 📁 审计项目   │  ← 可展开: 智能分析
│ ─────────── │
│ 三大工坊      │
│ 📚 知识工坊   │  ← 可展开: 违规库/依据库/案例库
│ 🔢 数据工坊   │  ← 可展开: 数据表/智能问数
│ 📋 资料工坊   │  ← 可展开: 文档/模板/重新推理
│ ─────────── │
│ 智能工具      │
│ 💬 法规问答   │
│ 🎯 审计定性   │
│ 📝 文书生成   │
│ ✅ 审理复核   │
│ 🧰 工具箱     │
│ ─────────── │
│ 配置         │
│ 👤 我的空间   │
│ ⚙️ 系统设置   │  ← 新增 tab
└──────────────┘
```

**交互**: 
- 点击展开/折叠分组（accordion 模式，只保持一个展开）
- 当前页高亮 + 左侧红色边框
- 支持折叠（56px 图标模式），hover 显示 tooltip
- 折叠状态持久化到 localStorage

### 2.3 Toast 通知

**文件**: `AuditWorkbench.notify(msg, type)`

| type | 颜色 | 图标 | 自动消失 |
|------|------|------|---------|
| success | 绿 #2d7d46 | ✅ | 3.5秒 |
| error | 红 #c41e3a | ❌ | 3.5秒 |
| warning | 橙 #b85e1a | ⚠️ | 3.5秒 |
| info | 蓝 #1a5c8a | ℹ️ | 3.5秒 |

### 2.4 认证

- URL 参数 `?token=admin` 自动提取并存入 sessionStorage
- API 请求自动附带 `Authorization: Bearer {token}` header
- 同源部署（无需跨域），网关 auth.mode=token

### 2.5 项目上下文

- `AuditWorkbench.setProject(id, name)` — 设置当前项目
- `AuditWorkbench.getProject()` — 读取 `{id, name}`
- 持久化到 localStorage (`aw_proj`)
- 导航栏实时显示当前项目名

---

## 3. 首页仪表盘

**文件**: `index.html` + `js/portal.js` + `js/api.js`

### 3.1 欢迎横幅

```
┌──────────────────────────────────────────────────────┐
│  下午好，欢迎回来                                      │
│  AuditWorkbench 审计实务工坊 · AI多智能体分析平台      │    2026年7月17日
│                                                      │    星期五
└──────────────────────────────────────────────────────┘
```

### 3.2 统计卡片

| 卡片 | 数据源 | 更新方式 |
|------|--------|---------|
| 进行中项目 | `API.projects.list()` count | 页面加载 |
| 待处理任务 | `localStorage aw_bg_tasks` | 本地 |
| 本月完成 | 本地模拟 | 后续对接后端 |
| 案例总数 | 固定 2,231 | 后续对接后端 |

### 3.3 资料处理状态

```
┌──────────────────────────────────────────────────────┐
│ 📄 审计资料处理状态                        [资料工坊 →] │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐              │
│ │  6   │ │  5   │ │  0   │ │  1   │              │
│ │资料总数│ │已完成 │ │识别中 │ │待处理 │              │
│ └──────┘ └──────┘ └──────┘ └──────┘              │
│ ● 银行流水2026Q1.csv  识别中...  [查看]              │
└──────────────────────────────────────────────────────┘
```

**交互**: 
- 面板可折叠到右上角（最小化指示器）
- 显示正在处理的文件列表
- 支持展开/收缩动画

### 3.4 快捷入口

4 个卡片网格:
- 🧠 智能分析 — 多Agent流水线，意图→疑点
- 📚 知识工坊 — 违规库+依据库+案例库
- 🔢 数据工坊 — 结构化数据表+智能问数
- 💬 法规问答 — RAG驱动的法规智能问答

### 3.5 最近动态 + 待办

- 最近实务动态（时间线列表）
- 待办事项（checkbox 列表，可勾选）
- 优先级标识（红/橙色条）

---

## 4. 审计项目管理

**文件**: `projects.html` + inline JS

### 4.1 项目列表

```
┌──────────────────────────────────────────────────────┐
│ 📁 审计项目管理                                       │
│ [输入项目名称...] [审计期间] [➕ 新建项目]              │
│                                                      │
│ ┌────────────────────────────────────────────────┐   │
│ │ 📁 市教育局2026采购审计  [草稿]   [智能分析] [删除] │   │
│ │ 教学设备采购招标合规性审计 · 期间: 2026            │   │
│ │ 创建: 2026-07-17                                │   │
│ └────────────────────────────────────────────────┘   │
│ ┌────────────────────────────────────────────────┐   │
│ │ 📁 当前项目 ← 绿色边框 + 徽标    [智能分析] [删除] │   │
│ └────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

**API**: `POST /api/audit/projects` → 自动创建 MinIO bucket `audit-project-{id}`

### 4.2 项目详情

4 个 Excel Sheet 风格 Tab:
- **项目基本情况**: 名称/编号/被审计单位/审计类型/期间/层级/审计组长/主审/审计目标
- **被审计单位情况**: 单位性质/职能/预算规模/内控/风险点
- **被审计行业政策**: 关联法规列表（带溯源链接）
- **项目资料**: 四阶段（审计准备/实施/报告/整改）业务文档

**操作**: [启动智能分析] [修改项目信息] 按钮始终可见

---

## 5. 智能分析向导

**文件**: `analysis.html` + `js/analysis-wiz.js` + `js/analysis.js`

### 5.1 7 步向导

```
┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
│ ①    │  │ ②    │  │ ③    │  │ ④    │  │ ⑤    │  │ ⑥    │  │ ⑦    │
│意图  │→│方法  │→│审计  │→│资料  │→│审核  │→│疑点  │→│文书  │
│判断  │  │推荐  │  │依据  │  │分析  │  │比对  │  │核实  │  │编撰  │
└──────┘  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘
```

### 5.2 AI 审计助手聊天面板

左侧聊天区，右侧结构化确认区。

**Agent 调用链**:
```
Step① IntentAnalyzer (输入意图)
    ↓
Step② { ViolationMatcher ∥ DataAdvisor ∥ RegulationAdvisor } 并行
    ↓
Step③ 人工确认断点 → LawSelector 组件
    ↓
Step④ 文件上传 → MinerU OCR → OntoSKU 元数据抽取
    ↓
Step⑤ AuditAnalyzer (逐模型比对+异常检测)
    ↓
Step⑥ SuspiciousPointGen (结构化疑点报告)
    ↓
Step⑦ FinalReviewer 终审评判 → 输出
```

### 5.3 法规选择器 (LawSelector)

**交互流程**:
1. 搜索法规名称/文号/关键词 → 显示匹配结果列表
2. 点击选中主法 → 展开关系树（上位法/下位法/相关法/历史版本）
3. 浏览条款列表（7类条款分类），勾选条款
4. 右侧确认面板实时显示已选依据
5. [确认依据] 按钮 → 提交到分析任务

**数据源**: 
- 法规检索: `GET /api/audit/knowledge/regulations?q={keyword}`
- 关系链: `GET /api/audit/knowledge/regulation/{id}/graph`
- 条款: `GET /api/audit/knowledge/clauses/{law_id}`

---

## 6. 三大工坊

### 6.1 知识工坊

**文件**: `knowledge.html` + `js/knowledge.js`

**三库联动**:

| Tab | 数据源 | 检索方式 | 关联 |
|-----|--------|---------|------|
| 违规行为库 | `audit_violations` + `sys_audititem` 四表 | 关键词搜索 + 分类筛选 | → 法规依据 → 案例 |
| 审计依据库 | `sys_core_law_allaudit` | 关键词 + 效力级别筛选 | → 关系链展开 → 条款匹配 |
| 审计案例库 | `audit_cases` (待实现) | 关键词 + 领域筛选 | → 违规模型 → 法规 → 同类型案例 |

**交互**:
- 顶部 Tab 切换
- 搜索框实时过滤
- 点击法规标题 → 弹窗显示关系链（上位法/下位法/相关法）
- 点击违规项 → 显示描述 + 表达式
- 点击案例 → 三向关联展示

**实验室功能** (🧪 开启后):
- 智能发现: AI 扫描三库发现隐藏模式
- 违规模式分析: 高频违规组合 + 趋势
- 法规关联挖掘: 隐性引用关系
- 案例聚类: 相似案例自动分组

### 6.2 数据工坊

**文件**: `dataworkshop.html` + inline JS

**6 张数据表**:

| 表名 | 覆盖类型 | 核心字段 | 模板数 |
|------|---------|---------|--------|
| `data_contracts` | 合同协议类 | party_a/b, amount, sign_date, procurement_method | ~80 |
| `data_finance` | 财务凭证/票据/账簿 | account, debit/credit, voucher_no | ~120 |
| `data_legal_docs` | 法律文书/审查报告 | case_no, issuing_body, verdict | ~200 |
| `data_registers` | 台账/清单/记录 | register_type, item_name, quantity | ~180 |
| `data_credentials` | 资质证照/业务单据 | cert_type, cert_no, holder | ~150 |
| `data_general` | 其他综合类 | category, title, summary | ~180 |

**表卡片 UI**: 6 张卡片网格，每张显示图标 + 名称 + 描述 + 行数统计

**数据浏览**:
- 点击卡片 → 选中高亮 → 下方表格展示数据行
- 分页（每页20条）
- 项目筛选下拉框
- 每行尾部 "📍溯源" 链接 → 原始文件位置

**智能问数** (🧪 实验室):
```
用户自然语言 → LLM 解析意图 → 生成伪SQL表达式
  → 用户确认/修改 → 执行表达式 → 表格展示命中结果
```

**违规表达式引擎** (🧪 实验室):
```
伪SQL: data_contracts.amount > 1000000 AND data_contracts.procurement_method = '询价'
  ↓ 解析为 AST 树 (AND/OR 逻辑)
  ↓ 对数据表逐行求值
  ↓ 返回 {total, hits, matches[{row, matched}]}
  ↓ 前端渲染扫描结果 + 高亮命中行
```

**API**: 
- `GET /api/audit/projects/{id}/data` — 表列表+行数
- `GET /api/audit/data/{table}/rows` — 数据浏览
- `POST /api/audit/data/query` — 智能问数
- `POST /api/audit/expression/execute` — 表达式引擎

### 6.3 资料工坊

**文件**: `docworkshop.html` + inline JS

**功能模块**:

| 模块 | 功能 | 状态 |
|------|------|------|
| 📄 我的文档 | 列表/查看/下载/溯源定位/重新解析 | 前端就绪 |
| 📋 提取模板 | 浏览 1,511 模板/按领域筛选 | 前端就绪 |
| 🕐 重新推理 | 切换模板重新抽取 | API 就绪 |

**文件上传流程**:
```
用户拖拽/选择文件
  → MinIO 存储: audit-project-{id}/raw/{file_id}/{filename}
  → MinerU OCR 解析 → Markdown
  → OCR MD 存入同桶: audit-project-{id}/raw/{file_id}/{name}.md
  → OntoSKU 模板匹配 → 字段抽取
  → 写入数据工坊对应表 (data_contracts 等)
  → 创建溯源锚点 (audit_document_traces)
  → 返回 {file_name, minio_bucket, raw_path, ocr_md_path, trace_id, media_type}
```

**媒体识别** (🧪 实验室):
- 语音: mp3/wav → Whisper 转文字 → MD 存储
- 图像: jpg/png → 多模态 LLM 理解 + OCR → MD 存储
- 文档: pdf/docx → MinerU → MD 存储

**API**:
- `POST /api/audit/projects/{id}/upload` — 上传+OCR
- `GET /api/audit/projects/{id}/files` — 文件列表
- `GET /api/audit/documents/{id}/trace` — 溯源
- `POST /api/audit/documents/reparse` — 重新推理

---

## 7. 智能工具

### 7.1 法规问答

**文件**: `lawqa.html` + inline JS

**功能**: RAG 驱动的法规深度问答
- 用户输入问题 → AI Agent 回复
- 回复中标注法规来源（🔗 关系链 + 📤 引用）
- 建议问题快捷入口
- 多轮对话支持

### 7.2 审计定性

**文件**: `qualification.html`

**功能**: 违规行为法律定性辅助
- 输入问题描述 → 自动匹配违规模型
- 构建法规依据链（定性→处罚→追责）
- 同类案例推荐

### 7.3 文书生成

**文件**: `documents.html`

**功能**: AI 自动生成审计文书
- 取证单 / 审计底稿 / 审计报告 / 审理复核意见书
- 模板填充 + 导出 Word/PDF

### 7.4 审理复核

**文件**: `review.html`

**功能**: AI 推理 vs 人工复核双栏对比

### 7.5 工具箱

**文件**: `toolbox.html`

**功能**: OCR 解析 / 法规对比 / 门槛金额计算 / 表达式测试

### 7.6 我的空间

**文件**: `workspace.html`

**功能**: 个人分析历史 / 依据收藏 / 自定义表达式 / 导出记录

---

## 8. 系统设置

**文件**: `settings.html` (全新独立文件, 自包含 JS)

### 8.1 导航结构

```
┌─────────────────────────────────────────────┐
│ [业务设置] [软件设置] [🧪 实验室]              │  ← L1 Tabs
├─────────────────────────────────────────────┤
│ [项目管理规则] [模板管理] [用户偏好] [智能体配置] │  ← L2 Tabs (随L1切换)
└─────────────────────────────────────────────┘
```

### 8.2 业务设置面板

#### 项目管理规则 (`panel-rules`)
- 现场实施阶段: 7 项审计期限配置 (预算执行30天/经责45天/国企60天...)
- 出具审计报告阶段: 8 个步骤期限 (征求意见稿→送达)
- 审计整改阶段: 台账上传日期+提前提醒天数
- 所有值可编辑，[保存规则] 按钮

#### 模板管理 (`panel-templates`)
- 搜索框+领域筛选
- 表格: 模板名称 | 领域 | 类型 | 操作
- API 加载: `GET /api/audit/templates`
- 点击 "使用" → 跳转资料工坊

#### 用户偏好 (`panel-preferences`)
- 默认审计领域/层级/AI确认模式
- 界面偏好: 通知方式 (Toast/徽标/浏览器通知)
- [保存偏好] 按钮

#### 智能体配置 (`panel-agents`)
- API 加载: `GET /api/audit/agents`
- 7个Agent卡片: 图标+彩色头+名称+角色+系统/自定义标签
- 显示绑定模型和 MCP 工具

### 8.3 软件设置面板

#### LLM 服务 (`panel-llm`)
- 文本推理网关: 地址/模型/超时
- 多模态服务: 地址/模型
- MinIO 对象存储: 地址/Key/Bucket
- OCR 引擎: MinerU/LiteParse 选择 + 地址
- [保存配置] [测试连接]

#### 数据源 (`panel-datasource`)
- MySQL 连接信息: 主机/端口/数据库
- 数据统计: 法规总表400K / 审计法规12K / 关系链31K / 条款119K / SKU 1,511

#### 沙箱保护 (`panel-sandbox`) 🆕
- 沙箱模式: 受信主机 / 完全隔离
- 工作区目录 (只读)
- 文件操作权限: 读/写/删除 checkbox
- 网络范围: 仅内网 / 允许外网

#### 记忆管理 (`panel-memory`) 🆕
- 存储位置: 本地路径 + 大小 (11MB)
- 隔离模式: 项目级隔离 / 全局共享
- 向量引擎: sqlite-vec / OpenAI
- [清除项目记忆] 操作按钮

#### 系统管理 (`panel-system`) 🆕
- SquillaRouter 路由: R0-R3 模型配置
- MCP 服务器状态: MySQL/MinIO/LLM/OCR (各带状态点)
- Skills: 6 Agent Skills · OntoSKU Skill
- Cron: 0 活跃定时任务
- 系统信息: 网关版本/审计模块/控制台链接

### 8.4 实验室面板 (`panel-lab`) 🆕

**一键总开关** (金色边框卡片):
- 主 toggle + "全部关闭/全部已开启/部分开启" 文本

**数据工坊实验室** (绿色左边框):
- ☑ 智能问数 — NL→伪SQL
- ☑ 表达式引擎 — 逐行扫描
- ☑ 数据导出 — CSV/Excel

**资料工坊实验室** (橙色左边框):
- ☑ 语音识别 — 音频→文字+MD
- ☑ 图像识别 — 多模态理解+OCR
- ☑ 知识图谱 — 文档关联可视化
- ☑ 文档生成 — AI 生成审计文书

**知识工坊实验室** (蓝色左边框):
- ☑ 智能发现 — AI 扫描三库
- ☑ 违规模式 — 高频组合+趋势
- ☑ 法规关联 — 隐性引用挖掘
- ☑ 案例聚类 — 自动分组

**持久化**: 每个开关独立存储在 `localStorage.aw_lab_{ws}_{feat}`

**关联**: 开启后，对应工坊页面加载时检查 `localStorage` 并显示对应功能面板

---

## 9. AI 智能体

### 9.1 Agent 列表

| # | Agent | 角色 | 模型 | MCP 工具 | 步骤 |
|---|-------|------|------|---------|------|
| 1 | IntentAnalyzer | 意图分析 — 读懂审计目标 | deepseek-v4-flash | llm_gateway | ① |
| 2 | ViolationMatcher | 违规匹配 — 从2195模型匹配 | deepseek-v4-flash | faiss_violations, llm_gateway | ② |
| 3 | DataAdvisor | 资料顾问 — 推荐资料清单 | deepseek-v4-flash | llm_gateway | ②∥ |
| 4 | RegulationAdvisor | 法规顾问 — 法规推荐+关系链+层级建议 | deepseek-v4-flash | mcp_mysql, faiss, llm | ②∥ |
| 5 | AuditAnalyzer | 审计分析 — 逐模型比对+异常检测 | deepseek-v4-flash | llm, multimodal, ontosku | ⑤ |
| 6 | SuspiciousPointGen | 疑点生成 — 结构化疑点报告 | deepseek-v4-flash | llm_gateway | ⑥ |
| 7 | FinalReviewer | 终审评判 — 审核所有输出→通过/修正/驳回 | deepseek-v4-pro | llm, mcp_mysql | ⑦ |

### 9.2 Agent 配置

存储: `audit_agents` 表 (tt 库)

字段:
- `name` (英文标识) · `display_name` (中文名)
- `role` (角色描述) · `system_prompt` (提示词)
- `icon` (Bootstrap图标) · `color` (主题色)
- `model` (默认模型) · `tools` (JSON, MCP工具列表)
- `is_system` (系统预置不可删) · `is_active`

API:
- `GET /api/audit/agents` — 列表
- `POST /api/audit/agents` — 创建
- `PUT /api/audit/agents/{id}` — 更新
- `DELETE /api/audit/agents/{id}` — 删除(仅自定义)
- `POST /api/audit/agents/{id}/run` — 执行

### 9.3 工作流

```
IntentAnalyzer (串行)
    ↓
{ViolationMatcher ∥ DataAdvisor ∥ RegulationAdvisor} (并行)
    ↓
人工确认断点 (LawSelector)
    ↓
文件上传+OCR+OntoSKU
    ↓
AuditAnalyzer
    ↓
SuspiciousPointGen
    ↓
FinalReviewer (终审→通过/修正/驳回)
    ↓
输出给用户
```

---

## 10. 基础设施

### 10.1 数据库

**tt 库 (新建 13 表)**:

| 表 | 用途 | 关键字段 |
|---|------|---------|
| `audit_projects` | 项目管理 | id(varchar32), name, minio_bucket, status |
| `audit_document_traces` | 溯源锚点 | project_id, file_name, minio_path, ocr_content, page_number |
| `audit_conversations` | AI对话记录 | session_id, project_id, page |
| `audit_analysis_tasks` | 分析任务 | project_id, step, step_data(JSON), agent_results(JSON) |
| `audit_templates` | 1511模板 | name, domain, category, output_fields(JSON) |
| `audit_violations` | 违规行为库 | violation_code, audititem_id, expression_text |
| `audit_agents` | 7 Agent配置 | name, system_prompt, tools(JSON), is_system |
| `project_suspicions` | 疑点报告 | project_id, suspicion_items(JSON), evidence_chain(JSON) |
| `data_contracts` | 合同协议类 | party_a/b, amount, procurement_method |
| `data_finance` | 财务类 | account, debit/credit, voucher_no |
| `data_legal_docs` | 法律文书类 | case_no, issuing_body, verdict |
| `data_registers` | 台账清单类 | register_type, item_name, quantity |
| `data_credentials` | 资质证照类 | cert_type, cert_no, holder |
| `data_general` | 综合类 | category, title, summary |

**audit_law 库 (复用 12 表)**:

`sys_core_law_allaudit` · `sys_core_law_subject_type` · `sys_core_law_subject_type_law` · `tools_clause_relation` · `tools_regulation_relation` · `sys_audititem` · `sys_audititem_meta` · `sys_audititem_qualitative` · `sys_audititem_punish` · `sys_user` · `sys_role` · `sys_menu` · `sys_dict_type` · `sys_dict_data` · `sys_config`

### 10.2 MinIO

- 端点: 192.168.3.164:9100 (ontosku/ontosku12345)
- 项目桶命名: `audit-project-{12位hex}`
- 桶内结构: `{pid}/raw/{file_id}/{filename}` + `{filename}.md`
- 建项目自动建桶; 补充脚本可回溯建桶

### 10.3 LLM 网关

- 文本: 192.168.3.189:8765/v1 (deepseek-v4-flash/pro)
- 多模态: 192.168.3.189:8767/v1
- OCR/SKU: 192.168.3.189:5005 (MinerU + OntoSKU)

### 10.4 记忆系统

- 引擎: OpenSquilla 内置 SQLite + sqlite-vec
- 存储: `/data/opensquilla_rc3/state/` (11MB)
- 隔离: 每项目独立 session_id，切换项目=切换上下文
- 项目级记忆: 对话记录表 `audit_conversations` 按 `project_id` 过滤

### 10.5 沙箱

- 模式: TrustedHost (受信主机)
- 工作区: `/data/opensquilla_rc3/workspace`
- 文件操作: 读/写允许，删除需审批
- 网络: 仅内网 (192.168.0.0/16)

---

## 11. API 接口

所有接口前缀 `/api/audit/`，认证 Header: `Authorization: Bearer admin`

### 11.1 项目管理

| Method | Path | 说明 |
|--------|------|------|
| GET | `/projects` | 列表 |
| POST | `/projects` | 创建 (自动建MinIO桶) |
| GET | `/projects/{id}` | 详情 |
| DELETE | `/projects/{id}` | 软删除 |

### 11.2 文件管理

| Method | Path | 说明 |
|--------|------|------|
| POST | `/projects/{id}/upload` | 上传→OCR→OntoSKU→数据工坊 |
| GET | `/projects/{id}/files` | 文件列表+解析状态 |
| GET | `/documents/{id}/trace` | 溯源锚点 |
| POST | `/documents/reparse` | 切换模板重新抽取 |

### 11.3 数据工坊

| Method | Path | 说明 |
|--------|------|------|
| GET | `/projects/{id}/data` | 6表列表+行数 |
| GET | `/data/{table}/rows` | 数据浏览+分页 |
| POST | `/data/query` | 智能问数 (NL→伪SQL) |

### 11.4 知识工坊

| Method | Path | 说明 |
|--------|------|------|
| GET | `/knowledge/violations` | 违规行为检索 |
| GET | `/knowledge/regulations` | 法规检索 |
| GET | `/knowledge/regulation/{id}/graph` | 法规关系链 |
| GET | `/knowledge/clauses/{law_id}` | 条款列表 |

### 11.5 表达式 + 疑点

| Method | Path | 说明 |
|--------|------|------|
| POST | `/expression/execute` | 伪SQL执行 |
| POST | `/suspicion/generate` | 生成疑点报告 |

### 11.6 模板 + Agent + 媒体

| Method | Path | 说明 |
|--------|------|------|
| GET | `/templates` | 模板列表 |
| GET/POST/PUT/DELETE | `/agents` | Agent CRUD |
| POST | `/agents/{id}/run` | Agent 执行 |
| POST | `/media/transcribe` | 语音转文字 |
| POST | `/media/analyze-image` | 图像理解 |
| POST | `/media/ocr-image` | 图片 OCR |

### 11.7 对话记忆

| Method | Path | 说明 |
|--------|------|------|
| GET | `/conversations` | 项目对话列表 |
| POST | `/conversations` | 记录新对话 |

---

## 12. 数据流与关联

### 12.1 溯源链路

```
疑点报告 (project_suspicions)
  └→ evidence_chain[{source_type, source_ref, page, excerpt, trace_anchor}]
      └→ audit_document_traces (file_name, minio_path, page_number, position_anchor)
          └→ MinIO: audit-project-{id}/raw/{file_id}/{filename}
```

### 12.2 数据流向

```
文件上传
  → MinIO (原始文件 + OCR MD 同桶存储)
  → audit_document_traces (溯源锚点)
  → OntoSKU 字段抽取
  → data_contracts/finance/legal_docs/registers/credentials/general
  → project_suspicions (疑点报告)
```

### 12.3 知识关联

```
audit_violations
  └→ audititem_id → sys_audititem (分类树)
      └→ sys_audititem_qualitative → law_id → sys_core_law_allaudit
      └→ sys_audititem_punish → law_id → sys_core_law_allaudit
          └→ tools_regulation_relation (superior/subordinate/related)
          └→ tools_clause_relation (条款分析)
```

### 12.4 前端组件复用

| 组件 | 使用页面 | 功能 |
|------|---------|------|
| App Framework | 全部13页 | 导航/侧边栏/Toast/认证/项目上下文 |
| API Client | 全部13页 | 统一请求/Token/错误处理 |
| LawSelector | 智能分析③/知识工坊/法规问答 | 法规搜索→关系树→条款浏览→确认 |
| FileUploader | 智能分析④/资料工坊 | 拖拽上传→OCR进度→结果展示 |
| ExpressionEngine | 智能分析⑤/数据工坊 | 伪SQL→表达式树→扫描结果 |
| TraceAnchor | 智能分析/数据工坊/资料工坊 | 溯源定位→原始文档页码 |
| AgentOrchestrator | 智能分析/设置 | 6/7 Agent 编排状态可视化 |

---

## 13. API 接口服务设计

### 13.1 ApiClient 核心类

```javascript
// 文件: js/api.js
class ApiClient {
  constructor(baseUrl)              // baseUrl 默认 window.location.origin
  async _req(method, path, options) // 统一请求: 自动 Token + JSON 序列化
  _get(path, params)                // GET 请求 + QueryString 构建
  _post(path, data)                 // POST 请求
  _put(path, data)                  // PUT 请求
  _delete(path)                     // DELETE 请求
  resource(name, basePath, methods) // 工厂方法: 批量生成 CRUD 方法
}

// 全局实例
const API = new ApiClient()

// Token 获取优先级
// 1. URL 参数 ?token=xxx
// 2. sessionStorage.getItem('opensquilla_token')
// 3. localStorage.getItem('aw_token')
```

### 13.2 资源注册表 (Resource Registry)

```javascript
// 项目管理 → 自动生成 list/get/create/update/delete
API.projects = API.resource('projects', '/api/audit/projects')

// 模板 → 只读
API.templates = API.resource('templates', '/api/audit/templates', ['list'])

// Agent CRUD
API.agents = {
  list()    → GET    /api/audit/agents
  create(d) → POST   /api/audit/agents
  update(id,d) → PUT /api/audit/agents/{id}
  delete(id) → DELETE /api/audit/agents/{id}
}
```

### 13.3 服务分组

| 服务组 | 调用方式 | 端点 | 数据流向 |
|--------|---------|------|---------|
| `API.projects` | resource() | `/api/audit/projects` | MySQL tt.audit_projects |
| `API.files` | 手工方法 | `/api/audit/projects/{id}/upload` | MinIO → OCR → OntoSKU → MySQL |
| `API.data` | 手工方法 | `/api/audit/data/{table}/rows` | MySQL tt.data_* |
| `API.knowledge` | 手工方法 | `/api/audit/knowledge/*` | MySQL audit_law.* |
| `API.expression` | 手工方法 | `/api/audit/expression/execute` | 伪SQL → AST → 行扫描 |
| `API.suspicion` | 手工方法 | `/api/audit/suspicion/generate` | 分析结果 → MySQL |
| `API.chat` | 手工方法 | `/api/chat` | OpenSquilla Agent Runtime |
| `API.analysis` | 手工方法 | `/api/audit/analysis` | Agent 编排 |
| `API.agents` | 手工方法 | `/api/audit/agents` | MySQL tt.audit_agents |
| `API.templates` | resource() | `/api/audit/templates` | MySQL tt.audit_templates |

### 13.4 后端路由注册 (Python)

```python
# 文件: src/opensquilla/audit/routes.py
def create_audit_routes(config) -> list[Route]:
    return [
        # 项目 (4 endpoints)
        Route("/api/audit/projects", api_list_projects, methods=["GET"]),
        Route("/api/audit/projects", api_create_project, methods=["POST"]),
        # ... 30+ routes total
    ]

# 在 gateway/app.py 中注册:
from opensquilla.audit.routes import create_audit_routes
routes.extend(create_audit_routes(config))
```

### 13.5 数据库访问层

```python
# 文件: src/opensquilla/audit/db.py
def query(sql, params, database="tt") -> list[dict]    # SELECT → dict list
def query_one(sql, params, database="tt") -> dict|None # SELECT → single
def execute(sql, params, database="tt") -> int          # INSERT/UPDATE/DELETE → rowcount
def insert(sql, params, database="tt") -> int           # INSERT → lastrowid
```

**连接配置** (fallback 默认值):
```python
_db_config = {
    "host": "192.168.3.164", "port": 3306,
    "user": "root", "password": "123456",
    "database": "tt", "charset": "utf8mb4",
}
```

### 13.6 JSON 序列化

```python
class _AuditEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)): return obj.isoformat()
        if isinstance(obj, Decimal): return float(obj)
        if isinstance(obj, bytes): return obj.hex()[:20] + '...'
        return super().default(obj)

class _AuditJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, cls=_AuditEncoder, ensure_ascii=False).encode("utf-8")
```

---

## 14. 交互时序图

### 14.1 项目创建流程

```
用户                 前端                  网关                  MySQL           MinIO
 │                   │                     │                     │               │
 │  填写名称+期间     │                     │                     │               │
 │──────────────────>│                     │                     │               │
 │                   │  POST /api/audit/projects                │               │
 │                   │────────────────────>│                     │               │
 │                   │                     │  INSERT audit_projects              │
 │                   │                     │────────────────────>│               │
 │                   │                     │                     │  make_bucket()│
 │                   │                     │──────────────────────────────────>│
 │                   │                     │                     │  bucket created│
 │                   │                     │<──────────────────────────────────│
 │                   │  {id, name, bucket} │                     │               │
 │                   │<────────────────────│                     │               │
 │  显示创建成功      │                     │                     │               │
 │<──────────────────│                     │                     │               │
```

### 14.2 文件上传 + OCR 流程

```
用户          前端              网关                  MinIO         MinerU        OntoSKU        MySQL
 │            │                  │                     │             │             │              │
 │ 拖拽文件   │                  │                     │             │             │              │
 │──────────>│                  │                     │             │             │              │
 │            │ POST upload      │                     │             │             │              │
 │            │────────────────>│                     │             │             │              │
 │            │                  │  put_object(raw)    │             │             │              │
 │            │                  │────────────────────>│             │             │              │
 │            │                  │  POST /parse (OCR)  │             │             │              │
 │            │                  │─────────────────────────────────>│             │              │
 │            │                  │  {markdown, pages}  │             │             │              │
 │            │                  │<─────────────────────────────────│             │              │
 │            │                  │  put_object(md)     │             │             │              │
 │            │                  │────────────────────>│             │             │              │
 │            │                  │  POST /sku/extract  │             │             │              │
 │            │                  │──────────────────────────────────────────────>│              │
 │            │                  │  {fields}           │             │             │              │
 │            │                  │<──────────────────────────────────────────────│              │
 │            │                  │  INSERT trace       │             │             │              │
 │            │                  │─────────────────────────────────────────────────────────────>│
 │            │                  │  INSERT data_*      │             │             │              │
 │            │                  │─────────────────────────────────────────────────────────────>│
 │            │  {trace_id, status}                    │             │             │              │
 │            │<────────────────│                     │             │             │              │
 │ 显示完成   │                  │                     │             │             │              │
 │<──────────│                  │                     │             │             │              │
```

### 14.3 智能分析 Agent 编排流程

```
用户          前端           网关           Agent1-3(并行)    Agent5-6(串行)    Agent7(终审)
 │            │               │                │                │                │
 │ 输入意图   │               │                │                │                │
 │──────────>│               │                │                │                │
 │            │ POST /analysis│               │                │                │
 │            │──────────────>│               │                │                │
 │            │               │ IntentAnalyzer│                │                │
 │            │               │──────────────>│                │                │
 │            │               │ {intent_json} │                │                │
 │            │               │<──────────────│                │                │
 │            │               │ ┌─ViolationMatcher             │                │
 │            │               │ ├─DataAdvisor   (并行)         │                │
 │            │               │ └─RegulationAdvisor            │                │
 │            │               │──────────────>│                │                │
 │            │               │ {results}     │                │                │
 │            │               │<──────────────│                │                │
 │            │ Agent推荐结果  │               │                │                │
 │            │<──────────────│               │                │                │
 │            │               │               │                │                │
 │ ⏸ 人工确认 │               │               │                │                │
 │──────────>│               │               │                │                │
 │            │ POST /confirm │               │                │                │
 │            │──────────────>│               │                │                │
 │            │               │ AuditAnalyzer │                │                │
 │            │               │──────────────────────────────>│                │
 │            │               │ SuspiciousPointGen            │                │
 │            │               │──────────────────────────────>│                │
 │            │               │ FinalReviewer                 │                │
 │            │               │──────────────────────────────────────────────>│
 │            │               │ [通过/修正/驳回]                              │
 │            │               │<──────────────────────────────────────────────│
 │            │ 疑点报告+终审 │               │                │                │
 │            │<──────────────│               │                │                │
 │ 查看报告   │               │               │                │                │
 │<──────────│               │               │                │                │
```

### 14.4 智能问数流程

```
用户              前端               网关              LLM (8765)          MySQL
 │                │                   │                   │                  │
 │ "查询所有询价   │                   │                   │                  │
 │  且金额>100万   │                   │                   │                  │
 │  的合同"       │                   │                   │                  │
 │───────────────>│                   │                   │                  │
 │                │ POST /data/query  │                   │                  │
 │                │──────────────────>│                   │                  │
 │                │                   │ 获取表schema       │                  │
 │                │                   │──────────────────────────────────────>│
 │                │                   │ {table: [columns]}│                  │
 │                │                   │<──────────────────────────────────────│
 │                │                   │ NL→SQL (LLM)      │                  │
 │                │                   │──────────────────>│                  │
 │                │                   │ 伪SQL表达式        │                  │
 │                │                   │<──────────────────│                  │
 │                │ {pseudo_sql}      │                   │                  │
 │                │<──────────────────│                   │                  │
 │ 显示表达式      │                   │                   │                  │
 │<───────────────│                   │                   │                  │
 │ [确认并执行]    │                   │                   │                  │
 │───────────────>│                   │                   │                  │
 │                │ POST /expression/execute              │                  │
 │                │──────────────────>│                   │                  │
 │                │                   │ SELECT * FROM     │                  │
 │                │                   │ data_contracts    │                  │
 │                │                   │──────────────────────────────────────>│
 │                │                   │ rows              │                  │
 │                │                   │<──────────────────────────────────────│
 │                │                   │ AST解析→逐行求值   │                  │
 │                │ {total,hits,matches}                  │                  │
 │                │<──────────────────│                   │                  │
 │ 命中结果表格    │                   │                   │                  │
 │<───────────────│                   │                   │                  │
```

### 14.5 项目记忆隔离流程

```
用户选择项目A         用户切换到项目B
 │                    │
 │ setProject(A)      │ setProject(B)
 │────> localStorage  │────> localStorage
 │                    │
 │ 生成 session_A     │ 生成 session_B
 │ = aw_A_timestamp   │ = aw_B_timestamp
 │                    │
 │ API.chat.send()    │ API.chat.send()
 │ → session_id=A     │ → session_id=B
 │                    │
 │ OpenSquilla Memory │ OpenSquilla Memory
 │ scope = session_A  │ scope = session_B
 │                    │
 │ 对话记录:          │ 对话记录:
 │ audit_conversations│ audit_conversations
 │ WHERE project_id=A │ WHERE project_id=B
```

---

## 15. 错误处理

### 15.1 前端错误分级

| 级别 | 处理方式 | 示例 |
|------|---------|------|
| **Fatal** | 页面不可用，显示错误页 | API 客户端初始化失败 |
| **Error** | Toast 通知 + 降级展示 | API 返回 500 → "加载失败: {msg}" |
| **Warning** | Toast 通知 + 继续运行 | 非关键 API 超时 → 使用缓存数据 |
| **Info** | 静默降级 | Lab 功能未开启 → 隐藏面板 |

### 15.2 API 错误处理矩阵

```
HTTP Status    ApiError Code        前端行为
───────        ────────────         ────────
200            -                    正常返回 data
400            BAD_REQUEST          Toast: "请求参数错误"
401            UNAUTHORIZED         Token 失效 → 提示刷新页面
403            FORBIDDEN            系统Agent不可删除
404            NOT_FOUND            Toast: "资源不存在"
500            INTERNAL_ERROR       Toast: "服务异常: {msg}" + 控制台日志
507            UPLOAD_STORE_FULL    Toast: "上传队列已满，请稍后重试"
Network Error  -                    Toast: "网络连接失败，请检查服务器状态"
```

### 15.3 后端异常处理

```python
# routes.py 中的 try/except 模式

# 数据库操作: query/execute 自带 commit/rollback
def db_cursor(database="tt"):
    conn = get_conn(database)
    try:
        yield conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# OCR 失败: 原始文件已保存，OCR 降级
try:
    ocr_result = await parse_document(file_bytes, file_name)
except Exception:
    pass  # OCR 失败不阻塞，文件已存 MinIO

# OntoSKU 失败: OCR 结果已保存，SKU 降级
try:
    sku_result = await extract_metadata(ocr_content)
    _insert_to_data_workshop(pid, trace_id, sku_result)
except Exception:
    pass  # SKU 失败不阻塞

# MinIO 不可用: 数据库记录仍创建
try:
    ensure_project_bucket(pid)
except Exception:
    pass  # MinIO 不可用时仍创建项目
```

### 15.4 文件上传边界状态

| 状态 | 触发条件 | 处理方式 |
|------|---------|---------|
| 文件过大 | > Nginx/网关限制 | HTTP 413 → "文件过大" |
| 不支持的格式 | .exe/.dll 等 | 接受但标记为 opaque, 不 OCR |
| 空文件 | size=0 | HTTP 400 → "文件为空" |
| 并发上传 | 多文件同时 | 串行处理，逐个 OCR |
| OCR 超时 | MinerU > 300s | 返回 uploaded (无 OCR)，后台继续 |
| OCR 失败 | MinerU 500 | 返回 uploaded (无 OCR)，用户可手动重试 |
| 桶不存在 | MinIO 连接失败 | 自动 create bucket |
| 同名文件 | 相同 filename | 不同 file_id，版本共存 |
| 断点续传 | 网络中断 | 不支持（后续版本） |

---

## 16. 边界状态处理

### 16.1 空数据状态

| 页面 | 空状态 | UI 表现 |
|------|--------|---------|
| 项目列表 | 0 个项目 | 图标 + "暂无审计项目" + "创建第一个项目" 按钮 |
| 数据工坊 | 表行数为 0 | 📥 图标 + "该表暂无数据" + 提示上传资料 |
| 知识工坊 | 0 条违规 | "暂无违规行为数据" + 提示导入 |
| 知识工坊 | 0 条法规 | "未找到匹配法规" |
| 模板列表 | 0 模板 | "暂无模板" |
| Agent 列表 | 0 Agent | "暂无Agent" |
| 对话列表 | 0 对话 | 空数组 `[]` |
| 智能问数 | LLM 返回空 | "AI未能生成有效查询，请尝试更具体的描述" |
| 表达式执行 | 0 条命中 | "表达式执行完成，0条命中" |

### 16.2 并发与防抖

| 场景 | 策略 |
|------|------|
| 搜索框输入 | 300ms debounce |
| Agent 并行执行 | Promise.all() |
| 文件批量上传 | for...of 串行 (避免 OCR 过载) |
| 页面 Tab 切换 | 每次切换时重新 fetch (简单策略) |
| Toast 通知 | 独立叠加, 3.5s 自动移除 |
| 项目切换 | 立即更新 localStorage + 导航栏 |

### 16.3 localStorage Key 规范

```
aw_proj            - 当前项目 {id, name}
aw_token           - 认证 Token (明文, 内网环境)
aw_session_{pid}   - 项目级对话 session_id
aw_lab_{ws}_{feat} - 实验室功能开关 (如 aw_lab_dw_nl)
aw_lab_enabled     - 实验室总开关
aw_bg_tasks        - 后台任务队列 JSON[]
aw_sidebar_collapsed - 侧边栏折叠状态
aw_guide_shown     - 首次引导已展示
```

### 16.4 跨浏览器状态

| 存储 | 内容 | 生命周期 |
|------|------|---------|
| `localStorage` | 项目/Token/Lab开关/任务 | 持久 |
| `sessionStorage` | 项目session_id | 会话 |
| `window.location` | Token（初始URL） | 页面加载时提取 |

### 16.5 性能指标

| 操作 | 目标 | 降级 |
|------|------|------|
| 页面首次加载 | < 2s | - |
| API 响应 (读) | < 500ms | 缓存数据 |
| API 响应 (写) | < 1s | 异步写入 |
| OCR 解析 | < 60s (小文件) | 超时不阻塞 |
| Agent 执行 | < 30s | 超时返回 partial |
| 智能问数 LLM | < 10s | 返回空提示用户重试 |
| 表达式执行 (1000行) | < 2s | 分页处理 |

### 16.6 安全性

| 层 | 措施 |
|----|------|
| 传输 | 内网 HTTP (生产建议 HTTPS) |
| 认证 | Token Bearer, 同源部署免跨域 |
| 沙箱 | TrustedHost 模式, 文件操作审计 |
| 数据库 | pymysql 参数化查询 (防注入) |
| XSS | 内联 HTML 通过 innerHTML 时做 escape |
| CSRF | 同源部署 → 浏览器自动防护 |
| MinIO | Access Key + Secret Key 认证 |
| 项目隔离 | 所有 API 查询带 project_id 过滤 |

---

## 17. 文件级代码映射

### 17.1 前端文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `index.html` | 281 | 首页仪表盘 + Guide引导 |
| `projects.html` | 1033 | 项目CRUD + 详情4Tab |
| `analysis.html` | 243 | 智能分析7步向导 |
| `knowledge.html` | 105 | 知识工坊三库联动 |
| `dataworkshop.html` | 280 | 数据工坊6表 + 智能问数 |
| `docworkshop.html` | 324 | 资料工坊文件管理 |
| `lawqa.html` | 81 | 法规问答 |
| `qualification.html` | 81 | 审计定性 |
| `documents.html` | 102 | 文书生成 |
| `review.html` | 76 | 审理复核 |
| `toolbox.html` | 122 | 工具箱 |
| `workspace.html` | 190 | 我的空间 |
| `settings.html` | ~450 | 系统设置 (11面板) |
| `js/api.js` | 210 | API Client + 服务注册 |
| `js/app.js` | 390 | 全局框架 |
| `js/knowledge.js` | 120 | 知识工坊 API 驱动 |
| `js/portal.js` | 357 | 首页交互 |
| `js/analysis-wiz.js` | 1342 | 智能分析聊天引擎 |
| `js/analysis.js` | 574 | 智能分析7步逻辑 |
| `css/theme.css` | 365 | 设计系统 |

### 17.2 后端文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/opensquilla/audit/__init__.py` | 25 | 模块入口 |
| `src/opensquilla/audit/db.py` | 75 | MySQL 连接层 |
| `src/opensquilla/audit/routes.py` | ~500 | 30+ API 端点 |
| `src/opensquilla/audit/agents/base.py` | 65 | Agent 基类 |
| `src/opensquilla/audit/agents/intent_analyzer.py` | 35 | Agent 1 |
| `src/opensquilla/audit/agents/violation_matcher.py` | 40 | Agent 2 |
| `src/opensquilla/audit/agents/data_advisor.py` | 45 | Agent 3 |
| `src/opensquilla/audit/agents/regulation_advisor.py` | 55 | Agent 4 |
| `src/opensquilla/audit/agents/audit_analyzer.py` | 45 | Agent 5 |
| `src/opensquilla/audit/agents/suspicion_generator.py` | 50 | Agent 6 |
| `src/opensquilla/audit/services/minio_service.py` | 60 | MinIO 桶管理 |
| `src/opensquilla/audit/services/ocr_service.py` | 25 | OCR 调用 |
| `src/opensquilla/audit/services/ontosku_service.py` | 40 | SKU 抽取 |
| `src/opensquilla/audit/services/trace_service.py` | 40 | 溯源锚点 |
| `src/opensquilla/audit/services/expression_engine.py` | 110 | 表达式引擎 |
| `src/opensquilla/audit/services/nl2sql_service.py` | 40 | NL→SQL |
| `src/opensquilla/audit/services/media_service.py` | 80 | 语音/图像识别 |
| `src/opensquilla/gateway/app.py` | +3行 | 注册审计路由 |
| `src/opensquilla/gateway/control_ui.py` | +1行 | 关闭30天缓存 |

