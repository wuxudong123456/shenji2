# Phase 2 验收报告（TEST_REPORT_PHASE_2）

> **验收对象**：[PHASE_2.md](phase-exec/PHASE_2.md) §9 完成标准（8 项）
> **验收日期**：2026-08-07
> **分支 / 提交**：`phase2`（P2 开发区间 `92448fd → 36fbcb1`，共 9 个切片提交）
> **执行者**：Claude（自动化验收）

---

## 一、验收结论（总表）

| # | §9 完成标准 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | M002 迁移执行成功，可回滚 | ✅ 通过 | `tt.audit_document_traces` 12→18 列（audit_year/file_category/file_subcategory/minio_bucket/file_size/deleted_at + idx_audit_year/idx_project_cat）；走 [migrate.py](../backend/data/migrate.py) `migrate_phase2_trace_columns` 幂等函数（information_schema 预检），附回滚 DDL |
| 2 | 年度树 `GET /workspace/tree?year=` 返回真实年度/项目/文件（非 mock） | ✅ 通过 | `test_p2_tree.py` 13/13：2026 项目含 A 不含 B(2025)、counts 五类聚合、files[] 来自 manifest |
| 3 | 跨项目 / 跨年度访问被拒（P2-10 全部命中 403/404） | ✅ 通过 | download/delete 跨项目 key→403（`test_p2_download_delete.py` 12/12）；年度树 `?year=2099`→空、不串年度；不存在项目→404 |
| 4 | manifest 与 MinIO 实际对象一致（对账无静默漂移） | ✅ 通过 | upload 同时写对象+追加 manifest（`test_p2_upload_prefix.py` 验 object_key 一致）；软删对象留原位+manifest.deleted=true；tree 读 manifest 缺失时 WARN + trace 对账自愈（不静默） |
| 5 | 重复 finalize 幂等（Phase 1 行为不回退，manifest 不被覆盖） | ✅ 通过 | `test_p2_manifest.py` 幂等 created_at 不变 10/10；`test_p1_flow.py` finalize 幂等 7/7（Phase 1 行为未破坏） |
| 6 | upload 落正确前缀 + 不再二次建桶 + OCR 触发未破坏 | ✅ 通过 | `test_p2_upload_prefix.py` 19/19（.pdf→`text/pdf/`、.png→`image/` 无子目录、trace 5 新列全落）；P2-2 删二次 make_bucket；upload 仍返回 task_id + ocr_status:pending（异步 OCR 触发保留） |
| 7 | §8 验收脚本全部通过 | ✅ 通过 | P2 测试套 9 文件 **140/140**（见 §三）；P2-1/2/3/4 由 upload/tree/files/manifest 间接+直接覆盖 |
| 8 | `05-regression-baseline.md` 回归通过（旧接口、Phase 1 流程未破坏） | ✅ 通过 | baseline 冒烟全绿（health/ocr/llm/projects/legacy/deprecated 旧模式）；`test_p1_flow.py` 7/7 |

**总判定：Phase 2 功能验收通过，可收尾。** 资料空间管理（年度派生 / 对象前缀 / workspace manifest / 年度项目树 / 落位/列表/下载/软删/隔离）全链路打通，Phase 1 流程与旧接口未破坏。

---

## 二、环境与基线

| 组件 | 状态 | 备注 |
|---|---|---|
| Flask 后端 `:5000` | ✅ 运行 | `/api/health` ok |
| MySQL | ✅ 运行 | `tt.audit_document_traces` 含 M002 六列两索引 |
| MinIO `:9100` | ✅ 运行 | 每项目独立 bucket `audit-project-{pid}`，manifest 往返正常 |
| OCR `:5005`（MinerU） | ✅ 健康 | upload 异步 OCR 触发保留 |
| LLM `:8765` | ✅ 可用 | `llm_available:true` |

**测试脚本结果**：

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| [test_workspace_service.py](../backend/tests/test_workspace_service.py) | P2-1 年度派生 / P2-4 分类 / P2-3 manifest 纯函数 / P2-10 pid 解析 | 42/42 |
| [test_minio_client_bucket.py](../backend/tests/test_minio_client_bucket.py) | §6.7 bucket 参数化（默认桶不破 + 隔离 + recursive） | 12/12 |
| [test_p2_manifest.py](../backend/tests/test_p2_manifest.py) | P2-3 manifest MinIO 往返 + 幂等 | 10/10 |
| [test_p2_finalize_manifest.py](../backend/tests/test_p2_finalize_manifest.py) | P2-3 §6.6 finalize 生成首版 manifest | 10/10 |
| [test_p2_upload_guard.py](../backend/tests/test_p2_upload_guard.py) | P2-2 upload 前置校验 + 删二次建桶 | 5/5 |
| [test_p2_upload_prefix.py](../backend/tests/test_p2_upload_prefix.py) | P2-6 §3.1 前缀 + trace 5 新列 + manifest 增量 | 19/19 |
| [test_p2_files_list.py](../backend/tests/test_p2_files_list.py) | P2-7 files manifest 化 + year/category 过滤 + ocr_done join | 17/17 |
| [test_p2_tree.py](../backend/tests/test_p2_tree.py) | P2-5 年度树 + P2-10 年度隔离 | 13/13 |
| [test_p2_download_delete.py](../backend/tests/test_p2_download_delete.py) | P2-8 下载 / P2-9 软删 / P2-10 跨项目拦截 | 12/12 |
| [test_p1_flow.py](../backend/tests/test_p1_flow.py) | Phase 1 回归（立项链 + finalize 幂等） | 7/7 |

**合计**：P2 测试套 140/140；Phase 1 回归 7/7；`05-regression-baseline.md` 冒烟全绿。

---

## 三、§8 验收脚本逐项证据

### 3.1 P2-1 年度派生（决策 12）
`derive_audit_year(audit_period, created_at)`：audit_period 正则取首年优先，created_at 兜底。纯测 8 项 + finalize/upload 实跑 trace.audit_year=2026 验证。

### 3.2 P2-2 bucket 延迟创建
upload 前置校验 `setup_stage=workspace`（否则 409 含 setup_stage）+ 桶存在（否则 409）；删除二次 make_bucket。`test_p2_upload_guard.py` 5/5。

### 3.3 P2-3 workspace manifest（单一事实源）
9 个函数（compute_safe_name / build_file_prefix / build_manifest_path / load/save / init_first_manifest / build_file_entry / append / mark_deleted）+ finalize §6.6 接入。manifest 存 `{year}/{pid}-{safe_name}/workspace-manifest.json`。MinIO 往返 10/10 + finalize 端到端 10/10。

### 3.4 P2-4 文件分类（§3.4 映射）
`classify_file(filename, content_type)` 后端判定 text/{word,pdf,excel,txt}、image、audio/original、video、other。纯测 14 项。

### 3.5 P2-5 年度项目树
`GET /api/audit/workspace/tree?year=` 读所有 workspace 项目按年度过滤，manifest 跨项目汇总 counts 五类；manifest 缺失回退 trace 对账重建首版 + WARN（不静默）。13/13。

### 3.6 P2-6 upload §3.1 前缀
`minio_path = {year}/{pid}-{safe_name}/{category}/[{subcategory}/]{file_id}.{filename}`（**叶子保留原文件名**，§3.3 示例口径；§6.1「{原扩展名}」与 §3.3 冲突，已确认取保留原名）。trace 落 5 新列；manifest 增量追加。19/19。

### 3.7 P2-7 文件列表 manifest 化
`GET /projects/{id}/files` 数据源改 manifest，默认过滤软删，支持 `year`/`category` 过滤，ocr_done 与 trace join，响应增 audit_year/category/subcategory/size/deleted。17/17。

### 3.8 P2-8/9/10 下载 / 软删 / 跨项目拦截
- download：`project_id` 派生每项目 bucket + `parse_pid_from_key` 前缀校验不匹配 403。
- delete：软删（trace.deleted_at=NOW() + manifest.deleted=true，对象留原位 §3.5）。
- 跨项目 download/delete 均命中 403；不存在项目 404。
- 旧 `project=<name>` 模式灰度保留（§6.8，deprecated）。
12/12 + parse_pid 纯测 5 项。

---

## 四、行为变更说明（对应 `05-regression-baseline.md` §7）

本 Phase 有意改变以下接口行为，按基线 §7「须显式标注」要求记录：

| 接口 | 变更 | 依据 | 兼容性 |
|---|---|---|---|
| `POST /projects/{id}/upload` | 增加 `setup_stage=workspace` 前置校验（未 finalize→409）；minio_path 改 §3.1 前缀；trace 增 5 列；写 manifest | PHASE_2 §6.1/§6.6 | 响应字段结构不变（success/file_id/file_name/minio_bucket/minio_path/trace_id/task_id/ocr_status）；OCR 触发保留 |
| `GET /projects/{id}/files` | 数据源 trace→manifest；增 year/category 过滤；响应增 audit_year/category/subcategory/size/deleted | PHASE_2 §6.2 | 前端 Phase 5 切换；ocr_done 保留 |
| `GET /workspace/download` | 增 `project_id` 分支（每项目桶 + 跨项目校验） | PHASE_2 §6.4 | 旧 `project=<name>` 模式灰度保留至 Phase 5（§6.8） |
| `DELETE /workspace/delete` | 增 `project_id` 分支（软删） | PHASE_2 §6.5 | 旧 `project=<name>` 物理删模式灰度保留（§6.8） |
| `POST /projects/{id}/workspace/finalize` | 成功后追加生成首版 manifest（幂等，失败不阻断） | PHASE_2 §6.6 | Phase 1 finalize 行为不回退（test_p1_flow 7/7） |
| `minio_client` 5 方法 | 加 `bucket=None` 参数（默认回落 MINIO_BUCKET） | PHASE_2 §6.7 | 默认桶调用零改动（test_minio_client_bucket 12/12） |

未变更接口（baseline 冒烟全绿）：`/api/health`、`/api/ocr/health`、`/api/llm/health`、`GET /api/audit/projects`、`GET /api/projects`（legacy）、`GET /workspace/files`（deprecated 旧模式）。

---

## 五、遗留 / 后续

- **旧 `{pid}/raw/` 文件**（K3）：不迁移；首次年度树/列对象纳入 manifest 时 `legacy_raw=true`（tree 自愈路径已支持）。存量项目若 finalize 于 P2-3 前（无 manifest），首次 `GET /workspace/tree` 触发 trace 对账重建。
- **manifest 对账告警**：当前以「upload/软删为唯一合法变更点 + tree 读缺失 WARN 自愈」保证不静默漂移；运行时「列对象 vs manifest」差集主动比对未做（§7 对策，按需在 Phase 5 前端切换前补）。
- **前端 mock 数据**：资料空间相关前端页仍用 mock，Phase 5 切换至本 Phase 提供的 manifest/tree/files 接口。
