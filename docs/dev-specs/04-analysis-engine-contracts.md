# 04 — 智能分析引擎接口 JSON Schema 契约（开发规格）

> 文档类型：开发规格（Spec），供后端/前端开发对照实现
> 上游依据：《审计工坊智能审计系统开发方案.md》第四章 4.5.1-4.5.4
> 关联文档：`02-api-routes.md`（HTTP 端点层）、`01-agent-base.md`（Agent 输入输出）
> 本规格为七步引擎的**数据契约**，与 HTTP 端点（传输层）分离；字段命名与现有前端 DTO / LangGraph state 一致
> 版本：v1（2026-08-05，随开发迭代升版，不反写主方案）

---

## 1. 通用约定

- 每个步骤契约由三部分组成：**输入 / 输出 / 控制检查**。
- 输入一律由服务端从 DB 装配（`AnalysisContextBuilder`），前端不直接传完整对象，只传 `task_id + 已确认 ID + 页面操作`。
- 输出必须携带 `source_refs`（统一证据引用，对应 `audit_source_refs`）；无来源的条目标记 `confirm_status: "待人工核实"`。
- 步骤推进：前端只调用「确认并进入下一步」，由工作流驱动，禁止自然语言关键词驱动。
- 业务字段用中文键名，标识字段用英文；所有必填字段用 `required` 声明，`[]` 表示可能为空数组。

## 2. Step1 审计意图确认 → analysis_task（对应方案 4.5.1）

**输入 `POST /api/audit/analysis` body：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["project_id"],
  "properties": {
    "project_id": { "type": "string", "description": "项目ID（必须已落库）" },
    "focus_item_id": { "type": "integer", "description": "本次聚焦的已确认审计事项ID（可选，缺省取第一个事项）" },
    "user_intent": { "type": "string", "description": "用户补充的审计意图描述（可选）" }
  }
}
```

**输出（结构化分析任务 analysis_task）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["task_id", "project_id", "project_context", "focus_item", "current_step"],
  "properties": {
    "task_id": { "type": "string", "description": "分析任务ID（= audit_analysis_tasks.id）" },
    "project_id": { "type": "string" },
    "project_context": {
      "type": "object",
      "description": "仅来自 DB 项目记录，AI 不得重写",
      "required": ["name", "audit_type", "target_level", "audit_period", "audited_unit", "objective", "scope"],
      "properties": {
        "name": { "type": "string", "description": "项目名称" },
        "audit_type": { "type": "string", "description": "审计类型" },
        "target_level": { "type": "string", "description": "单位层级" },
        "audit_period": { "type": "string", "description": "审计期间" },
        "audited_unit": { "type": "string", "description": "被审计单位" },
        "objective": { "type": "string", "description": "审计目标" },
        "scope": { "type": "string", "description": "审计范围" }
      }
    },
    "focus_item": {
      "type": "object",
      "properties": {
        "item_id": { "type": "integer" },
        "title": { "type": "string", "description": "事项名称" },
        "category": { "type": "string" },
        "priority": { "type": "string", "enum": ["高", "中", "低"] }
      }
    },
    "audit_item": { "type": "string", "description": "本次分析事项（= focus_item.title 归一）" },
    "target": { "type": "string", "description": "分析对象（= audited_unit / 事项核查对象）" },
    "scope": { "type": "string", "description": "分析边界" },
    "current_step": { "type": "integer", "const": 1 },
    "confirmed_results": { "type": "object", "description": "各步已确认结果，逐步累积" },
    "source_refs": { "type": "array", "items": { "type": "string" }, "description": "来源引用ID列表（对应 audit_source_refs）" }
  }
}
```

落库：`audit_analysis_tasks` 增量列 `focus_item_id / analysis_target / analysis_scope`。

## 3. Step2 方法推荐（对应方案 4.5.2）

**输入：** `analysis_task`（Step1 输出）+ 已确认对象范围。

**输出（违规模型候选 + 映射链）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["violation_candidates", "confirmation_status"],
  "properties": {
    "violation_candidates": {
      "type": "array",
      "description": "按项目类型/目标/对象/范围检索违规库得到的候选，按 match_score 降序",
      "items": {
        "type": "object",
        "required": ["violation_id", "violation_title", "match_score", "engine_rule"],
        "properties": {
          "violation_id": { "type": "integer" },
          "violation_code": { "type": "string" },
          "violation_title": { "type": "string" },
          "severity": { "type": "string", "enum": ["high", "medium", "low"] },
          "match_score": { "type": "number", "minimum": 0, "maximum": 1, "description": "匹配度（规则排序，非 AI 自由打分）" },
          "match_reason": { "type": "string", "description": "匹配依据：命中项目类型/目标/对象/范围的具体项" },
          "engine_rule": {
            "type": "object",
            "description": "来自 audit_engine_rules 的映射链",
            "properties": {
              "rule_id": { "type": "integer" },
              "target_table": { "type": "string", "enum": ["data_contracts", "data_finance", "data_legal_docs", "data_registers", "data_credentials", "data_general"] },
              "expression": { "type": "string", "description": "分析规则伪SQL（缺省取 violation.expression_text）" },
              "field_mapping": { "type": "object", "description": "模型字段→表字段映射" },
              "threshold": { "type": "object", "description": "阈值配置" }
            }
          },
          "audit_methods": {
            "type": "array",
            "description": "来自 audit_item_methods",
            "items": {
              "type": "object",
              "properties": {
                "method_name": { "type": "string" },
                "method_desc": { "type": "string" },
                "data_requirements": { "type": "array", "items": { "type": "string" }, "description": "数据字段要求清单" }
              }
            }
          },
          "source_refs": { "type": "array", "items": { "type": "object" }, "description": "来源引用（violation/case/law）" }
        }
      }
    },
    "confirmation_status": { "type": "string", "enum": ["pending", "confirmed", "rejected"] },
    "confirmed_violations": { "type": "array", "description": "人工勾选后回填 violation_id 列表" }
  }
}
```

人工确认端点：`POST /api/audit/analysis/{task_id}/confirm`（已有），body 增加 `selected_violations[]`。

## 4. Step3 法规依据确认

**输入：** `analysis_task` + `confirmed_violations` + 项目资料结构化字段 + 相关 chunks。

**输出（法规条款推荐，必须可溯源）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["law_recommendations"],
  "properties": {
    "law_recommendations": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["law_id", "law_name", "clause_id", "clause_no", "clause_text", "confirm_status"],
        "properties": {
          "law_id": { "type": "string", "description": "audit_law 法规ID" },
          "law_name": { "type": "string" },
          "potency_level": { "type": "string", "enum": ["法律", "行政法规", "部门规章", "地方性法规", "单位制度"] },
          "timeliness": { "type": "string", "enum": ["现行有效", "已废止"] },
          "clause_id": { "type": "string" },
          "clause_no": { "type": "string", "description": "条款位置，如 第4条" },
          "clause_text": { "type": "string", "description": "引用原文" },
          "apply_reason": { "type": "string", "description": "适用原因" },
          "source_refs": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "source_type": { "type": "string", "enum": ["law_clause", "document_chunk", "data_row", "violation", "case"] },
                "source_id": { "type": "string" },
                "document_id": { "type": "integer" },
                "file_name": { "type": "string" },
                "page_number": { "type": "integer" },
                "quote": { "type": "string", "description": "支撑结论的原文片段" }
              }
            }
          },
          "confirm_status": { "type": "string", "enum": ["可确认", "待人工核实"], "description": "无条款或无原文时标记待人工核实，禁止进入最终文书" }
        }
      }
    },
    "layer_advice": { "type": "string", "description": "法规层级适用建议" }
  }
}
```

## 5. Step4 资料与数据准备（对应方案 4.5.3 数据准备检查）

**输入：** `analysis_task` + Step2 已确认违规的 `data_requirements` 汇总。

**输出（分析准备度检查报告）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["ready", "checks"],
  "properties": {
    "ready": { "type": "boolean", "description": "是否满足进入 Step5 的全部条件" },
    "checks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "pass", "detail"],
        "properties": {
          "name": { "type": "string", "enum": ["文件存在", "OCR完成", "分类完成", "结构化完成", "字段完整", "trace存在"] },
          "pass": { "type": "boolean" },
          "detail": { "type": "string", "description": "如 'data_contracts 行数=0' / '缺 金额/采购方式'" }
        }
      }
    },
    "missing_items": {
      "type": "array",
      "description": "缺失资料清单，关联违规与数据要求",
      "items": {
        "type": "object",
        "properties": {
          "requirement": { "type": "string", "description": "数据字段要求" },
          "related_violation_id": { "type": "integer" },
          "check": { "type": "string" }
        }
      }
    }
  }
}
```

## 6. Step5 数据比对

**输入：** `analysis_task` + `confirmed_violations` + `audit_engine_rules` + 当前项目 data_* 结构化数据。

**输出（命中记录 + 证据引用）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["exec_results"],
  "properties": {
    "exec_results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["violation_id", "rule_id", "target_table", "total", "hits", "hit_rows"],
        "properties": {
          "violation_id": { "type": "integer" },
          "rule_id": { "type": "integer" },
          "target_table": { "type": "string" },
          "total": { "type": "integer" },
          "hits": { "type": "integer" },
          "hit_rate": { "type": "number" },
          "hit_rows": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["row_id", "evidence"],
              "properties": {
                "row_id": { "type": "integer", "description": "data_* 表主键" },
                "fields": { "type": "object", "description": "命中行字段（中文键名）" },
                "evidence": {
                  "type": "object",
                  "description": "数据行级证据，回 trace 与字段来源",
                  "properties": {
                    "table": { "type": "string" },
                    "row_id": { "type": "integer" },
                    "field_sources": {
                      "type": "array",
                      "items": {
                        "type": "object",
                        "properties": {
                          "field_name": { "type": "string" },
                          "chunk_id": { "type": "integer" },
                          "document_id": { "type": "integer" },
                          "page_number": { "type": "integer" },
                          "quote": { "type": "string" }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
```

## 7. Step6 疑点核实（对应方案 4.5.4）

**输入：** Step5 命中结果 + Step3 已确认法规 + 人工意见。

**输出（疑点候选，五态流转）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["suspicion_candidates", "overall_assessment"],
  "properties": {
    "suspicion_candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["suspicion_id", "violation_id", "title", "verify_status"],
        "properties": {
          "suspicion_id": { "type": "integer", "description": "project_suspicions.id" },
          "violation_id": { "type": "integer" },
          "title": { "type": "string" },
          "summary": { "type": "string", "description": "疑点概述（AI 语言组织，不创造事实）" },
          "verify_status": { "type": "string", "enum": ["MODEL_FOUND", "WAIT_CONFIRM", "CONFIRMED", "REJECTED", "NEED_MORE_EVIDENCE"] },
          "evidence_chain": {
            "type": "object",
            "properties": {
              "data_rows": { "type": "array", "items": { "type": "object" }, "description": "对应 Step5 命中行" },
              "law_clauses": { "type": "array", "items": { "type": "object" }, "description": "对应 Step3 法规条款" },
              "document_refs": { "type": "array", "items": { "type": "object" }, "description": "对应 chunk/页/原文" }
            }
          },
          "actions": { "type": "array", "items": { "type": "string", "enum": ["confirm", "reject", "need_more_evidence"] } }
        }
      }
    },
    "overall_assessment": { "type": "string" }
  }
}
```

**verify_status 状态流转表：**

| 当前状态 | 人工动作 | 下一状态 |
|---|---|---|
| MODEL_FOUND | 系统生成疑点候选 | WAIT_CONFIRM |
| WAIT_CONFIRM | 确认 | CONFIRMED |
| WAIT_CONFIRM | 判定不成立 | REJECTED |
| WAIT_CONFIRM | 证据不足 | NEED_MORE_EVIDENCE |
| NEED_MORE_EVIDENCE | 补充资料后重新确认 | WAIT_CONFIRM |

## 8. Step7 文书生成

**输入：** `analysis_task` + 已确认疑点 + 数据证据 + 法规条款 + 人工意见。

**输出（文书四件套，证据继承）：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["documents"],
  "properties": {
    "documents": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["doc_type", "title", "content"],
        "properties": {
          "doc_type": { "type": "string", "enum": ["取证单", "审计底稿", "审计报告初稿", "定性复核意见书"] },
          "doc_id": { "type": "string" },
          "title": { "type": "string" },
          "content": { "type": "string", "description": "AI 只做语言组织，事实来自已确认证据" },
          "source_refs": { "type": "array", "items": { "type": "object" }, "description": "继承已确认疑点的证据链，不重新自由生成来源" },
          "confirm_status": { "type": "string", "enum": ["draft", "confirmed"] }
        }
      }
    }
  }
}
```

## 9. 三道控制层检查统一契约（对应方案 4.5.3）

`GET /api/audit/analysis/{task_id}/readiness?stage=entry|data_ready|evidence_complete`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["stage", "ready", "checks"],
  "properties": {
    "stage": { "type": "string", "enum": ["entry", "data_ready", "evidence_complete"] },
    "ready": { "type": "boolean" },
    "checks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "pass", "detail", "source"],
        "properties": {
          "name": { "type": "string" },
          "pass": { "type": "boolean" },
          "detail": { "type": "string" },
          "source": { "type": "string", "description": "数据源：表名/字段，如 audit_projects.setup_stage" }
        }
      }
    }
  }
}
```

三道检查的固定检查项：

| stage | name 枚举 | source |
|---|---|---|
| entry | 项目完成 / 对象范围完成 / 事项完成 / 空间存在 / 权限正确 | `audit_projects.setup_stage`、`audit_items`、权限上下文 |
| data_ready | 文件存在 / OCR完成 / 分类完成 / 结构化完成 / 进入data_* / 字段完整 / trace存在 | `audit_document_traces`、`audit_document_chunks`、`data_*`、`audit_field_sources` |
| evidence_complete | 疑点已确认 / 数据证据存在 / 文档引用存在 / 法规存在 | `project_suspicions`、`audit_source_refs` |

## 10. HTTP 端点映射

| 步骤 | 端点 | 输入 | 输出 |
|---|---|---|---|
| Step1 | `POST /api/audit/analysis` | 第2节 输入 | analysis_task |
| Step2 | `POST /api/audit/analysis/{id}/confirm` | 第3节 输出回填 `selected_violations` | 更新后的任务状态 |
| Step3 | `POST /api/audit/analysis/{id}/confirm` | 第4节 输出回填 `selected_laws` | 更新后的任务状态 |
| Step4 | `GET /api/audit/analysis/{id}/readiness?stage=data_ready` | 第5节 | 准备度报告 |
| Step5 | `POST /api/audit/analysis/{id}/step/4`（含 uploaded_files） | 第6节 输入 | 第6节 输出 |
| Step6 | `POST /api/audit/suspicion/generate` | 第7节 输入 | 疑点候选 |
| Step7 | `POST /api/audit/documents/batch` | 第8节 输入 | 文书四件套 |

## 11. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-08-05 | 初版，由《开发方案》附录A 拆分而来 |
