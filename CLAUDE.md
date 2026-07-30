# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ 首次阅读：请先阅读 AGENTS.md

[AGENTS.md](AGENTS.md) 是项目的 AI 入口文档，包含当前开发状态、架构决策、文档阅读顺序和开发任务。本文件提供基础的项目概览和技术参考。

---

## Project Overview

AuditWorkbench (审计实务工坊) is an AI-assisted audit analysis platform for Chinese government audit professionals. It's a full-stack web app: **Python/Flask backend** + **vanilla HTML/CSS/JS frontend**, backed by **MinIO** (object storage) and **MySQL**.

**当前开发阶段**: Phase 0（写开发规格文档）→ Phase 1（基础设施），详见 [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)。

## Commands

```bash
# Backend — start the Flask API server (default http://0.0.0.0:5000)
cd backend && python app.py

# Backend — install dependencies
cd backend && pip install -r requirements.txt

# LiteParse OCR server — standalone (default http://127.0.0.1:5006)
cd backend/services && python liteparse_server.py

# Frontend — serve static files (any HTTP server)
cd frontend && python -m http.server 8080

# Verify OCR engine health
curl http://localhost:5000/api/ocr/health

# Verify overall API health
curl http://localhost:5000/api/health

# Verify LLM health
curl http://localhost:5000/api/llm/health
```

There are no build steps, linters, test suites, or Docker configs yet.

## Architecture

### 总体架构（目标状态）

```
Electron Desktop → OpenSquilla 网关 (:18791) → 审计扩展层
                     ├── 6 AI Agent (LangGraph 工作流)
                     ├── /api/audit/* REST API
                     ├── MCP │ Skills │ Search │ Scheduler
                     └── 连接 MinIO + MySQL + LLM
```

**当前实际状态**：前端 UI 已完整（14 页），后端 Flask 独立运行（文件/OCR/模板/LLM 服务），前后端 API 尚未对接（前端用 mock 数据降级）。Agent 系统和工作流尚未实现。

### Backend (`backend/`)

- **`app.py`** — Single-file Flask API with route groups:
  - `/api/files/<project>/...` — upload, list, download, delete, info (MinIO-backed)
  - `/api/projects/...` — CRUD project folders (stored as MinIO directory markers)
  - `/api/ocr/parse` + `/api/ocr/health` — OCR file parsing via configured engine
  - `/api/templates/*` — template catalog, search, detail, classify, extract
  - `/api/llm/health` — LLM health check
- **`config.py`** — Reads all settings from `.env` via `python-dotenv`.
- **`services/minio_client.py`** — Singleton MinIO client wrapper. Auto-creates the configured bucket on first use.
- **`services/ocr_client.py`** — Strategy pattern: `OCREngine.get_engine()` returns `LiteParseClient` or `MinerUClient`.
- **`services/llm_client.py`** — OpenAI-compatible LLM client (`call_llm()` + `call_llm_json()`). Defaults to `deepseek-v4-flash` via local proxy.
- **`services/template_service.py`** — Loads 1000+ YAML audit templates, with search/filter/category-tree.
- **`services/extraction_service.py`** — Document auto-classification + structured field extraction + auto-classify-and-extract pipeline.
- **`services/liteparse_server.py`** — Standalone Flask app wrapping `liteparse` library for local OCR.
- **`agents/`, `workflow/`, `prompts/`** — Empty directories, reserved for multi-agent system. **Now being implemented per [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).**

### ⚠️ Dead Code — DO NOT MODIFY

These files import from a non-existent `shared.*` package (OntoSKU project legacy):

- `backend/templates/classifier.py` — Dead. Actual classification in `services/extraction_service.py`.
- `backend/templates/profile_loader.py` — Dead. Actual template loading in `services/template_service.py`.
- `backend/templates/prompt_builder.py` — Dead.

### YAML Templates (`backend/templates/profiles/audit/`)

1000+ audit document templates organized by domain/category. Each template contains:
- `output.fields[]` — structured fields to extract from documents
- `violations[]` — audit violation detection rules with pseudo-SQL expressions
- `guideline` — anti-hallucination extraction rules

### Frontend (`frontend/`)

No framework — all vanilla JS. Bootstrap Icons for icons, single CSS file.

| File | Role |
|------|------|
| `js/app.js` | Global nav framework (`AuditWorkbench`). Renders navbar, sidebar, handles theme/profile/task state. |
| `js/api.js` | REST client (`AuditAPI` + legacy `MinioAPI`). Targets `/api/audit/*` endpoints (OpenSquilla gateway). |
| `js/analysis.js` | 7-step analysis wizard (`AnalysisWizard`). Currently uses mock data. |
| `js/analysis-wiz.js` | Chat-driven AI audit assistant (`AW`). Mock data (violation DB, regulation DB). |
| `js/knowledge.js` | Knowledge Workshop (`KnowledgeWorkshop`) — three-tab: violations, regulations, cases. Mock data. |
| `js/portal.js` | Homepage dashboard. |

**Page mapping**: Each `.html` file in [frontend/](frontend/) corresponds to a feature module. Sidebar nav defined in `AuditWorkbench.nav[]`.

### Data Flow (Current)

```
Browser (localStorage) → Frontend vanilla JS → Flask API (:5000) → MinIO (:9100)
                                                    ↓
                                              OCR Engine (MinerU :5005 or LiteParse :5006)
                                                    ↓
                                              LLM (:8765 deepseek-v4-flash)
```

## Key Design Decisions

- **"Submit → Confirm → Execute" interaction model**: Every AI recommendation must be human-confirmed before proceeding. Audit conclusions carry legal responsibility.
- **Three workshops**: Knowledge (violations, regulations, cases), Data (structured data querying), Document (upload, template extraction, re-inference).
- **Government visual style**: Blue `#1a3a5c` + red `#c41e3a`. No dark mode for main UI. Minimum 14px font size.
- **Agent boundary rules**: Defined by input source (upstream vs independent), output consumer (next agent vs user), and MCP tool set (non-overlapping = clear boundary).
- **Reference-not-copy**: Agents pass IDs (`violation_id`, `law_id`), not full text. Downstream agents query complete data via MCP on demand.
- **Knowledge graph**: Three-layer architecture — Law layer (12,016 laws + 31,317 relations), Audit layer (2,195 violations + audit item tree), Case layer (three-way associations).
- **Mock data in frontend**: Currently hardcoded. Will be replaced in Phase 5 per implementation plan.

## Configuration

All config in `.env` at repo root. Key variables:
- `MINIO_*` — Object storage connection (default 127.0.0.1:9100)
- `MYSQL_*` — Database connection (configured, not yet used in code)
- `OCR_ENGINE` — `liteparse` or `mineru`
- `LLM_API_BASE` — OpenAI-compatible endpoint (default http://127.0.0.1:8765/v1)
- `LLM_MODEL` — Model name (default deepseek-v4-flash)
- `FLASK_HOST` / `FLASK_PORT` — Backend listen address

## Documentation Index

| Document | Purpose |
|----------|---------|
| [AGENTS.md](AGENTS.md) | **AI 入口文档** — 当前状态、架构决策、阅读顺序、开发任务 |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | 6 Phase 实施方案 — 任务清单、代码骨架、依赖关系、里程碑 |
| [docs/REQUIREMENTS_GAP.md](docs/REQUIREMENTS_GAP.md) | 64 项缺口清单 — 优先级、依赖、统计 |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | 需求规格说明书 — 120 项功能需求 |
| [docs/DESIGN.md](docs/DESIGN.md) | 系统设计 — 数据库 DDL、API 签名、Agent 架构、部署 |
| [docs/DESIGN_PLAN.md](docs/DESIGN_PLAN.md) | 产品设计 — 视觉规范、交互模型、溯源设计、组件规格 |
| [docs/DESIGN_THINKING.md](docs/DESIGN_THINKING.md) | 设计思路 — 三工坊理念、用户理解、设计哲学 |
