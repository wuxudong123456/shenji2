# Phase 4 验收报告：文档与字段溯源（可追踪）

> 执行包：[docs/phase-exec/PHASE_4.md](phase-exec/PHASE_4.md)（唯一执行依据）
> 前置：Phase 3（解析链路）已完成验收
> 验收日期：2026-08-08

## 1. 目标回顾

建立「可追踪」五层链：结构化字段 → data_* 行(document_trace_id) → 字段来源(audit_field_sources) → 文档切片(audit_document_chunks: 页码/坐标/原文) → 统一证据引用(audit_source_refs)。重解析后旧证据标 `superseded`，查询时推导「待复核」留痕不删；降级路径（LiteParse/LLM）无页码时「不伪造」，标待人工核实。

## 2. 落地内容（按切片）

| 切片 | 任务 | 产物 | commit |
|---|---|---|---|
| 0 | M004 DDL | 3 张溯源表迁移 `migrate_phase4_provenance_tables()` | cb140d7 |
| 1 | P4-2 chunk 归一化 | `_normalize_chunks` 纯函数（防御性键名 + metadata 下钻） | 7578eac |
| 2 | P4-3/4/10 chunk 落库+双写+失效 | `_persist_chunks`（superseded 幂等 + position_anchor 双写） | d79f769 |
| 3 | P4-7 EvidenceService | `evidence_service.py`（add_ref/get_refs/link_data_row_to_document） | 4b53aab |
| 4 | P4-5/6 字段匹配+行→trace | `_build_field_sources`（文本匹配兜底 + 行级证据引用） | 40976c3 |
| 5 | P4-8 trace 查询接口 | `GET /api/audit/traces/<type>/<id>`（聚合 refs+field_sources+chunks） | 2a72377 |
| 6 | 端到端 | `test_p4_trace.py` 真实 PDF 全链路 | b88e2c8 |
| 校准 | P4-2 联调 | `_normalize_chunks` 下钻 metadata 取页码/坐标（§6.2） | 14c38cb |

**P4-1 确认即过**：Phase 3 已填 `trace.external_document_id`/`external_job_id`（OntoSKU 路径稳定非空）；重解析 UPDATE 覆盖写 = §6.1 允许的「记新 ID」，代码审查确认锚点不丢。

## 3. §9 完成标准逐项

- [x] **数据库 M004 迁移执行成功**（三张新表），可回滚 —— `audit_document_chunks`/`audit_source_refs`/`audit_field_sources`，`CREATE TABLE IF NOT EXISTS` 逐表预检幂等，跑两次第二次全跳过。回滚 DDL 见执行包 §5。
- [x] **chunk 逐条落 `audit_document_chunks`（双写 `position_anchor` 兼容）** —— `_persist_chunks` 在 trace UPDATE 之后调用；`position_anchor` 仍由原 UPDATE 写（前端在读），新表行追加，双写验证（e2e + 单测）。
- [x] **字段→chunk 溯源（`audit_field_sources`，含 `extra_fields` 字段）** —— `row_dict` key→列名、`extra_fields` key→`extra_fields->$.字段名`（K5 覆盖未映射字段）；文本匹配兜底（决策1）。
- [x] **data_* 行 → trace → 文档链路可查（P4-6）** —— `data_*.document_trace_id` Phase 3 已全写；`_build_field_sources` 调 `link_data_row_to_document` 落 data_row→trace 引用（document_id 锚 trace）。
- [x] **`GET /traces/{result_type}/{result_id}` 返回完整溯源链（P4-8）** —— 聚合 refs + field_sources（JOIN chunks 取原文/页码/坐标/status）。
- [ ] **前端至少一处真实溯源展示（P4-9）** —— **延后至 Phase 5**（决策2）。本 Phase 保证后端接口可用 + 测试覆盖；前端整体 mock，Phase 5 统一对接避免返工。
- [x] **重解析后旧 chunks 标 `superseded`；溯源查询靠 status/版本推导过期并标待复核（留痕不删）（P4-10）** —— `_persist_chunks` 插新前废旧；查询接口动态推导 expired（chunk.status=superseded→expired=True），不批量改写 field_sources/source_refs。
- [x] **§8 验收脚本全部通过** —— 见下表测试统计。
- [x] **`05-regression-baseline.md` 回归通过（Phase 1/2/3 行为未破坏）** —— smoke baseline 失败 0；p1/p2/p3 全量回归全绿。

## 4. 测试统计

| 测试 | 结果 |
|---|---|
| `test_p4_slice1.py`（chunk 归一化纯函数，含 metadata） | PASS=37 FAIL=0 |
| `test_p4_slice2.py`（chunk 落库+双写+失效） | PASS=21 FAIL=0 |
| `test_p4_slice3.py`（EvidenceService） | PASS=23 FAIL=0 |
| `test_p4_slice4.py`（字段匹配+行→trace） | PASS=17 FAIL=0 |
| `test_p4_slice5.py`（trace 查询接口+P4-10 推导） | PASS=20 FAIL=0 |
| `test_p4_trace.py`（端到端真实 PDF） | PASS=23 FAIL=0 |
| **Phase 4 小计** | **141/141** |
| `test_p1_flow.py` | 通过 7 失败 0 |
| `test_p2_*`（7 文件） | 通过 86 失败 0 |
| `test_p3_slice3/456/7/9` | PASS=64 FAIL=0 |
| `test_p3_ocr.py`（真实 OCR e2e） | PASS=30 FAIL=0 |
| smoke baseline | 通过 7 失败 0 |

Phase 1/2/3 行为未破坏（241 + smoke 全绿），Phase 4 新增 141 全绿。

## 5. 行为变更说明（无破坏性）

- **`_run_ocr_task` 仅追加调用**：在现有 trace UPDATE **之后**追加 `_persist_chunks`，在 `_insert_into_data_table` **之后**追加 `_build_field_sources`。选路/降级/分类/现有 UPDATE 列/`_insert_into_data_table` 签名/`complete_task` 既有键全部不变（result 仅增量加 `chunks_count`）。
- **`position_anchor` 双写保留**：现状前端在读，本 Phase 只追加 `audit_document_chunks`，灰度切换前端后再移除。
- **3 张新表零侵入**：不碰任何现有表/查询。
- **reparse 路由契约不变**（Phase 3 已验）：P4-10 只在 worker 内加 superseded UPDATE。

## 6. 关键技术发现：K2 §4 OntoSKU chunks 真实结构（联调校准）

执行包 §6.2 明确「联调时用真实 chunks.json 校准字段名（K2 §4）」。本 Phase 端到端真实 PDF 联调得到 OntoSKU chunk 真实结构：

```json
{ "chunk_id": "...", "type": "text", "content": "切片原文",
  "path": "tmpXXX.pdf",                       // 源临时文件名，非章节路径
  "metadata": { "page_nums": [], "bbox": null, "tokens": [...], ... } }
```

校准结论（commit 14c38cb）：
- **页码/坐标在嵌套 `metadata` 内**（非顶层）→ `_normalize_chunks` 已下钻 `metadata` 取 `page_nums`/`bbox`（顶层键优先、metadata 兜底）。未来 OntoSKU 对分页文档返回页码时，溯源链可正确捕获页码级 provenance。
- **`content`→text** 已正确捕获（文本匹配由此命中）。
- **`path` 是源文件名**（非章节），不映射 `section_path`（避免误导）。
- **本测试 PDF 的 `metadata.page_nums` 源端即为 `[]`**（OntoSKU 未对该文档做页级分割）→ 落库 NULL，溯源展示「无精确页码，待人工核实」，**不伪造**（§3.4）。这是源端事实，非 bug。

## 7. P4-5 字段→chunk 匹配精度限制（决策1）

OntoSKU 字段级 field→chunk 溯源结构未知（K2 §4 待校准），本 Phase 用**文本包含兜底**：字段值→str→在 chunk 原文里找包含，首个命中→chunk_id；命中不到 `chunk_id=NULL`。

**已知精度限制**：
- 短值（如金额「100」）易在多个 chunk 命中 → 取首个，可能非真正来源；
- 命中不到的字段 `chunk_id=NULL`，溯源展示「待人工核实」。

精度升级路径：待 OntoSKU 字段级溯源结构明确后（§6.4 优先级①），替换为精确映射。本 Phase 文本兜底保证链路可用，不阻塞。

## 8. 不做（边界，归属后续 Phase）

- **P4-9 前端溯源展示** → Phase 5（决策2）；
- AI 结论类 `audit_source_refs`（analysis_hit/suspicion/law_recommendation）→ Phase 7/8；
- data_* 查询/筛选接口 → Phase 5；
- field_mapper 别名自动扩展 → Phase 7；
- OntoSKU field→chunk 精确溯源 → K2 结构明确后升级（本 Phase 文本兜底）。

## 9. 验收结论

Phase 4「可追踪」目标达成：结构化字段可双向溯源至文档切片（页码/坐标/原文），重解析后旧证据失效并标待复核（留痕不删），降级路径不伪造。§9 完成标准除 P4-9（前端，决策延后 Phase 5）外全部满足；Phase 1/2/3 行为零破坏。
