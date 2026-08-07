# 06 — OntoSKU API 契约快照（开发规格）

> 用途：Phase 3 编写 OntoSKU 调用与 chunk 解析的对照依据；无此契约不开工。
> 依据：① 2026-08-06 用户提供的真实解析样例（锦川市清岳区政务服务中心卷宗目录）；② `backend/services/ontosku_client.py` 代码已知的 API/ZIP 结构。
> 版本：v1（2026-08-06）。随真实 API 响应/ZIP 内容补充升版。

---

## 1. 服务信息

| 项 | 值 |
|---|---|
| 地址 | `http://192.168.3.189:5005` |
| 版本 | OntoSKU 1.0.0.1 |
| 调用方式 | `POST /v1/jobs`（上传）→ `GET /v1/jobs/{job_id}`（轮询）→ `GET /v1/documents/{document_id}` + `/chunks`（取结果）|
| 上传 | `POST /v1/jobs {source_type:file, file_name, sku_profiles}` → `POST /v1/jobs/{job_id}/confirm-upload` |
| 结果包 | 下载 ZIP：`full.md` + `sku_results.json` + `chunks.json` |

## 2. 真实解析样例（2026-08-06 用户提供）

**样例文档**：锦川市清岳区政务服务中心 2025 年度信息化设备采购项目资料卷宗目录。

### 2.1 匹配模型

`audit/历史档案类/卷宗` —— 即 `audit_document_traces.ontosku_template` 应存的值。

### 2.2 提取字段（两层结构）

**① 模板提取字段（样例显示 37 个）**：

| 字段 | 值 | 类型 | 溯源 |
|---|---|---|---|
| title | 锦川市清岳区政务服务中心2025年度信息化设备采购项目资料卷宗目录 | string | 🔍 |
| outline | - 001_项目资料卷宗目录_2025-02-18.pdf ... | string | 🔍 |
| summary | 该文档为...资料卷宗目录，包含采购计划、预算指标、三批采购需求及审批... | string | 🔍 |
| key_entities | 锦川市清岳区政务服务中心,林晓岚,陈启航,... | string | 🔍 |
| document_type | 档案目录 | string | 🔍 |
| name | 项目资料卷宗目录 | string | 🔍 |
| description | 归集...立项、预算、采购、合同、履约、验收和财务资料。 | string | 🔍 |
| type | 卷宗目录 | string | 🔍 |
| 文档标题 | 项目资料卷宗目录 | string | 🔍 |
| 文档编号 | QYZW-CG-2025-001 | string | 🔍 |
| 涉及单位 | 锦川市清岳区政务服务中心 | string | 🔍 |
| …（其余字段按模板 output.fields） | … | … | 🔍 |

**② `_document_overview` 概览字段（5 个）**：title / outline / summary / key_entities / document_type —— 文档级概览，独立于模板字段。

### 2.3 溯源概念

- 每个提取字段带 🔍 溯源标记 → 溯源报告含「中间文件 & 耗时」。
- 字段级溯源是 OntoSKU 的核心能力（下游用 chunks 定位原文）。
- 中间文件与耗时：`full.md` / `sku_results.json` / `chunks.json` 的产物 + 各阶段耗时（供审计工坊 `audit_agent_traces`/`trace` 参考）。

## 3. 契约要点（对审计工坊的落地）

| OntoSKU 概念 | 审计工坊落点 |
|---|---|
| 匹配模型 `audit/历史档案类/卷宗` | `audit_document_traces.ontosku_template` |
| 模板提取字段（中文名） | `field_mapper.map_extracted_fields()` → data_* 表 / `extra_fields` |
| 字段溯源 🔍 | `audit_field_sources`（字段→chunk）+ `audit_source_refs`（结论→证据） |
| `_document_overview` | 文档级摘要，可存 `data_general.summary` 或 trace 扩展 |
| `document_id` / `job_id` | `audit_document_traces.external_document_id` / `external_job_id` |
| `full.md` | `audit_document_traces.ocr_content` |
| `chunks.json` | `audit_document_chunks` 逐条落库 |

## 4. 剩余待确认（影响 Phase 4 细节，不阻塞 Phase 3 开工）

1. **chunks 原始 JSON 结构**：样例为 UI 提取结果，未含 `chunks.json` 的原始字段（chunk_id / page_nums / bbox / type / text 的具体格式）。`ontosku_client.py` 已按 `{chunk_id, page_nums, bbox, type, text}` 解析，需拿一份真实 `chunks.json` 核对字段名。
2. **页图对齐**：页面图像接口 1 起始页码与 chunk 的 `page_nums` 对齐关系，需一份真实响应核对。
3. **降级路径**：LiteParse 无 bbox/page_nums，降级时溯源字段为空——需统一"无页码不伪造"的展示规则。

> 以上 1/2 项由用户在 Phase 3 前提供一份真实 `chunks.json`（或允许访问 192.168.3.189 时抓取）核对；否则按 `ontosku_client.py` 现有解析实现，联调时校准。

## 5. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-08-06 | 由用户提供真实解析样例 + ontosku_client.py 代码知识整理 |
