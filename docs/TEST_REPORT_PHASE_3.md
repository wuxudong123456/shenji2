# Phase 3 验收报告（TEST_REPORT_PHASE_3）

> **验收对象**：[PHASE_3.md](phase-exec/PHASE_3.md) §9 完成标准（10 项）
> **验收日期**：2026-08-08
> **分支 / 提交**：`phase2`（Phase 3 共 7 个提交，基线 `9935677`(Phase 2 验收) → `601b7d0` → `026af64` → `8de4aea` → `ecf687f` → `18ce5f1` → `98573ea` → `1f6a5cb`）
> **执行者**：Claude（自动化验收）

---

## 一、验收结论（总表）

| # | §9 完成标准 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | 数据库 `M003` 迁移执行成功，可回滚 | ✅ 通过 | `601b7d0`：trace +5 列（external_document_id/external_job_id/parse_engine/parse_status/parsed_at）+2 索引；task_queue +1 列（payload JSON）。走 `information_schema` 幂等预检，二跑全 skip；回滚 DDL 见 PHASE_3 §5 注释 |
| 2 | 上传 → trace → OCR（OntoSKU）→ Markdown 落库 → data_* 有行（端到端） | ✅ 通过 | [test_p3_ocr.py](../backend/tests/test_p3_ocr.py) **30/30**：真实 PDF 经 OntoSKU 解析，trace.parse_engine='ontosku'、ocr_content 非空、data_* 命中行 + doc_type 非空 |
| 3 | OntoSKU 失败自动降级 LiteParse / LLM，全程 `parse_engine` 标记正确 | ✅ 通过 | [test_p3_slice7.py](../backend/tests/test_p3_slice7.py) **14/14**：三档（liteparse 实质文本 / local-llm 扫描件空文本 / local-llm 异常）全覆盖；档位先定后抽，LLM 抽取失败仍标对档位 |
| 4 | `parse_status` 随任务状态同步（pending/running/done/failed） | ✅ 通过 | [test_p3_slice3.py](../backend/tests/test_p3_slice3.py) **19/19**：`_set_trace_parse_status` running/failed/done+parsed_at 全态；终态同步 trace='failed' |
| 5 | 失败重试上限 3 + 退避；超限终止 | ✅ 通过 | [test_p3_slice3.py](../backend/tests/test_p3_slice3.py)：退避序列=[1,2]（2^(retry-1)），re-submit×2，第 3 次终态 failed、retry_count=3 |
| 6 | 进程重启恢复卡住任务（processing→pending） | ✅ 通过 | 本报告 §3.6：合成 processing 任务 #83 → kill backend → 重启 → 启动日志「恢复 1 个卡住的任务」→ DB 翻转为 pending（超时回收按计划挪 Phase 6） |
| 7 | 重新解析 `ocr_version+1`，旧版本保留 | ✅ 通过 | [test_p3_ocr.py](../backend/tests/test_p3_ocr.py) P3-12：reparse 异步 → 新 task → 轮询 completed → ocr_version 旧值+1。**口径见 §3.7**：本 Phase 旧文本覆盖写，版本号可追溯，旧内容存档待 Phase 4 |
| 8 | 引擎选路统一（`_run_ocr_task` 唯一选路点，`OCREngine` 降为取客户端）；payload/result 分离 | ✅ 通过 | `98573ea`：`_run_ocr_task` docstring + `OCREngine`/`get_engine()` 注释标注「选路权移交，仅取客户端」，**不新建 OcrOrchestrator**；`026af64`：create_task 加 payload 参数 + get_task/_clean_task 读写 payload，worker 优先读 payload（result 过渡兜底在途任务） |
| 9 | §8 验收脚本全部通过 | ✅ 通过 | 见 §四对照表：test_p3_ocr.py(P3-1/3/5/6/7/9/12) + slice3(P3-5/10) + slice7(P3-4) + slice456(P3-3/7/9) + slice9(P3-12) + 手工(P3-11) + 代码审查(P3-8) |
| 10 | `05-regression-baseline.md` 回归通过（Phase 1/2 行为未破坏） | ✅ 通过 | 基线冒烟 **14/14** 全 200；P1+P2 回归 **147/147**（P1 7/7 + P2 140/140） |

**总判定：Phase 3 功能验收通过，可收尾。** 解析链路在最窄口径（上传→OntoSKU→Markdown→分类→字段→data_* 行）已端到端加固完毕，任务可靠（重试/恢复/重解析）三项齐备，全程 `parse_engine` 标记正确，未破坏 Phase 1/2。

---

## 二、环境与基线

| 组件 | 状态 | 备注 |
|---|---|---|
| Flask 后端 `:5000` | ✅ 运行 | `task_id=bkny1f6k7`（P3-11 验证后重启实例）；`/api/health` 正常 |
| MySQL | ✅ 运行 | `tt.audit_document_traces` 含 M003 解析列；`tt.audit_task_queue` 含 payload 列 |
| MinIO `:9100` | ✅ 运行 | 每项目桶隔离，e2e 上传/重解析产物落桶 |
| OntoSKU `192.168.3.189:5005` | ✅ 可达 | e2e 真实解析单文档 ~18s，document_id/job_id 非空 |
| LiteParse `127.0.0.1:5006` | ✅ 可达 | 三档降级中间档（slice7 用 mock 隔离） |
| LLM `:8765` | ✅ 运行 | `llm_available=true`（本会话已起，与 Phase 1 验收时未起不同） |

**测试脚本结果**：

| 脚本 | 用途 | 结果 |
|---|---|---|
| [test_p1_flow.py](../backend/tests/test_p1_flow.py) | P1 流程回归 | **7/7** |
| [test_workspace_service.py](../backend/tests/test_workspace_service.py) | P2-1/3/4/10 纯函数 | **42/42** |
| [test_minio_client_bucket.py](../backend/tests/test_minio_client_bucket.py) | P2 bucket 隔离 | **12/12** |
| [test_p2_manifest.py](../backend/tests/test_p2_manifest.py) | P2-3 manifest 往返 | **10/10** |
| [test_p2_finalize_manifest.py](../backend/tests/test_p2_finalize_manifest.py) | P2-3 finalize 首版 | **10/10** |
| [test_p2_upload_guard.py](../backend/tests/test_p2_upload_guard.py) | P2-2 前置校验 | **5/5** |
| [test_p2_upload_prefix.py](../backend/tests/test_p2_upload_prefix.py) | P2-6 前缀/分类/manifest | **19/19** |
| [test_p2_files_list.py](../backend/tests/test_p2_files_list.py) | P2-7 files 列表 | **17/17** |
| [test_p2_tree.py](../backend/tests/test_p2_tree.py) | P2-5 年度树 | **13/13** |
| [test_p2_download_delete.py](../backend/tests/test_p2_download_delete.py) | P2-8/9/10 下载/软删 | **12/12** |
| [test_p3_slice3.py](../backend/tests/test_p3_slice3.py) | P3-5/P3-10 状态机+退避 | **19/19** |
| [test_p3_slice7.py](../backend/tests/test_p3_slice7.py) | P3-4 三档降级 | **14/14** |
| [test_p3_slice456.py](../backend/tests/test_p3_slice456.py) | P3-3/7/9 客户端+doc_type | **18/18** |
| [test_p3_slice9.py](../backend/tests/test_p3_slice9.py) | P3-12 reparse 异步 | **13/13** |
| [test_p3_ocr.py](../backend/tests/test_p3_ocr.py) | P3 端到端真实 PDF | **30/30** |

**合计**：P1+P2 回归 147/147 + P3 切片 64/64 + P3 e2e 30/30 = **241/241 全绿**。

---

## 三、§9 完成标准逐项证据

### 3.1 M003 迁移（§9-1，`601b7d0`）

- trace 表 +5 列：`external_document_id VARCHAR(100)` / `external_job_id VARCHAR(100)` / `parse_engine VARCHAR(50)` / `parse_status VARCHAR(20) NOT NULL DEFAULT 'pending'` / `parsed_at DATETIME`；+2 索引 `idx_parse_status` / `idx_external_doc`。
- task_queue +1 列：`payload JSON NULL`。
- 迁移走 [migrate.py](../backend/data/migrate.py) 函数式（`DATABASE="tt"` 全局 + `information_schema` 幂等预检），二跑全 skip；回滚 DDL 注释保留于 [PHASE_3.md](phase-exec/PHASE_3.md) §5。结论：✅

### 3.2 端到端解析（§9-2，P3-1/3/6/9）

[test_p3_ocr.py](../backend/tests/test_p3_ocr.py) 用仓库 `data/test_contract_cn.pdf`（24KB 真实中文合同）真实解析：

```
上传 → trace_id+task_id+ocr_status='pending'；trace.parse_status 已初始化、parse_engine NULL；task.payload 非空、result NULL
轮询 completed（~18s）→ parse_status='done' / parsed_at 非空 / parse_engine='ontosku'
                   / ocr_content 非空 / external_document_id 非空 / external_job_id 非空
data_* 表命中行 / doc_type 非空 / document_trace_id 关联正确
```

结论：✅

### 3.3 三档降级 + parse_engine 标记（§9-3，P3-4）

[test_p3_slice7.py](../backend/tests/test_p3_slice7.py) 14/14 覆盖 `_fallback_local_extract` 全路径（mock LiteParseClient + auto_classify_and_extract）：

| 场景 | engine | 说明 |
|---|---|---|
| LiteParse 实质文本（≥10 字） | `liteparse` | text 取 LiteParse 产物，fields 经 LLM 抽取 |
| LiteParse 空白（扫描件） | `local-llm` | 文本回落 existing_ocr（修了 `lp_text or existing_ocr` 对纯空白串误判为真的 bug） |
| LiteParse success=False | `local-llm` | |
| LiteParse 抛异常 | `local-llm` | |
| LiteParse 短文本(<10) | `local-llm` | 保留 LiteParse 短文本 |
| LLM 抽取异常 | success=False | **档位先定后抽**：engine 仍='liteparse'（已定档），error 含兜底说明 |

主路径 OntoSKU 档由 e2e 坐实（parse_engine='ontosku'）。结论：✅

### 3.4 parse_status 状态机同步（§9-4，P3-5）

[test_p3_slice3.py](../backend/tests/test_p3_slice3.py) 验 `_set_trace_parse_status` + `_fail_with_trace`：

- running：worker 取走后落 running，parsed_at 仍 NULL。
- failed：终态（fail_task 返回 False）同步 trace.parse_status='failed'；待重试（返回 True）保持 running 不误标 failed。
- done+parsed_at：完成时 parse_status='done' 且 parsed_at 非空。
- None trace_id 安全无异常（防御 _fail_with_trace 无 trace 场景）。

结论：✅

### 3.5 失败重试上限 3 + 指数退避（§9-5，P3-10）

[test_p3_slice3.py](../backend/tests/test_p3_slice3.py) 全生命周期（`fail_task` 内 `time.sleep(2**(retry-1))` + 懒加载 `submit_task` 重投）：

```
重试1 → True（回 pending）；退避=1s；re-submit
重试2 → True（回 pending）；退避=2s；re-submit
重试3 → False（终态 failed）；无 sleep、无 re-submit
末态 status='failed'，retry_count=3
退避序列=[1,2]，re-submit 序列长度=2（仅前两次），均为同一 task_id
```

结论：✅（贴合 §6.10「重试前延迟」，不新建 poller，循环导入用懒加载规避）

### 3.6 进程重启恢复（§9-6，P3-11）

确定性故障注入（不依赖 kill-mid-OCR 的时序窗口，隔离验证恢复机制本身）：

```
① 注入合成任务 #83，UPDATE status='processing'（模拟 worker 进程被杀遗留）
   PRE_STATUS=processing
② taskkill /F /T 旧 backend（PID 1916）
③ 重启 backend → 启动日志：[task_manager] 恢复 1 个卡住的任务
④ DB 复核：POST_STATUS=pending（processing→pending 翻转成立）
   RECOVERED_FROM_PROCESSING=True
```

`recover_stuck_tasks()`（[task_manager.py:157](../backend/services/task_manager.py#L157)）逻辑为 `UPDATE ... SET status='pending' ... WHERE status='processing'`，由 [app.py:430-434](../backend/app.py#L430) `if __name__=='__main__'` 启动块同步调用。Phase 3 未改此函数，本次确认其经启动接线端到端生效。**超时回收（processing 僵死自动回收）按 §6.11 挪 Phase 6**（P6-8，阈值需借监控看板 OntoSKU 真实耗时数据联调定）。结论：✅

### 3.7 重新解析 ocr_version+1（§9-7，P3-12）

[test_p3_ocr.py](../backend/tests/test_p3_ocr.py) P3-12 + [test_p3_slice9.py](../backend/tests/test_p3_slice9.py) 13/13：

- reparse → 200，返回 `{success, document_id, task_id, ocr_version, message}`，**不再同步返 result**（异步化）。
- 带 template_name 透传 sku_profile 仍 200。
- 前置校验：basic 阶段 → 409（「资料空间」）、不存在 document_id → 404、缺 document_id → 400。
- 轮询重解析 task completed → `ocr_version` 旧值+1。

> **「旧版本保留」口径（决策 3，用户拍板）**：§6.12 字面「旧 ocr_content/extracted_fields 保留」在本 Phase 落地为「**版本号可追溯**」——ocr_version 递增、新内容覆盖写 trace 各列，旧文本内容本 Phase **不存档**；旧证据标 superseded 待 Phase 4 建 `audit_document_chunks` 时做。此为已确认的决策，非回归。

结论：✅

### 3.8 引擎选路统一 + payload/result 分离（§9-8）

- **选路统一**（`98573ea`，§3.1/§6.3）：以 [task_worker._run_ocr_task](../backend/services/task_worker.py) 为**唯一选路点**（OntoSKU 主→LiteParse 降级→LLM 兜底）；[OCREngine](../backend/services/ocr_client.py)/`get_engine()` docstring 标注「选路权移交，仅取客户端」。**不新建 OcrOrchestrator**（执行包 §3.1 明令）。
- **payload/result 分离**（`026af64`，§6.2）：`create_task` 加 `payload` 参数（默认 None，旧调用零改动）；`get_task` SELECT 加 payload、`_clean_task` 解析；upload 路由删独立 `UPDATE result`，改 `create_task(..., payload={trace_id,minio_bucket,minio_path,filename,project_id,sku_profile})`（**修复 sku_profile 断链**——原 worker:147 读但 create_task 不接、upload 没塞，永远 None）；worker `task_payload = task_data.get("payload") or task_data.get("result") or {}`（payload 优先，result 过渡兜底在途任务）。

结论：✅

---

## 四、§8 验收脚本对照

| §8 断言 | 覆盖方式 | 结论 |
|---|---|---|
| P3-1/P3-2 上传+trace+入队（payload 列） | test_p3_ocr（trace.parse_status 初始化/engine NULL；task.payload 非空/result NULL） | ✅ |
| P3-3/P3-5/P3-6 OntoSKU→Markdown 落库 | test_p3_ocr（parse_engine='ontosku'、external_document_id/job_id 非空、ocr_content 非空、parse_status='done'/parsed_at） | ✅ |
| P3-4 降级 | test_p3_slice7（三档 liteparse/local-llm） | ✅ |
| P3-7/P3-8/P3-9 分类→字段→data_* 有行 | test_p3_ocr（ontosku_template 非引擎字符串、含 `/`；data_* 行 doc_type 非空）+ test_p3_slice456（template_name/doc_type 抽取 + data 写入） | ✅ |
| P3-10 失败重试 | test_p3_slice3（退避 [1,2] + re-submit×2 + 终态 failed） | ✅ |
| P3-11 任务恢复 | 本报告 §3.6 确定性故障注入（processing→pending） | ✅ |
| P3-12 重新解析 | test_p3_ocr（ocr_version+1）+ test_p3_slice9（异步 shape + 前置校验） | ✅ |

P3-8（字段提取：命中别名列、未命中进 extra_fields 原样保留）由代码审查确认——`field_mapper` 本 Phase **不改别名表**（§3.4，别名按模板自动扩展归 Phase 7），e2e 中 fields 经 mapper 落 data_* 列已间接坐实。

---

## 五、行为变更（前端联调注意）

| 接口 | 变更 | 影响 |
|---|---|---|
| `POST /api/audit/documents/reparse` | **同步 → 异步**（§6.12 mandated） | 旧：同步返回 extract_result。新：返回 `{success, document_id, task_id, ocr_version, message}`，需轮询 `GET /api/audit/tasks/<task_id>` 到 completed 再读 trace 新内容。**前端需适配**。 |

其余接口（upload 响应 shape、files 列表、download/delete、项目树）均未变，P1/P2 回归 147/147 坐实。

---

## 六、环境限制与残留事项

1. **P3-11 超时回收挪 Phase 6**：本 Phase 只做「重启恢复 processing→pending」（§6.11 唯一必做项）。processing 僵死的自动超时回收（阈值需借 Phase 6 监控看板 OntoSKU 真实耗时数据联调定）归 P6-8，本轮不实现——非缺口，属计划内边界。
2. **chunks 不落库**：OntoSKU 返回的 `chunks.json` 本 Phase 拿到但不落库（§3.3/§0），逐条落 `audit_document_chunks` + 字段→chunk 溯源归 Phase 4。
3. **data_* 只写不查**：六表本 Phase 只写（含 doc_type），查询/采购/访谈新表归 Phase 5。
4. **field_mapper 别名静态**：本 Phase 不改别名表（缺口率 59-85% 为已知），未命中字段进 extra_fields 原样保留；按模板自动扩展归 Phase 7。
5. **reparse 旧版本不存档**：见 §3.7 口径，旧内容覆盖写，版本号可追溯，旧证据 superseded 待 Phase 4。

---

## 七、Phase 3 收尾状态

- 数据库 M003（trace 解析列 + task payload 列）：✅ 执行。
- 解析链路加固（trace 初始化 / payload-result 分离 / external_id+job_id+engine 落 trace / ocr_content / ontosku_template 真值 / doc_type 补写）：✅ 实现并验收。
- 任务可靠（parse_status 状态机 / 三档降级 / 失败重试退避 / 重启恢复 / 重解析异步+版本号）：✅ 实现并验收。
- 选路统一（_run_ocr_task 唯一选路点，不新建 OcrOrchestrator）：✅ 实现。
- 回归基线 + P1/P2 套件：✅ 未破坏（147/147 + 基线 14/14）。

**Phase 3 验收通过，可进入 Phase 4（字段溯源：audit_document_chunks / audit_field_sources）。** 待办（非阻塞，跨 Phase）：reparse 前端联调改异步轮询、Phase 4 落 chunks + 旧版本 superseded 标记、Phase 6 超时回收阈值联调。
