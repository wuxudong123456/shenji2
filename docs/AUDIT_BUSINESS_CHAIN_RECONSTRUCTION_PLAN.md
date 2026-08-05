# 审计工坊真实业务链路补齐与改造规划方案

> 文档用途：领导需求确认 / 架构评审 / 后续实施立项  
> 分析基线：当前仓库前端、Flask 后端、MySQL DDL、Agent/LangGraph、MinIO/OCR 集成代码  
> 方案性质：存量系统增量改造，不重新设计，不改变现有前端视觉与交互框架，不实施代码变更  
> 编制日期：2026-08-04

## 一、领导决策摘要

当前系统已经具备“项目、资料、OCR、结构化数据、违规模型、法规、分析、疑点、文书”的主要模块，但这些模块尚未按真实审计顺序形成受控主链路。核心问题不是缺少页面或算法，而是：项目创建过早触发资源创建、阶段准入未被后端强制、资料目录缺少年份和类型语义、溯源停留在文档级、数据分析仍存在绕过项目结构化数据的入口、AI聊天承担了过多页面状态和流程控制。

本次建议只补齐六项主能力：

1. 将项目创建校正为“立项基础信息 → 对象与范围 → 审计事项 → 资料空间”的四阶段受控流程。
2. 保留现有页面和字段，以后端阶段校验、分阶段保存和状态计算替代一次性生成。
3. 在现有项目 bucket 和 `minio_bucket` 字段基础上增加年度、项目和资料类型的逻辑目录，不重构存储平台。
4. 将现有 `audit_document_traces.position_anchor` 中的 chunks 能力提升为可查询的字段级、数据行级证据链。
5. 强制智能分析只读取当前 `project_id` 下的数据工坊结构化表，原始文件只用于查看、复核和重新解析。
6. 将智能分析聊天收敛为每步一条固定 ID 的正式总结，页面操作只记录状态或审计日志，不进入 LLM 上下文。

建议领导确认的总体结论：前端布局基本可复用，改造重点应放在后端流程编排、数据关系、溯源模型和 AI 上下文契约；不建议重做 UI，也不建议推倒现有 Flask、LangGraph、MinIO 和六类结构化表。

## 二、范围与约束

### 2.1 本次纳入范围

- 审计项目四阶段创建与准入控制。
- 资料工坊年度、项目、资料类型隔离。
- 文件上传、OCR、切片、结构化抽取和来源追踪。
- 资料工坊到数据工坊的数据落库链路。
- 七步智能分析的步骤总结、上下文和审计日志边界。
- 违规方法推荐、法规依据推荐及其项目上下文和溯源约束。
- 现有前端、API、数据库和 Agent 工作流的适配规划。

### 2.2 明确不做

- 不新增与真实业务主链路无关的功能。
- 不改变 `projects.html`、`docworkshop.html`、`dataworkshop.html`、`analysis.html` 的现有视觉结构和主要交互方式。
- 不重写 `frontend/js/app.js` 导航框架。
- 不改动已有字段含义；同一业务含义优先映射到现有字段。
- 不让前端适配新的后端自定义格式；后端 DTO 继续兼容 `frontend/js/api.js`。
- 不让 LLM 脱离当前项目、已确认数据和来源证据自由生成。
- 本方案阶段不开发、不迁移数据、不执行 DDL。

## 三、当前实现情况

### 3.1 总体能力判断

| 领域 | 当前结论 | 现有依据 | 满足度 |
|---|---|---|---:|
| 项目创建页面 | 已有四个 Tab，基础信息、对象范围、事项均已呈现 | `frontend/projects.html` | 部分支持 |
| 项目后端 | 已有项目 CRUD、事项读取/保存、AI 拆分事项 | `backend/routes/audit_routes.py` | 部分支持 |
| 项目字段 | 项目名称、编号、单位、类型、方式、期间、层级、目标、范围、金额已落库 | `audit_projects` 及迁移脚本 | 大部分支持 |
| 阶段顺序 | 前端可任意切换，保存时一次提交多阶段字段；后端无阶段准入 | `Proj.tab()`、`Proj.save()` | 不支持 |
| 资料空间 | 创建项目时立即创建 `audit-project-{project_id}` bucket | 项目 POST 接口 | 与目标不符 |
| 年度隔离 | 项目列表有年度折叠展示，但后端无明确年度查询和目录契约 | `projects.html` 静态年度区 | 展示有、业务无 |
| 文件隔离 | 每项目独立 bucket，上传路径含 `project_id/raw/file_id` | 上传接口、MinIO 客户端 | 已有基础 |
| OCR/解析 | OntoSKU 优先、LiteParse/本地抽取降级，异步任务执行 | `task_worker.py`、`ocr_client.py` | 已支持 |
| 文档切片 | OntoSKU 返回 chunks，包含 `page_nums`/坐标的设计入口 | `ontosku_client.py`、`position_anchor` | 部分支持 |
| 结构化入库 | 文档分类后写入六类 `data_*` 表，并关联 `project_id`、`document_trace_id` | `task_worker.py`、`schema.sql` | 已支持基础链路 |
| 数据分析 | 表达式引擎支持按 `project_id` 扫描结构化表 | `expression_engine`、分析路由 | 基本支持 |
| AI 项目上下文 | 分析任务会读取部分项目字段注入 LangGraph | `/api/audit/analysis` | 部分支持 |
| Agent 溯源 | Agent 记录 `trace_id`、知识来源和工具调用记录 | `BaseAgent`、`audit_agent_traces` | 有框架、未闭环 |
| 对话控制 | 前端大量步骤状态、进度和提示通过 `say()` 追加到聊天 | `analysis-wiz.js` | 与目标不符 |
| 上下文洁净度 | localStorage 保存并恢复 `chatHTML`、`rightPanelHTML` | `saveProgress()` | 存在污染风险 |

### 3.2 前端是否支持目标业务流程

结论：页面容器和操作入口基本齐备，但行为层不符合严格分阶段要求。

已支持：

- `projects.html` 已有“审计立项、审计对象和范围、审计事项”三个现成 Tab。
- 审计事项支持 AI 拆分、人工新增以及页面内编辑/移除入口。
- 项目页已有年度分组视觉结构。
- 资料工坊已有文件上传、OCR、字段、溯源三个查看区域。
- 智能分析已有七步页面结构和 Toast 能力。

主要不符合项：

- “AI综合分析”文案和接口会一次提取基础信息、对象范围和审计事项，违反立项阶段只创建基础信息的要求。
- Tab 可任意跳转，未根据前序阶段完成状态禁用或拦截。
- 页面顶部只有统一“保存项目”，`Proj.save()` 会同时提交 `audited_unit`、`objective`、`scope` 等多阶段字段，并在同一保存回调中保存事项。
- `splitItems()` 只校验项目名称，没有强制校验项目已落库、对象和范围已确认。
- 年度列表目前主要是静态展示，不能证明不同年度数据由后端隔离。
- 智能分析中的勾选、确认、上传、扫描、恢复等动作会追加聊天消息；刷新恢复还会直接保存完整聊天 HTML。
- 若干“溯源”链接仍是占位展示或 Toast，并非统一的证据查询结果。

### 3.3 后端接口是否符合目标流程

结论：接口覆盖面较全，但缺少阶段命令、前置校验和资源延迟创建。

| 现有接口/行为 | 当前作用 | 与目标的差距 |
|---|---|---|
| `POST /api/audit/projects` | 创建项目并立即创建 bucket | 应只保存立项基础信息，bucket 应延迟到第四阶段 |
| `PUT /api/audit/projects/{id}` | 任意更新允许字段 | 应按阶段白名单校验，同时保持原接口兼容 |
| `POST /api/audit/projects/extract-info` | 同时提取基础、范围和事项 | 应限定为当前阶段；立项阶段不得产生对象、范围和事项 |
| `POST /api/audit/projects/split-audit-items` | 按表单文本生成事项 | 缺少项目落库检查、对象/范围完整性、历史案例与知识库检索、来源引用 |
| `PUT /api/audit/projects/{id}/items` | 全量替换事项 | 能满足增删改后的最终保存，但需要版本/操作审计和状态校验 |
| `POST /api/audit/projects/{id}/upload` | 上传、建 trace、异步 OCR | 缺少“项目空间已创建”准入和年度/类型目录参数 |
| `GET /api/audit/documents/{id}/trace` | 返回单条文档 trace | 尚不能按字段、数据行、AI结论查询证据链 |
| `GET /api/audit/data/{table}/rows` | 可按项目筛选，也允许空项目查询全局 | 项目智能分析场景必须拒绝空 `project_id` |
| `POST /api/audit/analysis` | 启动意图、违规、资料、法规推荐 | 项目上下文只注入类型、名称、期间、层级、单位，未完整注入目标、范围、事项和资料证据 |
| `POST /api/chat` | 通用对话 | 未接收结构化任务状态；记录表只存标题，不支持固定步骤总结 |

### 3.4 数据库关系是否支持

结论：主体关系可复用，阶段关系和细粒度证据关系不足。

现有可复用关系：

```text
audit_projects.id
  ├─ audit_items.project_id
  ├─ audit_document_traces.project_id
  │    └─ data_*.document_trace_id
  ├─ audit_analysis_tasks.project_id
  ├─ audit_agent_traces.project_id
  └─ project_suspicions.project_id
```

优势：

- 六类结构化表均有 `project_id`，具备项目级分析条件。
- 每条结构化记录均预留 `document_trace_id`，具备从数据行回到文档的基础。
- `project_suspicions.evidence_chain` 和 `audit_agent_traces` 已预留 AI 推理溯源入口。
- `audit_items` 已与项目关联，无需重新设计事项主体。

不足：

- `audit_projects` 无显式“当前创建阶段/各阶段确认时间”；仅凭 `status` 不能区分四个创建阶段。
- 审计年度没有独立字段。可由现有审计期间或项目日期派生，但必须统一规则，不能由前端各自猜测。
- 页面已有的审计对象细分字段中，`target_unit`、`extend_unit`、审计重点、报告文号、开始日期、进点日期并未完整进入当前保存 DTO。
- `audit_document_traces` 是一文档一条记录，`page_number` 只能表示单页，实际 chunks 被整体序列化在 `position_anchor`，不利于按页、段、字段查询。
- 六类结构化表的 `raw_text` 是整段文本，不能精确表达“字段值来自哪个 chunk”。
- 法规引用和历史案例引用没有统一挂接到“推荐事项/推荐法规/分析结论”的证据明细记录。
- 现有表多为逻辑索引，DDL 未体现项目表外键约束；隔离主要依赖应用层条件。

### 3.5 AI 上下文是否存在污染

结论：存在明显污染风险，但可在不改 UI 的前提下治理。

具体表现：

- 前端保存完整 `chatHTML` 和 `rightPanelHTML`，将展示层内容与业务状态混在一起。
- 步骤推进主要依赖用户说“确认、上传、比对、疑点、文书”等自然语言关键字，而不是结构化命令。
- 勾选结果、加载过程、成功/失败提示、恢复提示、背景展开、记忆压缩等均可能追加为 AI 消息。
- `/api/chat` 只接收 `message/session_id`，没有 `project_id`、当前步骤、已确认 ID、证据引用等必需上下文。
- Agent 输入虽使用 LangGraph 状态，但项目上下文注入不完整，且存在页面本地 memory 与数据库项目记录双来源。
- 分析工作流当前以 1-6 步为主，而产品页面包含第 7 步文书，状态模型和展示步骤尚未统一。

### 3.6 溯源链路是否完整

结论：已具备技术组件，但未达到“任何 AI 输出均可追溯”的业务验收标准。

现有链路：

```text
文件 → MinIO路径 → audit_document_traces
     → OCR全文 + chunks JSON
     → data_* 行(document_trace_id)
     → 表达式扫描结果
     → Agent trace / suspicion evidence_chain
```

主要断点：

1. 文件 trace 与 OntoSKU `document_id/job_id` 没有完整、稳定地存入专用字段。
2. chunks 只存 JSON 整体，缺少可查询的 `chunk_id/page/bbox/text` 明细。
3. 结构化字段只有字段值，没有字段到 chunk 的逐字段映射。
4. 分析命中行可回到 `document_trace_id`，但未稳定携带命中字段和 source chunk。
5. 法规推荐虽然记录 law 来源，但“法规名称—条款—原文—知识库记录—推荐原因”未形成统一引用对象。
6. 事项推荐当前由 LLM 直接输出 `legal_bases` 等文本，未绑定历史案例 ID、违规 ID、法规 ID、资料 chunk ID。
7. 前端多个溯源按钮仍为静态链接、假文案或 Toast，缺少统一 trace API 响应。

## 四、OntoSKU 参考能力核查

2026-08-04 对 `http://192.168.3.189:5005` 进行了只读 API 核查。服务返回 OntoSKU 1.0.0.1，OpenAPI 显示以下能力：

- 解析任务支持 `parse_track=chunk` 和受控的 `page_memory` 模式。
- 解析参数支持 OCR、智能标题、文本/图片/表格摘要和 SKU profile。
- 文档 chunks 可按文档分页查询，并按 `text/image/table/page` 类型过滤。
- 可按 `document_id + chunk_id` 查询单个切片。
- 页面图像接口使用 1 起始页码，并与 chunk 元数据中的 `page_nums` 对齐。
- 检索接口返回 `evidence_text`、`referenced_chunks`、`results` 和 `decision_trace`，明确将证据交给下游 Agent，而不是在检索层直接生成答案。
- 可下载原始上传文件和完整结果包；当前项目客户端已解析 `full.md`、`sku_results.json`、`chunks.json`。

对审计工坊的直接借鉴不是复制 OntoSKU 系统，而是复用其证据契约：`document_id → chunk_id → page_nums/bbox → original text/page image`。审计工坊需要在此基础上增加 `project_id`、结构化字段、数据行、违规模型、法规条款和 AI 结论的业务关联。

注意：本次完成了 API 契约核查；由于浏览器运行时缺少 Chromium，未完成其可视化页面逐项操作核验。页面高亮、坐标缩放和跨格式定位应列入后续联合验收，不在本方案中假定已完全满足。

## 五、目标业务架构

### 5.1 项目创建主链路

```text
阶段1 审计立项
  保存现有基础字段，项目状态=draft
       ↓ 后端校验基础字段
阶段2 审计对象和范围
  更新同一 project_id 的现有对象/范围字段
       ↓ 后端校验对象+范围
阶段3 审计事项
  AI基于项目上下文检索后推荐；人工增删改；保存 audit_items
       ↓ 后端校验至少一项已确认事项
阶段4 创建资料空间
  根据审计年度+project_id+项目名称创建存储空间
  项目状态=active
```

控制原则：

- 第一步 POST 只接受基础信息字段，不返回对象、范围和事项。
- 第二步只更新同一 `project_id`，不能另建项目。
- 第三步推荐接口必须从数据库加载项目，不接受前端用自由文本伪造完整上下文。
- 第四步必须幂等；重复确认不得创建第二个 bucket 或重复目录。
- 阶段状态由后端返回，前端只据此控制现有 Tab 和按钮状态。

### 5.2 字段复用与映射原则

| 业务信息 | 优先复用字段 | 处理建议 |
|---|---|---|
| 项目名称 | `audit_projects.name` | 保持不变 |
| 项目编号 | `project_code` | 保持不变 |
| 审计类型 | `audit_type` | 保持不变 |
| 审计方式 | `audit_method` | 保持不变 |
| 审计级别/单位层级 | `target_level` | 后端 DTO 兼容现有 `level` 别名 |
| 被审计单位 | `audited_unit` | 作为主要审计对象基础字段 |
| 审计目标 | `objective` | 保持不变 |
| 审计范围 | `scope` | 保持不变 |
| 审计期间 | `audit_period` | 保持不变 |
| 涉及金额 | `amount` | 保持不变 |
| 审计事项 | `audit_items` | 保持现表和现有 JSON 子字段 |
| 审计年度 | 从 `audit_period` 起始日期派生 | 暂不新增业务字段；后端统一计算 `audit_year` DTO |
| 立项单位 | 当前无明确持久化字段 | 领导确认其是否等同创建单位/creator；不允许自行混用 |
| 延伸审计单位 | 页面已有 `f-extend-unit`，DB无等价字段 | 建议仅做同名增量列，不能塞入其他含义字段 |
| 报告文号、开始/进点日期、审计重点 | 页面已有、保存链路缺失 | 作为“现有前端字段补持久化”单列评审，不改变 UI |

### 5.3 资料工坊逻辑结构

保留当前“每项目独立 bucket”的物理隔离优势，通过对象前缀实现领导要求的年度和资料类型视图：

```text
bucket: audit-project-{project_id}
└─ {audit_year}/{project_id}-{项目名称安全短名}/
   ├─ project-materials/               项目资料桶
   ├─ text/
   │  ├─ word/
   │  ├─ pdf/
   │  ├─ excel/
   │  └─ txt/
   ├─ audio/
   │  ├─ original/
   │  └─ transcript/
   └─ other/
```

说明：

- “年度”是资料工坊查询和对象前缀的一级逻辑维度。
- “项目”继续以 `project_id` 为唯一隔离键，项目名只用于展示和安全短名，不能作为关联主键。
- `minio_bucket` 字段继续存项目 bucket 名，不改变含义。
- 文件类型由后端 MIME、扩展名和解析结果判定，前端不承担目录规则。
- 上传 API 继续保持现有路径兼容，由后端把文件落入正确前缀。
- 不同项目跨 bucket 隔离；所有数据库查询强制 `project_id`；年度查询由后端派生年度后过滤。

## 六、端到端数据链路

### 6.1 资料处理链路

```text
上传请求(project_id)
  → 校验项目active且空间已创建
  → 文件MD5去重（同项目内）
  → MinIO分类路径
  → audit_document_traces文档主记录
  → 异步OCR/OntoSKU任务
  → Markdown + chunks + 页图引用
  → 文档分类 + 模板匹配
  → 字段抽取（每字段绑定chunk）
  → 写入对应data_*表
  → 建立数据行/字段证据引用
  → 文件状态=可分析
```

### 6.2 智能分析读取链路

```text
用户进入项目分析
  → project_id
  → 读取项目基础信息、对象范围、已确认事项ID
  → 读取已确认违规ID/法规ID
  → 查询当前项目data_*结构化数据
  → 表达式/阈值/语义函数分析
  → 生成命中数据行ID和证据引用ID
  → Agent形成疑点与步骤结论
  → 每条结论绑定来源集合
```

禁止路径：

- Agent 直接下载原始文件全文进行项目分析。
- 不带 `project_id` 扫描所有项目结构化数据。
- 将页面 HTML、全部聊天记录或操作日志作为 LLM 输入。
- 法规推荐只返回法规标题而没有 law/clause/source 引用。
- 事项推荐只返回自然语言，不保存推荐依据 ID。

## 七、统一溯源设计

### 7.1 最小证据单元

每个可展示的 AI 结论至少返回：

```json
{
  "result_id": "业务结果ID",
  "result_type": "audit_item|law_recommendation|analysis_hit|suspicion|document",
  "project_id": "项目ID",
  "sources": [
    {
      "source_type": "document_chunk|data_row|law_clause|violation|case",
      "source_id": "来源记录ID",
      "document_id": "文档ID（如适用）",
      "file_name": "来源文件（如适用）",
      "page_number": 3,
      "bbox": [0, 0, 100, 30],
      "quote": "用于支撑结论的原文片段",
      "parsed_at": "解析时间",
      "relation": "supports|contradicts|derived_from"
    }
  ]
}
```

这是后端响应契约，不要求改变现有 UI；现有“📍溯源”入口可继续使用，只需改为读取真实数据。

### 7.2 溯源层级

| 层级 | 必备标识 | 能回答的问题 |
|---|---|---|
| 原始文件 | project_id、trace_id、minio_path、MD5 | 来自哪个项目和文件 |
| 文档切片 | chunk_id、page_nums、bbox、原文 | 文件的哪一页哪一段 |
| 结构化字段 | table、row_id、field_name、chunk_id | 这个字段值如何提取 |
| 规则与知识 | violation_id、law_id、clause_id、case_id | 使用了什么规则/法规/案例 |
| Agent执行 | agent_trace_id、input refs、tool calls | AI读取了哪些来源、做了什么调用 |
| 业务结论 | result_id、source refs | 事项、推荐、疑点、文书由哪些证据支撑 |

### 7.3 数据库调整建议

坚持“现有业务表不改字段定义、只做增量”的原则。

优先级 P0：

- 为 `audit_projects` 增加最小流程控制字段：`setup_stage`、`workspace_created_at`。若领导坚持零新增列，可由字段完整性计算阶段，但无法可靠记录确认时间，不建议。
- 为 `audit_document_traces` 增加 OntoSKU 技术标识：`external_document_id`、`external_job_id`、`parse_engine`、`parse_status`、`parsed_at`。现有字段保持不变。
- 新增 `audit_document_chunks`：一行一个 chunk，保存 `trace_id/project_id/chunk_id/chunk_type/page_numbers/bbox/text/section_path`。
- 新增 `audit_source_refs`：统一关联业务结果与 `document_chunk/data_row/law_clause/violation/case`，避免每个模块各存一种 evidence JSON。

优先级 P1：

- 新增 `audit_field_sources`：关联 `table_name/row_id/field_name` 与 chunk，支持字段级定位。
- 为步骤正式总结提供持久化表 `audit_step_summaries`，唯一键 `(analysis_task_id, step_no)`，保存固定 `message_id`、结构化总结和来源引用。
- 扩展事项推荐依据，建议新增关联表而非修改 `audit_items` 现有字段：事项 ID 关联 violation、law、case、document chunk。
- 对审计日志沿用现有日志体系，新增事件类型而非把日志写入对话表。

数据约束建议：

- 所有新增证据表必须有 `project_id` 联合索引。
- 所有数据工坊查询必须在服务层自动附加 `project_id`，不能信任 LLM 生成该条件。
- `document_trace_id`、`chunk_id`、法规条款 ID 和业务结果 ID 应使用真实引用，不以标题文本替代。
- 项目年度只由后端统一派生；无法从审计期间获得时，由现有日期字段或创建日期按已确认规则兜底并标记来源。

## 八、AI 上下文与聊天收敛方案

### 8.1 三类信息严格分离

| 信息类型 | 存放位置 | 是否进入聊天 | 是否进入 LLM |
|---|---|---:|---:|
| 勾选、取消、拖拽、加载、按钮状态 | 页面状态 | 否 | 否 |
| 成功、失败、已选择等临时反馈 | Toast/状态文字 | 否 | 否 |
| 详细操作记录 | 审计日志 | 否 | 默认否 |
| 当前步骤业务状态 | 分析任务 `step_data` | 否 | 是，结构化输入 |
| 每步正式结论 | `audit_step_summaries` | 是，每步一条 | 是，必要时作为上游结论 |
| 来源证据 | `audit_source_refs` | 通过溯源面板展示 | 是，只传引用和必要摘录 |

### 8.2 固定消息 ID 覆盖

七步固定消息 ID：

- `step-1-summary`：审计意图结论。
- `step-2-summary`：方法推荐结论。
- `step-3-summary`：法规确认结论。
- `step-4-summary`：资料准备结论。
- `step-5-summary`：数据比对结论。
- `step-6-summary`：疑点核实结论。
- `step-7-summary`：文书生成结论。

用户返回修改后，更新同一记录和同一 DOM 消息，不追加新消息。只有点击现有“确认并进入下一步”类动作时才生成或覆盖总结。

### 8.3 LLM 输入契约

```json
{
  "project_id": "...",
  "task_id": "...",
  "step": 3,
  "project_context": {
    "basic": {"现有项目字段": "值"},
    "target_scope": {"现有对象范围字段": "值"},
    "audit_item_ids": ["..."]
  },
  "confirmed_ids": {
    "violation_ids": ["..."],
    "law_ids": ["..."],
    "document_ids": ["..."]
  },
  "step_result": {},
  "source_refs": ["ref-..."]
}
```

服务端自行装配，不接受前端传完整聊天 HTML。`chatHTML` 只能用于旧版本兼容展示，完成迁移后不再作为业务状态来源。

## 九、方法推荐与法规依据规则

### 9.1 方法/违规推荐

推荐输入限定为：

- 当前项目现有基础字段。
- 审计类型。
- 审计目标。
- 审计对象。
- 已确认审计范围和事项。

执行顺序：先按项目类型和事项分类检索违规候选，再以目标、对象、范围做相关性排序，最后由 AI 解释推荐原因。LLM 不负责凭空创造违规模型，输出必须包含 `violation_id`、来源表、匹配字段和推荐理由。

### 9.2 审计事项推荐

只有后端确认前两阶段完成后才允许执行。数据源同时包括：

- 项目基本信息、对象、范围。
- `audit_violations` 和审计事项分类知识。
- 现有历史案例库。
- 审计知识库/法规库。

每个推荐事项必须返回：事项结构、推荐理由、关联 `violation_ids/law_ids/case_ids/document_chunk_ids`、数据源摘要。人工修改不删除原始推荐依据，保存“AI建议版本”和“人工确认版本”的差异日志。

### 9.3 法规推荐

法规推荐使用三路证据：

1. 项目基础信息和已确认事项。
2. 项目资料的结构化字段及相关 chunks。
3. 法规知识库和关系图。

每条结果至少包含 `law_id`、法规名称、效力/时效、`clause_id`、条款位置、引用原文、法规库来源、关联项目资料引用和推荐理由。若只有法规名称而无法定位条款，应标记“待人工核实”，不能作为已确认依据。

## 十、前后端改造方案

### 10.1 前端：保留 UI，只调整行为层

| 页面/脚本 | 保留 | 行为调整 |
|---|---|---|
| `projects.html` | 四个 Tab、表单和按钮外观 | Tab 增加后端阶段准入；统一保存改为保存当前阶段；AI立项只填基础字段 |
| 项目事项区 | AI拆分、添加、修改、删除 | 调用前校验项目ID、对象、范围；保存后返回事项依据引用 |
| `docworkshop.html` | 年度/项目/文件浏览和三类详情 Tab | 数据改为后端年度树；上传携带项目ID；溯源读取 chunk/字段证据 |
| `dataworkshop.html` | 表格、问数、表达式界面 | 从当前项目上下文取 `project_id`；项目分析入口禁止空 ID |
| `analysis.html` | 七步布局、聊天区、右侧结果区 | 操作反馈改 Toast；固定步骤总结覆盖；不保存 HTML 作为业务状态 |
| `analysis-wiz.js` | 七步渲染和现有交互 | 去除关键词驱动步骤推进，改为结构化确认命令；上下文只传 ID 和结果 |
| `api.js` | 现有 AuditAPI 封装和字段格式 | 增加阶段保存/finalize/trace detail 调用，后端仍适配现有 DTO |

不改变页面布局的实现方式：后端返回 `setup_stage`、`allowed_actions`、`missing_fields`，前端只切换现有 Tab、按钮 disabled、Toast 和状态文案。

### 10.2 后端：兼容原路由，补充业务命令

建议保留现有 CRUD，并增加下列语义接口；旧接口内部转调同一 service，避免双套逻辑：

| 建议接口 | 作用 | 关键校验 |
|---|---|---|
| `PUT /api/audit/projects/{id}/basic` | 保存立项基础信息 | 仅基础字段白名单 |
| `PUT /api/audit/projects/{id}/target-scope` | 保存对象和范围 | 项目存在、基础阶段完成 |
| `POST /api/audit/projects/{id}/items/recommend` | 推荐事项 | 前两阶段完成；从 DB 读取上下文和知识来源 |
| `PUT /api/audit/projects/{id}/items` | 保存人工确认事项 | 至少一项；记录差异日志 |
| `POST /api/audit/projects/{id}/workspace/finalize` | 创建资料空间并激活项目 | 四阶段完整；幂等 |
| `GET /api/audit/workspace/tree?year=...` | 返回年度—项目—类型—文件树 | 服务端项目权限和年度过滤 |
| `GET /api/audit/traces/{result_type}/{result_id}` | 统一查询结论证据链 | project_id 权限、引用完整性 |
| `PUT /api/audit/analysis/{id}/summaries/{step}` | 覆盖步骤总结 | 固定 message ID、版本和来源 |

兼容要求：

- `POST /api/audit/projects` 的响应字段保持不变，但不再在第一阶段创建 bucket。
- `GET /api/audit/projects/{id}` 继续返回原字段和 `audit_items`，只追加阶段信息。
- 文件、数据、知识、分析现有端点路径保持可用。
- 后端提供前端需要的字段别名，不要求前端改用数据库列名。

### 10.3 服务层职责

- `ProjectLifecycleService`：按现有字段完成阶段校验、保存和允许动作计算。
- `WorkspaceService`：年度派生、bucket 幂等创建、目录分类和项目隔离。
- `DocumentPipelineService`：上传、任务、OCR、chunk、结构化落库状态机。
- `EvidenceService`：统一写入和查询 source refs。
- `AnalysisContextBuilder`：按 `project_id/task_id/step` 装配 LLM 结构化上下文。
- 现有 `knowledge_service`、`regulation_graph`、`expression_engine`、Agent 和 MCP 服务继续复用，不重复实现。

## 十一、数据链路断点清单与修复优先级

| 编号 | 断点 | 风险 | 修复级别 |
|---|---|---|---:|
| B01 | 项目一创建即建 bucket | 产生空项目空间，顺序不真实 | P0 |
| B02 | 立项 AI 一次生成对象、范围、事项 | 越阶段生成，责任边界不清 | P0 |
| B03 | 后端无阶段准入 | 可绕过前端直接调用后续接口 | P0 |
| B04 | 年度只有展示，无统一后端语义 | 跨年度查询和目录不可靠 | P0 |
| B05 | 项目页面部分现有字段未持久化 | 页面输入刷新后丢失，上下文不完整 | P0 |
| B06 | 事项推荐未强制检索案例/知识库 | 推荐可能泛化、无依据 | P0 |
| B07 | chunks 整体塞入 trace JSON | 无法高效按页、字段查询 | P0 |
| B08 | 字段值没有 chunk 引用 | 不能定位抽取值原文 | P0 |
| B09 | 数据工坊允许空 project_id 全局查询 | 存在跨项目分析和数据泄露风险 | P0 |
| B10 | 法规推荐缺少统一条款引用对象 | 结论无法完整复核 | P0 |
| B11 | 聊天记录保存完整 HTML | 上下文污染、状态不可审计 | P1 |
| B12 | 步骤操作持续追加消息 | 对话冗余，返回修改造成多版本并存 | P1 |
| B13 | 1-6 工作流与 7 步页面不统一 | 状态、恢复和总结编号易错位 | P1 |
| B14 | Agent trace、文档 trace、疑点 evidence 各自独立 | 无统一端到端证据查询 | P1 |

## 十二、分阶段实施规划

### 阶段 A：项目生命周期校正（P0）

目标：严格实现四阶段顺序，不创建新 UI。

- 建立字段—阶段矩阵和后端白名单。
- 改造项目创建为 draft，仅保存基础信息。
- 增加对象范围阶段保存和后端校验。
- 事项推荐改为按项目 ID 读取完整上下文。
- 事项确认后才允许 finalize workspace。
- 完成现有页面字段的持久化差距确认。

验收：任何客户端都不能跳过前序阶段；立项完成时数据库无事项、无项目 bucket。

### 阶段 B：资料空间与年度隔离（P0）

- 后端统一派生审计年度。
- finalize 时幂等创建项目 bucket 和分类前缀。
- 资料工坊树由后端真实数据生成。
- 上传、下载、列表、删除均校验项目和年度归属。

验收：不同年度、不同项目无法通过接口串读；同一项目重复 finalize 不产生重复空间。

### 阶段 C：OCR 与证据链（P0）

- 落库 OntoSKU document/job ID。
- 将 chunks 从 JSON 展开为可查询记录。
- 建立字段—chunk、数据行—trace 关联。
- 实现统一 trace 查询响应。
- 验证 PDF 页码、bbox 和原文高亮；Word/Excel/TXT 使用可解释的段落、表格、行号锚点。

验收：随机抽取一个结构化字段，可定位到原文件、页/行、原文片段、解析时间和项目。

### 阶段 D：数据工坊强制主链路（P0）

- 明确六类表与资料类型映射。
- 项目分析 API 强制 `project_id`。
- Agent 只取结构化数据表和证据引用。
- 原始文件仅用于复核和重新解析。

验收：断开原始文件读取权限后，已解析项目仍可完成分析；移除 project_id 的分析请求必须失败。

### 阶段 E：AI上下文与步骤总结（P1）

- 建立 `AnalysisContextBuilder`。
- 前端页面操作只更改状态或 Toast。
- 七个固定 message ID 持久化覆盖。
- 操作日志与对话/LLM上下文分离。
- 统一 LangGraph 与页面七步状态。

验收：每步最多一条正式消息；返回修改只更新原消息；LLM请求体不含 HTML 和全量操作日志。

### 阶段 F：推荐可溯源与端到端验收（P1）

- 事项推荐绑定案例、违规、法规和资料引用。
- 法规推荐绑定条款原文与项目资料依据。
- 疑点、文书继承分析来源引用。
- 运行“立项→空间→上传→OCR→入库→分析→疑点→文书”全链路测试。

验收：任一 AI 输出都能反向展开到规则/法规/案例/数据行/文档原文；引用丢失时结果标记不可确认。

## 十三、验收标准

### 13.1 业务流程

- 立项阶段只能保存项目基础信息。
- 未完成对象和范围时，AI事项推荐被后端拒绝。
- 未确认事项时，不创建资料空间。
- 资料空间创建后项目才进入 active。
- 人工可对 AI 事项推荐进行增删改并保存最终版本。

### 13.2 数据隔离

- 所有文件、结构化数据、分析任务、疑点均能通过 `project_id` 唯一归属。
- 年度树由真实项目数据生成。
- 空 `project_id` 不能执行项目智能分析。
- 跨项目 trace ID 访问必须经过项目权限校验。

### 13.3 溯源

- AI事项显示推荐依据及规则/案例/资料引用。
- 法规显示法规名称、条款、来源、位置、引用原文。
- 数据字段可定位到文件、页码/行号、坐标或段落、原文和解析时间。
- 疑点可定位到数据行、字段来源、违规规则和法规依据。
- 文书引用继承已确认疑点的证据，不重新自由生成来源。

### 13.4 AI上下文

- 请求体不包含聊天 HTML、右侧面板 HTML、全部操作日志。
- 每次 Agent 调用都有 project/task/step、已确认 ID 和来源引用。
- 临时反馈不进入聊天历史。
- 每步只有一条正式总结，固定 ID 覆盖更新。

## 十四、风险与控制措施

| 风险 | 控制措施 |
|---|---|
| 现有前端部分字段无 DB 映射 | 先做字段盘点，由领导确认含义；只添加同名映射，不改现有字段定义 |
| 旧项目没有阶段状态 | 按字段和事项做一次性阶段推断，人工确认后再标记 active |
| chunks 数据格式因文件类型不同 | 统一最小字段，原始元数据放 JSON 扩展，不强行丢失引擎信息 |
| OntoSKU 不可用触发降级 | 降级结果必须标明引擎和溯源等级；无页码时不能伪造坐标 |
| 旧前端仍调用原接口 | 保留原路由并内部适配新 service，分阶段灰度 |
| LLM 推荐缺少证据 | 设后端结果校验器，无来源的条目进入“待人工补证”，不得自动确认 |
| 项目名称变更影响目录 | 关联始终使用 project_id；显示名变化不迁移物理主键路径 |

## 十五、需要领导确认的需求决策

1. “立项单位”是否等同当前创建人/所属组织；若不是，确认页面现有哪个字段承载，不能由架构自行猜测。
2. 审计年度统一取审计期间起始年份，还是项目开始日期年份；建议取审计期间起始年份。
3. `f-target-unit` 与 `f-unit` 是否同一业务含义；若不同，确认审计对象是否允许多个单位。
4. 延伸审计单位、报告文号、项目开始日期、进点日期、审计重点是否属于本轮“现有字段补持久化”范围。
5. 旧项目阶段迁移是否允许按现有数据自动推断后批量人工确认。
6. 无精确条款或无文档页码的 AI 推荐，是否统一标记“待人工核实”并禁止进入最终文书；建议是。
7. 音频转写目前不在现有 OCR 主链中，是否只规划目录和接口，后续再接既有转写服务；本方案不建议本轮新建音频算法能力。

## 十六、最终建议

本轮应定位为“真实业务链路补齐”，不是功能扩张。最优实施顺序是先校正项目生命周期和数据隔离，再补证据链，最后收敛 AI 上下文和对话。这样可以最大限度复用现有前端、Flask 路由、MySQL 六类数据表、OntoSKU 客户端、Agent 和 LangGraph，避免在链路尚未受控时继续增加智能功能。

建议领导先确认第五章目标流程、第七章溯源最低标准、第十五章七项业务口径。确认后再拆分开发任务、接口契约和数据库迁移脚本。
