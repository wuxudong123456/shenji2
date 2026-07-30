# 审计实务工坊项目问题报告

测试日期：2026-07-29  
测试范围：前端静态页面、Flask 后端、REST API、数据库/MinIO/OCR/LLM 配置、后台任务、核心分析状态流、上传路径、权限与安全边界  
测试边界：只分析和测试，未修改业务代码或 UI。仅新增本报告和一个 45 字节上传测试夹具。  
目标地址：`http://127.0.0.1:5000`

## 一、结论

- 项目可以用 `backend/.venv` 启动 Flask，首页及主要 HTML 页面可返回 HTTP 200。
- 项目不能完成核心业务流程。MySQL、MinIO、OCR、LLM 均不可用；核心业务 API 返回 500 或超时。
- 即使外部依赖恢复，前端 `AuditAPI` 的基础地址绑定错误会使大多数 API 请求变成 `undefined/api/...`，核心页面仍无法连接后端。
- 当前不具备验收条件，也不具备上线条件。
- 综合健康度暂评：**26/100**。该分数基于已执行的服务层、接口、脚本和安全测试；浏览器 UI 自动化因运行环境阻塞未纳入通过项。

## 二、实际执行结果

### 已通过

| 功能 | 结果 | 证据 |
|---|---|---|
| 项目虚拟环境 | 通过 | Python 3.12.13；Flask、PyMySQL、MinIO、LangGraph 可导入 |
| Flask 应用导入 | 通过 | 成功注册 65 条路由 |
| Flask 启动 | 通过但不适合生产 | 监听 `0.0.0.0:5000`，使用 Werkzeug Debug 开发服务器 |
| 静态页面返回 | 通过 | `/`、`analysis.html`、`projects.html`、`docworkshop.html`、`knowledge.html`、`dataworkshop.html`、`settings.html` 均 HTTP 200 |
| 前端 JS 语法 | 通过 | `frontend/js/*.js` 六个文件均通过 `node --check` |
| 基础参数校验 | 部分通过 | 空分析意图返回 400；聊天历史缺少 `session_id` 返回 400 |

### 失败

| 功能 | 结果 | 实测表现 |
|---|---|---|
| MySQL | 失败 | 连接 `192.168.3.164:3306` 超时；项目、法规、违规、任务、案例接口均 500 |
| MinIO 上传 | 失败 | 上传测试夹具 15 秒无响应并超时 |
| OCR | 失败 | `/api/ocr/health` HTTP 200，但 `healthy=false` |
| LLM | 失败 | `/api/llm/health` HTTP 200，但 `llm_available=false` |
| 前端 API 客户端 | 失败 | 实际执行生成 `undefined/api/audit/projects` 等错误 URL |
| 核心分析流程 | 失败 | 无法创建分析任务，无法进入违规匹配、法规推荐、确认、上传、分析和疑点报告阶段 |
| 后台任务 | 失败 | 任务列表因 MySQL 不可达返回 500；状态持久化无法验证 |
| 权限控制 | 失败 | 未携带身份凭证的请求直接进入业务函数/数据库层，没有 401 或 403 |

### 未验证

- 真实浏览器点击、表单填写和截图：Playwright Chromium 两次下载分别超时；系统 Edge 控制授权也超时。未伪造页面操作结论。
- 数据库真实表结构、数据量、落库内容、重复写入和事务一致性：目标 MySQL 不可达。
- MinIO 对象是否形成孤儿、bucket 隔离是否真实生效：目标 MinIO 请求超时。
- OCR 后的模板匹配、字段提取、数据表落库、溯源页码/坐标：OCR、LLM、MySQL 均不可用。
- 多用户/多角色越权复现：系统没有可用登录入口、令牌或角色上下文，因此无法建立两个真实用户会话；代码和无凭证请求已确认缺少鉴权门槛。

## 三、问题清单概览

| ID | 严重程度 | 问题 |
|---|---|---|
| AW-001 | 阻断/Critical | 前端 `AuditAPI` 基础地址绑定错误，核心请求全部指向 `undefined/api/...` |
| AW-002 | 阻断/Critical | MySQL 不可达，全部核心业务 API 失败 |
| AW-003 | Critical | Debug 模式对外暴露完整堆栈和 Werkzeug 调试器 |
| AW-004 | Critical | 无身份认证、无角色授权、无项目归属校验，且 CORS 允许任意来源 |
| AW-005 | High | 上传路径不校验项目存在性、文件类型/大小，外部存储超时时请求长时间挂起 |
| AW-006 | High | 分析任务的内存 ID 与数据库自增 ID 不一致，重启回退与后续更新会失效 |
| AW-007 | High | 项目创建与 MinIO bucket 创建非原子，存储失败仍返回项目创建成功 |
| AW-008 | High | 上传 OCR 标称异步但实际同步执行，失败被吞掉且没有可靠后台续跑 |
| AW-009 | High | 大量 Mock、固定结果、假删除和空实现仍出现在用户主路径 |
| AW-010 | Medium | 健康检查误报整体正常，不能反映数据库、MinIO、OCR、LLM 状态 |
| AW-011 | Medium | 未知 HTML 路由返回首页 200，掩盖错误链接和部署缺失 |
| AW-012 | High | Flask Debug 重载触发 OpenBLAS/OMP 内存分配失败，启动稳定性不足 |

## 四、问题详情

### AW-001 前端 API 请求地址为 `undefined/api/...`（Critical）

复现：在 Node 中加载真实 `frontend/js/api.js`，模拟 `window.location.origin=http://127.0.0.1:5000` 后调用项目、知识、分析和文件方法。实际输出分别为：

```text
undefined/api/audit/projects
undefined/api/audit/knowledge/violations?q=x
undefined/api/audit/analysis
undefined/api/audit/projects/p1/files
```

原因：`base` 定义在 `AuditAPI` 顶层，但方法位于 `projects`、`files`、`analysis` 等子对象内；以 `AuditAPI.projects.list()` 调用时，`this` 指向子对象，`this.base` 为 `undefined`。

影响：项目 CRUD、上传、知识检索、分析流程、后台任务、Agent 管理和文书生成等大多数前端功能无法命中后端。

修复建议：统一引用 `AuditAPI.base`，或将请求封装成不依赖动态 `this` 的公共请求函数；增加浏览器级契约测试，断言所有请求 URL 以当前 origin 或配置网关为前缀。

### AW-002 MySQL 不可达导致核心接口全部失败（Critical）

复现：启动 Flask 后请求 `/api/audit/projects`、法规、违规、任务和案例接口。均返回 500；单次请求约 5 至 10 秒。调试页显示：

```text
pymysql.err.OperationalError: (2003, "Can't connect to MySQL server on '192.168.3.164' (timed out)")
```

原因：`.env` 指向远端 MySQL，但当前机器到目标地址不可达，应用启动时也没有依赖预检或熔断。

影响：项目、分析任务、法规、违规、案例、文件元数据、后台任务、对话历史均不可用；核心业务流程在第一步中断。

修复建议：先恢复网络/服务和凭证；启动阶段执行数据库连通性与迁移版本检查；运行期返回结构化 503，而不是调试 500；设置更短连接超时、退避和熔断。

### AW-003 对外暴露 Debug 调试器与完整堆栈（Critical）

复现：触发任一数据库错误，HTTP 500 响应包含 Werkzeug Debugger、完整本地路径、源码行、SQL 调用栈、调试 secret 和交互式 console 页面。

原因：`backend/app.py` 固定以 `debug=True` 启动，并监听 `0.0.0.0`。

影响：泄露服务器路径、依赖、SQL、内部地址和实现细节；交互式调试器在错误配置下可能扩大为远程代码执行风险。

修复建议：生产环境强制关闭 Debug 和 reloader，使用生产 WSGI 服务；统一异常处理和日志关联 ID，响应只返回安全错误码与简短消息。

### AW-004 无鉴权、无项目授权且 CORS 任意放行（Critical）

复现：不携带 Cookie、Token 或 Authorization 请求项目、任务、Agent 和删除接口，请求直接进入业务/数据库层而非返回 401/403。以 `Origin: https://evil.example` 发起预检，返回：

```text
Access-Control-Allow-Origin: https://evil.example
Access-Control-Allow-Methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
```

原因：全局 `CORS(app)` 无白名单；路由没有认证装饰器、用户上下文、角色检查或项目归属条件。创建项目还将 `creator` 固定写为 `system`。

影响：数据库恢复后，任何能访问服务的人都可读写项目、文件、Agent 配置、任务、案例和分析结果；可跨项目枚举、修改或删除数据。

修复建议：接入统一身份认证；按用户/组织/角色和 `project_id` 做服务端授权；所有 SQL 查询加入租户/项目归属条件；收紧 CORS 至可信网关来源；为敏感变更增加审计日志。

### AW-005 上传校验与超时控制缺失（High）

复现：向不存在的项目 `qa-nonexistent` 上传 45 字节文本夹具。接口没有先返回项目不存在，而是立即尝试创建任意 bucket；15 秒内无响应，客户端超时。

原因：上传入口不查询项目存在性；不限制扩展名、MIME、大小、文件名；读取整个文件到内存；MinIO 调用缺少面向请求的快速失败策略。

影响：可构造任意项目 ID 创建 bucket，可能产生孤儿对象；大文件可能耗尽内存；用户请求长时间挂起；危险文件可进入存储和后续解析链。

修复建议：先验证项目归属和状态；限制大小、类型和文件名；流式上传；设置连接/读取超时；对象写入与元数据写入采用补偿机制，失败时清理对象。

### AW-006 分析任务 ID 持久化模型不一致（High）

复现条件：数据库恢复后创建分析任务，再重启服务并查询原 `task_id`。本次因 MySQL 不可达未能完成动态复现，代码路径已确认缺陷。

原因：LangGraph 使用 16 位字符串 `task_id`，但插入 `audit_analysis_tasks` 时没有将该 ID 写入表；设计表的 `id` 是自增整数。重启后的回退查询和后续 UPDATE 却使用字符串 `task_id` 匹配 `id`。

影响：进程重启后任务不可恢复；确认、完成和结果更新可能更新 0 行；前端看到任务丢失或状态长期不一致。

修复建议：为业务任务使用独立唯一 `task_id` 字段并加唯一索引，所有查询/更新统一使用它；数据库事务提交成功后再返回；增加重启恢复集成测试。

### AW-007 项目记录与 MinIO bucket 状态不一致（High）

复现条件：数据库可用、MinIO 不可用时创建项目。本次 MinIO 不可用已确认，但数据库不可用阻止了完整动态复现。

原因：接口先提交项目 INSERT，再尝试创建 bucket；MinIO 异常被无条件吞掉，接口仍返回 `success=true` 和 bucket 名。

影响：产生“项目存在但存储不可用”的半成功状态；后续上传才暴露错误，难以恢复和对账。

修复建议：明确项目创建状态为 `provisioning/active/failed`；bucket 创建成功后再激活项目，或通过事务外补偿删除/标记失败；响应必须反映存储初始化结果。

### AW-008 OCR 标称异步但实际同步，失败无可靠续跑（High）

复现：上传请求在外部存储阶段已持续超过 15 秒；代码显示对象写入后直接在请求线程调用 `OCREngine.parse()`，并将所有 OCR 异常吞掉。

原因：上传路由没有提交 `audit_task_queue`；注释“异步非阻塞”与实现不符。OCR 失败只将响应标成 `pending`，未见该上传路径创建可重试任务。

影响：大文件会占用 Web 工作线程；请求容易超时；OCR 失败后文件可能永久停在 pending，用户无法获知错误或可靠重试。

修复建议：上传仅完成校验、存储和任务入队；Worker 执行 OCR/提取并持久化进度、错误和重试次数；为重复提交建立幂等键。

### AW-009 用户主路径仍含 Mock、固定结果和假实现（High）

复现/证据：

- `frontend/js/analysis-wiz.js` 固定 `totalRecords=5`、金额映射、法规全文 Mock 和多个延时模拟步骤。
- `frontend/js/portal.js` 固定资料总数 6、固定三条待办、空任务时伪造“银行流水识别中”，智能搜索返回固定违规/法规/案例。
- `frontend/docworkshop.html` API 失败时展示三个假项目。
- `MinioAPI.deleteFile()` 直接返回 `{ok:true}`，没有发请求。
- `analysis.html` “重新评估”按钮用定时器固定显示“未检测到豁免审批文件”。
- `toolbox.html` 多个工具仅调用 `todo()`。

影响：页面看似有结果但不对应真实数据库和任务状态，可能误导审计人员作出错误判断；删除和评估操作会出现假成功。

修复建议：验收环境禁止 Mock 降级；错误时展示明确失败和重试入口；对所有写操作校验 HTTP 状态和服务端结果；将演示数据与正式环境通过构建配置完全隔离。

### AW-010 健康检查误报（Medium）

复现：`/api/health` 返回 `status=ok`，同时 MySQL 500、上传超时、OCR `healthy=false`、LLM `false`。

原因：总健康接口只返回固定 `ok` 和 MinIO 地址，不探测任何依赖。

影响：桌面壳、网关、运维和负载均衡可能把不可用实例当作健康实例继续分流。

修复建议：区分 liveness 与 readiness；readiness 检查 MySQL、MinIO、OCR、LLM 和必要表/版本，任一核心依赖失败时返回 503，并记录耗时。

### AW-011 未知页面返回首页 200（Medium）

复现：请求 `/does-not-exist.html`，返回完整首页且 HTTP 200。

原因：通配页面路由找不到目标文件时统一回退 `index.html`，但项目不是使用前端路由的 SPA。

影响：坏链接、拼写错误和部署漏文件被掩盖；监控、搜索引擎和前端错误处理无法识别 404。

修复建议：仅 `/` 返回首页；不存在的 `.html` 返回标准 404；增加静态资源和导航链接巡检。

### AW-012 Debug 重载导致启动不稳定（High）

复现：实际启动日志出现：

```text
OMP: Error #111: Memory allocation failed.
OpenBLAS error: Memory allocation still failed after 10 retries, giving up.
```

原因：Debug reloader 启动多进程，同时导入可能初始化 FAISS/OpenBLAS 等重资源组件；开发服务器没有资源和进程模型控制。

影响：服务可能在重载时崩溃、重复初始化后台线程或产生多个 Python 进程；启动行为不可预测。

修复建议：关闭 reloader；延迟初始化重资源组件；限制 BLAS 线程；使用生产 WSGI 进程模型并做冷启动/重启测试。

## 五、核心流程判定

```text
输入审计意图
  ├─ 前端 AuditAPI URL 错误 → 阻断
  └─ 绕过前端直调后端
       ├─ LLM 不可用 → 阻断
       ├─ MySQL 不可用 → 阻断
       └─ 无法进入违规匹配/法规推荐/人工确认
            └─ MinIO/OCR 不可用 → 上传与解析阻断
                 └─ 表达式分析、疑点报告、落库均未执行
```

结论：核心流程 **0 次完整跑通**。当前只验证到“页面可返回、部分输入校验有效”，不能视为产品可用。

## 六、验收与上线判断

- 功能验收：不具备。核心流程、落库、上传、任务、状态恢复均未通过。
- 安全验收：不具备。Debug 暴露、无鉴权/授权、任意 CORS 是上线阻断项。
- 稳定性验收：不具备。依赖超时、请求挂起、Debug 多进程内存失败。
- 数据验收：不具备。数据库不可达，无法验证数据完整性、幂等、隔离和恢复。
- 演示条件：仅能展示静态页面；由于大量 Mock，不应把页面展示视为真实业务演示。

## 七、建议修复优先级

1. **P0 安全封禁**：关闭 Debug/reloader；限制 CORS；接入身份认证、角色授权和项目归属校验。
2. **P0 恢复基础设施**：打通 MySQL、MinIO、OCR、LLM；实现可靠 readiness 与结构化 503。
3. **P0 修复前端 API 基址**：确保真实浏览器请求命中同源 `/api/audit/*`，增加端到端契约测试。
4. **P0 修复分析任务持久化 ID**：统一 LangGraph 与数据库任务标识，验证重启恢复和人工确认状态流。
5. **P1 重构上传/后台任务可靠性**：项目校验、文件限制、流式上传、入队、幂等、补偿、超时和重试。
6. **P1 清除正式主路径 Mock/假成功**：API 失败显示真实错误，禁止演示数据静默降级。
7. **P1 数据隔离与一致性测试**：双用户、双项目、重复提交、取消/重试、对象与元数据对账。
8. **P2 HTTP 与运维完善**：404、错误码、日志关联 ID、性能基线、并发和故障注入测试。

## 八、下一轮验收前置条件

- 提供可从测试机访问的 MySQL、MinIO、OCR、LLM 地址和测试数据。
- 提供至少两个不同角色测试账号，用于真实越权和项目隔离验证。
- 恢复可控浏览器运行时或批准系统浏览器控制，以完成逐页面点击、控制台、网络和截图取证。
- 准备一组可公开用于测试的 PDF/Excel/图片材料及期望抽取结果。
