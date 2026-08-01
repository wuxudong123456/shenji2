# Synthetic Procurement Audit Case Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一套 2025 年四川省区县级政务服务中心办公电脑采购审计案例，交付 45—60 份相互勾稽的 DOCX/PDF 业务文件、测试基准、证据链和完整性校验结果。

**Architecture:** 以唯一 JSON 事实台账作为所有业务数据的单一来源，由分层 Python 生成器分别渲染公文、采购、合同、履约和财务资料，再由校验器检查金额、日期、引用、PDF 文本和文件哈希。文件先在仓库 `testdata/synthetic_procurement_audit_case/` 分批生成和验证，全部通过后复制到用户指定的 `D:\清岳区政务服务中心办公电脑采购审计测试案例\`。

**Tech Stack:** Python 3、python-docx、ReportLab、openpyxl、PyMuPDF、标准库 `json/hashlib/decimal/unittest`；Codex bundled Python：`C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。

## Global Constraints

- 所有单位、人员、账号、税号、发票号、银行流水号和业务事实均为虚构。
- 发票、银行回单和记账凭证必须显著标注“模拟测试件，不得用于交易或报销”。
- 印章仅使用“模拟测试专用章”，不得仿制真实机关、企业或银行印章。
- 业务资料按批次独立成件，不以汇总件替代应分别形成的文件。
- 公文、方案、合同和业务表单同时输出 DOCX/PDF；票据类仅输出 PDF。
- PDF 为清晰电子版且文本可搜索；中文不得乱码，表格不得越界。
- 三份合同金额固定为 1,462,800 元、1,389,600 元、1,336,400 元，合计 4,188,800 元。
- 六类疑点：拆分采购、合同倒签、超范围追加及付款、发票重复入账、验收时间异常、供应商关联。
- 每项疑点至少两份独立证据，并按“事实—文件—页码—字段—法规—预期结论”追溯。
- 不修改 `backend/templates/classifier.py`、`backend/templates/profile_loader.py`、`backend/templates/prompt_builder.py` 或现有 Flask API。
- 写入 `D:\` 前必须取得目录写权限；未授权时保留已验证的仓库内暂存成果，不尝试绕过权限。

---

## File Structure

- Create: `scripts/case_pack/synthetic_procurement/facts.json` — 唯一事实台账。
- Create: `scripts/case_pack/synthetic_procurement/schemas.py` — 事实字段、金额和日期验证。
- Create: `scripts/case_pack/synthetic_procurement/renderers.py` — DOCX/PDF/XLSX 通用渲染接口。
- Create: `scripts/case_pack/synthetic_procurement/content.py` — 29 类材料的正文和表格数据构造。
- Create: `scripts/case_pack/synthetic_procurement/generate.py` — 分阶段生成入口。
- Create: `scripts/case_pack/synthetic_procurement/validate.py` — 文件、勾稽、PDF 和哈希验证。
- Create: `scripts/case_pack/synthetic_procurement/export.py` — 将已验证资料复制到 `D:\`。
- Create: `scripts/case_pack/synthetic_procurement/tests/test_facts.py` — 事实台账测试。
- Create: `scripts/case_pack/synthetic_procurement/tests/test_content.py` — 文件覆盖和证据链测试。
- Create: `scripts/case_pack/synthetic_procurement/tests/test_outputs.py` — 输出格式、PDF 文本和一致性测试。
- Create: `testdata/synthetic_procurement_audit_case/` — 仓库内可重复生成的暂存交付物。
- Create: `D:\清岳区政务服务中心办公电脑采购审计测试案例\` — 用户最终交付目录，仅在授权后写入。

### Task 1: 建立唯一事实台账与不变量测试

**Files:**
- Create: `scripts/case_pack/synthetic_procurement/facts.json`
- Create: `scripts/case_pack/synthetic_procurement/schemas.py`
- Create: `scripts/case_pack/synthetic_procurement/tests/test_facts.py`

**Interfaces:**
- Produces: `load_facts(path: Path) -> dict`、`validate_facts(facts: dict) -> list[str]`
- Consumes: 已批准设计中的项目、批次、金额、疑点和安全约束。

- [ ] **Step 1: 写失败测试，锁定合同总额与三批标识**

```python
def test_contract_total_and_batches():
    facts = load_facts(FACTS)
    assert [x["batch_id"] for x in facts["batches"]] == ["B01", "B02", "B03"]
    assert sum(x["contract_amount"] for x in facts["batches"]) == 4_188_800
    assert validate_facts(facts) == []
```

- [ ] **Step 2: 运行测试并确认因文件或函数不存在而失败**

Run: `& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest scripts.case_pack.synthetic_procurement.tests.test_facts -v`

Expected: `ERROR`，包含 `ModuleNotFoundError` 或 `FileNotFoundError`。

- [ ] **Step 3: 编写事实台账**

台账必须含 `project`、`organizations`、`people`、`suppliers`、`budget`、`batches`、`items`、`deliveries`、`acceptances`、`invoices`、`payments`、`vouchers`、`findings`、`legal_sources`。金额使用整数分或整数元，生成阶段统一使用 `Decimal`，禁止浮点计算税额。

- [ ] **Step 4: 实现事实验证**

`validate_facts` 必须检查：ID 唯一、合同总额、每批明细合计、价税合计、付款引用、序列号唯一、日期可解析、第二批 12% 追加、重复发票引用、第三批异常验收顺序、供应商关联字段及模拟标识。

- [ ] **Step 5: 运行测试**

Expected: `OK`，且所有事实验证错误列表为空。

- [ ] **Step 6: 提交**

```powershell
git add scripts/case_pack/synthetic_procurement
git commit -m "testdata: add synthetic procurement case facts"
```

### Task 2: 固化官方法规来源与疑点规则

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/facts.json`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_facts.py`

**Interfaces:**
- Produces: `legal_sources[]`，字段为 `title/doc_no/issuer/published/effective/status/url/verified_on/clauses`。
- Consumes: 四川省财政厅、国务院、财政部或中国政府采购网官方页面。

- [ ] **Step 1: 写失败测试**

```python
def test_legal_sources_are_official_and_complete():
    facts = load_facts(FACTS)
    assert len(facts["legal_sources"]) >= 4
    for source in facts["legal_sources"]:
        assert source["url"].startswith("https://")
        assert source["status"] == "有效"
        assert source["verified_on"] == "2026-08-01"
        assert source["clauses"]
```

- [ ] **Step 2: 运行测试并确认失败**

Expected: `FAIL`，指出来源数量或字段不足。

- [ ] **Step 3: 核验并写入法规元数据**

至少纳入川财规〔2023〕9号、《政府采购法》、《政府采购法实施条例》及四川省框架协议采购管理规定。拆分采购引用实施条例第二十八条；合同追加引用实施条例第六十七条相关规定。每条疑点保存精确条款号和官方 URL。

- [ ] **Step 4: 运行测试**

Expected: `OK`。

- [ ] **Step 5: 提交**

```powershell
git add scripts/case_pack/synthetic_procurement/facts.json scripts/case_pack/synthetic_procurement/tests/test_facts.py
git commit -m "docs(testdata): add official procurement legal sources"
```

### Task 3: 实现通用 DOCX、PDF、XLSX 渲染器

**Files:**
- Create: `scripts/case_pack/synthetic_procurement/renderers.py`
- Create: `scripts/case_pack/synthetic_procurement/tests/test_outputs.py`

**Interfaces:**
- Produces: `render_docx(spec: DocumentSpec, path: Path) -> None`、`render_pdf(spec: DocumentSpec, path: Path) -> None`、`render_xlsx(sheets: dict[str, list[list]], path: Path) -> None`、`extract_pdf_text(path: Path) -> str`。
- `DocumentSpec` fields: `title: str`、`metadata: dict`、`sections: list`、`tables: list`、`footer_notice: str`。

- [ ] **Step 1: 写失败测试，生成最小中文文档**

测试生成含“锦川市清岳区”“模拟测试件”的 DOCX、PDF 和 XLSX，并断言文件非空、PDF 可提取中文、DOCX 段落包含标题、XLSX 单元格值正确。

- [ ] **Step 2: 运行测试并确认失败**

Expected: `ERROR`，指出渲染接口不存在。

- [ ] **Step 3: 实现渲染器**

DOCX 使用 A4、中文字体、标题层级、页眉页脚、页码和表格重复标题行；PDF 使用支持中文的系统字体和可搜索文本；XLSX 使用冻结标题行、筛选器、合理列宽和日期/金额格式。

- [ ] **Step 4: 运行测试**

Expected: `OK`，临时输出均可读取且 PDF 文本包含中文安全标识。

- [ ] **Step 5: 提交**

```powershell
git add scripts/case_pack/synthetic_procurement/renderers.py scripts/case_pack/synthetic_procurement/tests/test_outputs.py
git commit -m "feat(testdata): add audit case document renderers"
```

### Task 4: 生成立项、预算、需求与审批材料

**Files:**
- Create: `scripts/case_pack/synthetic_procurement/content.py`
- Create: `scripts/case_pack/synthetic_procurement/generate.py`
- Create: `scripts/case_pack/synthetic_procurement/tests/test_content.py`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/01_项目立项与预算/`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/02_采购需求与审批/`

**Interfaces:**
- Produces: `build_document_specs(facts: dict, stage: str) -> list[OutputSpec]`、`generate_stage(stage: str, root: Path) -> list[Path]`。
- Consumes: Tasks 1—3 的事实台账和渲染器。

- [ ] **Step 1: 写失败测试**

断言阶段 `planning` 生成卷宗目录、情况说明、年度计划、预算通知及三批独立的需求申请和审批表；每个非票据材料同时存在 DOCX/PDF。

- [ ] **Step 2: 运行测试并确认文件数量不足而失败**

- [ ] **Step 3: 编写完整正文与表格**

年度计划必须显示同一预算项目约 440 万元；三批申请分别引用同一预算指标，设备参数、数量、单价、申请理由、经办人和审批链完整。第二批审批日期按事实台账埋入倒签证据，但正文不得标注异常。

- [ ] **Step 4: 生成并运行测试**

Run: `& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/case_pack/synthetic_procurement/generate.py --stage planning --root testdata/synthetic_procurement_audit_case`

Expected: 输出 `STAGE_OK planning`，测试 `OK`。

- [ ] **Step 5: 人工抽查两组 DOCX/PDF**

检查预算通知和第二批审批表：标题、文号、签署、金额、日期、页码、附件及安全标识完整。

- [ ] **Step 6: 提交**

提交生成器、测试和该阶段测试数据。

### Task 5: 生成三批询价、报价、资格审查与评审材料

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/content.py`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_content.py`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/03_供应商响应与评审/`

**Interfaces:**
- Produces: `stage="procurement"` 的三批询价记录、每批三家报价、资格审查和评审成交意见。

- [ ] **Step 1: 写失败测试**

断言每批均有三份独立报价函，成交价最低或评分最高的供应商与事实台账一致；关联供应商线索至少分布在联系电话、邮箱、地址、收款信息中的两个字段。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 生成采购材料**

每份报价必须含供应商抬头、报价有效期、分项价格、税率、交付期、质保期、联系人和签署页。三批评审分别形成，不得共用一份评审记录。

- [ ] **Step 4: 运行生成与测试**

Expected: `STAGE_OK procurement`，三批 × 三家报价全部存在，文档内不出现“关联”“陪标”“异常”等泄题词。

- [ ] **Step 5: 提交**

提交本阶段生成器变更、测试和输出。

### Task 6: 生成成交通知与三份完整合同

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/content.py`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_content.py`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/04_成交通知与合同/`

**Interfaces:**
- Produces: `stage="contracts"` 的三份成交通知和三份采购合同。

- [ ] **Step 1: 写失败测试**

断言三份合同编号唯一、金额精确、分项合计一致；第二批签约日期晚于指定送货日期；合同包含标的、技术参数、交付、验收、价税、付款、质保、违约和争议解决条款。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 生成成交通知与合同**

合同附件必须列出设备名称、品牌占位名称、型号、配置、数量、含税单价、税率和价税合计。不得只生成两列表格式摘要。

- [ ] **Step 4: 运行生成、计算和文本测试**

Expected: `STAGE_OK contracts`；合同合计 `4,188,800.00`；DOCX/PDF 提取文本均含核心条款。

- [ ] **Step 5: 提交**

提交本阶段变更和输出。

### Task 7: 生成送货、安装、验收与资产登记材料

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/content.py`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_content.py`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/05_供货安装与验收/`

**Interfaces:**
- Produces: `stage="fulfillment"` 的三批送货单、安装记录、验收报告和资产登记资料。

- [ ] **Step 1: 写失败测试**

断言序列号在送货、安装、验收和资产登记之间可追踪；正常设备日期顺序正确；第三批指定设备满足 `验收日期 < 送货日期或安装日期`。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 生成履约资料**

每批独立形成送货、安装和验收文件。第二批追加设备数量和金额必须与 12% 追加事实完全一致，且缺少合法补充审批文件这一“缺失证据”只在测试答案中说明。

- [ ] **Step 4: 运行生成与序列号联查测试**

Expected: `STAGE_OK fulfillment`，不存在未引用或重复使用的非预设序列号。

- [ ] **Step 5: 提交**

提交本阶段变更和输出。

### Task 8: 生成模拟发票、付款、回单与记账凭证

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/content.py`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_content.py`
- Generate: `testdata/synthetic_procurement_audit_case/01_待审项目资料/06_发票支付与会计凭证/`

**Interfaces:**
- Produces: `stage="finance"` 的逐批模拟发票 PDF、付款审批 DOCX/PDF、银行回单 PDF、记账凭证 PDF。

- [ ] **Step 1: 写失败测试**

断言第一批合同、发票、付款和回单金额一致；指定重复发票被两个凭证附件索引引用；第二批付款含 12% 追加金额；所有票据 PDF 均含完整安全水印。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 生成财务资料**

发票字段包括购买方、销售方、虚构税号、发票号码、开票日期、货物明细、规格、单位、数量、单价、金额、税率、税额、价税合计；银行回单包含付款人、收款人、虚构账号、金额、用途和交易时间；记账凭证包含科目、摘要、借贷金额、附件张数和制单复核。

- [ ] **Step 4: 运行生成和安全测试**

Expected: `STAGE_OK finance`；任一票据缺少“模拟测试件，不得用于交易或报销”即测试失败。

- [ ] **Step 5: 提交**

提交本阶段变更和输出。

### Task 9: 生成测试基准、证据链与法规清单

**Files:**
- Modify: `scripts/case_pack/synthetic_procurement/content.py`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/项目事实基准表.xlsx`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/文件证据链索引.xlsx`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/预设疑点及法规依据.docx`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/预设疑点及法规依据.pdf`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/OCR与字段提取预期结果.xlsx`
- Generate: `testdata/synthetic_procurement_audit_case/02_测试基准与答案/法规官方来源清单.pdf`

**Interfaces:**
- Produces: `stage="oracle"` 的金标准文件。

- [ ] **Step 1: 写失败测试**

断言六类疑点全部存在，每项至少两条证据，证据引用的文件实际存在，页码为正整数，字段非空，法规 URL 为官方 HTTPS 页面。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 生成五类控制材料**

OCR 基准逐文件记录 `relative_path/doc_type/page_count/expected_fields`；证据链逐行记录 `finding_id/fact/file/page/field/legal_source/expected_conclusion`；事实基准按项目、机构、人员、供应商、批次、明细、发票、付款、凭证和资产分工作表。

- [ ] **Step 4: 运行生成与引用完整性测试**

Expected: `STAGE_OK oracle`，无悬空文件引用或缺失字段。

- [ ] **Step 5: 提交**

提交控制材料和生成器变更。

### Task 10: 全量验证、清单、哈希与 AuditWorkbench 抽测

**Files:**
- Create: `scripts/case_pack/synthetic_procurement/validate.py`
- Modify: `scripts/case_pack/synthetic_procurement/tests/test_outputs.py`
- Generate: `testdata/synthetic_procurement_audit_case/README_案例使用说明.pdf`
- Generate: `testdata/synthetic_procurement_audit_case/03_完整性校验/文件清单.xlsx`
- Generate: `testdata/synthetic_procurement_audit_case/03_完整性校验/SHA256校验清单.txt`

**Interfaces:**
- Produces: `validate_pack(root: Path) -> ValidationReport`，退出码 0 表示可交付。

- [ ] **Step 1: 写失败测试**

测试临时删除一个文件或篡改一个金额后，验证器必须分别报告 `MISSING_FILE` 和 `FACT_MISMATCH`。

- [ ] **Step 2: 运行测试并确认失败**

- [ ] **Step 3: 实现全量验证器**

验证文件数量、扩展名、DOCX/PDF 配对、PDF 文本、中文乱码特征、页数、金额总计、日期规则、序列号引用、证据链、法规来源、安全水印、文件清单和 SHA256。

- [ ] **Step 4: 全量重新生成并验证**

Run: `& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/case_pack/synthetic_procurement/generate.py --stage all --root testdata/synthetic_procurement_audit_case`

Run: `& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/case_pack/synthetic_procurement/validate.py --root testdata/synthetic_procurement_audit_case`

Expected: `PACK_VALID`，错误数 0，业务文件数在 45—60 范围内。

- [ ] **Step 5: 在 AuditWorkbench 做代表性抽测**

选择合同、验收报告、模拟发票和付款审批各一份，验证上传成功、OCR/文本可读、文档分类合理、合同金额/日期/主体字段可提取。若运行环境缺少服务，记录为外部环境限制，但不得把文件级验证标记为通过。

- [ ] **Step 6: 提交**

提交验证器、测试、README、文件清单和哈希清单。

### Task 11: 授权后导出到 D 盘并做交付后校验

**Files:**
- Create: `scripts/case_pack/synthetic_procurement/export.py`
- Create: `D:\清岳区政务服务中心办公电脑采购审计测试案例\`

**Interfaces:**
- Consumes: Task 10 中 `PACK_VALID` 的仓库内资料包。
- Produces: `export_verified_pack(source: Path, destination: Path) -> None`。

- [ ] **Step 1: 写导出前置条件测试**

验证源目录若缺少 `03_完整性校验/SHA256校验清单.txt` 或验证报告非 `PACK_VALID`，导出必须拒绝执行。

- [ ] **Step 2: 请求并取得 `D:\` 写权限**

目标必须精确为 `D:\清岳区政务服务中心办公电脑采购审计测试案例\`，不得对 `D:\` 根目录执行递归删除、覆盖或清理。

- [ ] **Step 3: 执行非破坏性导出**

目标不存在时创建；目标已存在时停止并报告冲突，不自动删除或覆盖用户文件。

- [ ] **Step 4: 比较源目标哈希**

Run: `& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts/case_pack/synthetic_procurement/export.py --source testdata/synthetic_procurement_audit_case --destination 'D:\清岳区政务服务中心办公电脑采购审计测试案例'`

Expected: `EXPORT_OK`，源目标文件数和 SHA256 全部一致。

- [ ] **Step 5: 交付说明**

报告最终绝对路径、文件总数、各格式数量、验证结果、未执行的环境抽测及安全标识。导出脚本本身可提交，`D:\` 交付文件不纳入 Git。

## Final Verification

- [ ] 运行所有单元测试：`& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s scripts/case_pack/synthetic_procurement/tests -v`
- [ ] 运行 `validate.py` 并确认 `PACK_VALID`。
- [ ] 检查 Git 状态，确认未改动禁止文件和无关用户文件。
- [ ] 授权后运行导出并确认 `EXPORT_OK`。
- [ ] 从 `D:\清岳区政务服务中心办公电脑采购审计测试案例\` 随机打开至少 2 个 DOCX、4 个 PDF、2 个 XLSX。
