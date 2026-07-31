# Government Procurement Audit Case Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, synthetic, system-aligned government procurement audit case pack that can be uploaded to and validated against AuditWorkbench.

**Architecture:** A single canonical fact model drives every source document, structured golden dataset, rule expectation, evidence chain, and audit output. Generated artifacts live under `testdata/government_procurement_full_case/`; builder and verification scripts live under `scripts/case_pack/` so every artifact is reproducible.

**Tech Stack:** JSON/CSV, bundled Python with python-docx/reportlab, bundled Node.js with `@oai/artifact-tool`, Poppler/LibreOffice renderers, SHA-256 manifest validation.

## Global Constraints

- Use only fictional entities, people, identifiers, accounts, signatures, and seals.
- Match existing `/api/audit/*` request fields and the six `data_*` table schemas.
- Use existing `audit/<类别>/<文档类型>` YAML templates; do not edit dead template code.
- Use only expression syntax currently supported by the parser; represent aggregation with precomputed register fields.
- Do not modify application runtime code or production databases.
- Store final artifacts in `testdata/government_procurement_full_case/`.
- Render and visually inspect every DOCX, PDF, and XLSX before completion.

---

### Task 1: Canonical Case Model and Directory Contract

**Files:**
- Create: `scripts/case_pack/case_facts.json`
- Create: `scripts/case_pack/validate_case_pack.py`
- Create: `testdata/government_procurement_full_case/00_README/资料包说明.md`
- Create: `testdata/government_procurement_full_case/manifest.json`

**Interfaces:**
- Consumes: Design spec and current database/template schemas.
- Produces: Stable IDs, entities, dates, amounts, expected findings, file inventory, and a validator used by all later tasks.

- [ ] Define all A/B/C business facts with stable project, procurement, contract, invoice, voucher, payment, guarantee, and trace IDs.
- [ ] Add assertions for arithmetic consistency, expected anomaly counts, unique identifiers, required directories, manifest paths, and SHA-256 values.
- [ ] Run `python scripts/case_pack/validate_case_pack.py --facts-only`; expect `FACTS_OK`.
- [ ] Generate the initial README and manifest inventory.

### Task 2: Machine-Readable Golden Dataset

**Files:**
- Create: `scripts/case_pack/build_golden_data.mjs`
- Create: `testdata/government_procurement_full_case/04_结构化基准/结构化提取金标准.xlsx`
- Create: `testdata/government_procurement_full_case/04_结构化基准/golden_dataset.json`
- Create: `testdata/government_procurement_full_case/04_结构化基准/csv/*.csv`

**Interfaces:**
- Consumes: `case_facts.json`.
- Produces: Six database-aligned tables, reconciliation formulas, expected findings, JSON and CSV baselines.

- [ ] Build sheets for the six tables plus file inventory, reconciliations, and expected findings.
- [ ] Keep amounts/dates typed and use formulas for differences, ratios, and pass/fail checks.
- [ ] Export UTF-8 CSV and golden JSON from the same facts.
- [ ] Inspect key ranges and scan formulas for spreadsheet errors.
- [ ] Render every worksheet and visually inspect for clipping or unreadable content.

### Task 3: Uploadable Source Documents

**Files:**
- Create: `scripts/case_pack/build_documents.py`
- Create: `testdata/government_procurement_full_case/01_项目立项/*`
- Create: `testdata/government_procurement_full_case/03_原始资料/A_正常采购/*`
- Create: `testdata/government_procurement_full_case/03_原始资料/B_拆分采购/*`
- Create: `testdata/government_procurement_full_case/03_原始资料/C_保证金及履约异常/*`

**Interfaces:**
- Consumes: Canonical facts and file inventory.
- Produces: Editable DOCX plus stable-page PDF source materials, each containing traceable business IDs.

- [ ] Generate project approval, procurement request, approval, inquiry/tender, award, contract, acceptance, invoice, voucher, payment, and guarantee documents.
- [ ] Apply one resolved `standard_business_brief` token set consistently.
- [ ] Add explicit “虚拟测试资料” markings without obscuring OCR fields.
- [ ] Render DOCX to PNG/PDF and inspect every page.
- [ ] Render final PDFs with Poppler and inspect every page.

### Task 4: Audit Inputs, Rules, and Expected Answers

**Files:**
- Create: `testdata/government_procurement_full_case/02_审计输入/*`
- Create: `testdata/government_procurement_full_case/05_违规规则/*`
- Create: `testdata/government_procurement_full_case/06_标准答案/*`

**Interfaces:**
- Consumes: Golden dataset and actual parser grammar.
- Produces: Paste-ready intent, confirmation inputs, executable expressions, expected scan totals/hits, and false-positive/false-negative criteria.

- [ ] Record intent analysis expectations, violation keywords, document recommendations, and regulation search targets.
- [ ] Define GP-001 through GP-005 with table, expression, expected row IDs, and evidence.
- [ ] Parse/evaluate every row-level expression against representative records using current parser functions.
- [ ] Record aggregation rule GP-001 against a precomputed register row and state the engine boundary.

### Task 5: Expected Audit Outputs

**Files:**
- Create: `testdata/government_procurement_full_case/07_预期成果/*`

**Interfaces:**
- Consumes: Expected findings and evidence mappings.
- Produces: Suspicion list, evidence-chain index, evidence request, workpaper, suspicion report, and review opinion.

- [ ] Generate editable DOCX and stable PDF outputs.
- [ ] Distinguish system suspicion from confirmed audit conclusion.
- [ ] Ensure every factual assertion links to a file and page/table row.
- [ ] Render and inspect every final page.

### Task 6: Operator Guide and Acceptance Workbook

**Files:**
- Create: `testdata/government_procurement_full_case/08_操作手册/审计工坊完整业务流程操作手册.docx`
- Create: `testdata/government_procurement_full_case/08_操作手册/审计工坊完整业务流程操作手册.pdf`
- Create: `testdata/government_procurement_full_case/09_验收记录/全流程验收记录.xlsx`

**Interfaces:**
- Consumes: Actual pages, endpoints, artifacts, and expected results.
- Produces: Step-by-step UI/API instructions and a formula-driven acceptance scorecard.

- [ ] Document prerequisites, input, UI/API action, expected response, failure signature, and diagnosis path for every workflow stage.
- [ ] Include exact project-create, upload, analysis, confirm, expression, and suspicion endpoint examples.
- [ ] Build acceptance sheets for environment, upload/OCR, classification/extraction, data reconciliation, rules, traceability, and document generation.
- [ ] Render and inspect the guide and every workbook sheet.

### Task 7: Final Manifest and Verification

**Files:**
- Modify: `testdata/government_procurement_full_case/manifest.json`
- Modify: `testdata/government_procurement_full_case/00_README/资料包说明.md`

**Interfaces:**
- Consumes: All final artifacts.
- Produces: Verified hashes, counts, template mappings, expected tables, and final validation report.

- [ ] Rebuild SHA-256 entries after all artifacts are final.
- [ ] Run the validator; expect zero missing files, arithmetic mismatches, invalid template mappings, and hash mismatches.
- [ ] Run compact PDF/DOCX/XLSX open and render checks.
- [ ] Run `git diff --check` for text files and report the read-only Git limitation if commit remains unavailable.
