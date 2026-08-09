-- =============================================================================
-- AuditWorkbench — 业务数据库 DDL
-- 数据库: tt (192.168.3.164:3306)
-- 状态: ✅ 全部已建表（此文件为参考文档，非执行脚本）
-- 生成时间: 2026-07-29
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. audit_projects — 审计项目
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_projects (
    id              VARCHAR(32)   NOT NULL PRIMARY KEY COMMENT '项目ID',
    name            VARCHAR(200)  NOT NULL                COMMENT '项目名称',
    description     TEXT                                  COMMENT '项目描述',
    audit_period    VARCHAR(100)                          COMMENT '审计期间',
    minio_bucket    VARCHAR(100)                          COMMENT 'MinIO bucket名称',
    status          VARCHAR(20)   DEFAULT 'draft'         COMMENT 'draft/active/completed/archived',
    creator         VARCHAR(64)                           COMMENT '创建人',
    updater         VARCHAR(64)                           COMMENT '更新者',
    create_time     DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time     DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted         BIT(1)        DEFAULT b'0'            COMMENT '是否删除'
) COMMENT '审计项目';

-- ---------------------------------------------------------------------------
-- 2. audit_document_traces — 文档溯源锚点
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_document_traces (
    id                INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id        VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    file_name         VARCHAR(500)                          COMMENT '原始文件名',
    file_md5          VARCHAR(32)                           COMMENT '文件MD5（去重校验）',
    minio_path        VARCHAR(1000)                         COMMENT 'MinIO对象路径',
    ocr_version       INT           DEFAULT 1               COMMENT 'OCR版本号',
    ocr_content       LONGTEXT                              COMMENT 'OCR/Markdown内容',
    page_number       INT                                   COMMENT '原始文档页码',
    position_anchor   TEXT                                  COMMENT '位置锚点(段落/坐标)',
    ontosku_template  VARCHAR(500)                          COMMENT '匹配的OntoSKU模板',
    extracted_fields  JSON                                  COMMENT '抽取的元数据字段',
    created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_file (file_name),
    INDEX idx_project_md5 (project_id, file_md5)
) COMMENT '文档溯源—OCR结果可追溯到原始文件页码';

-- ---------------------------------------------------------------------------
-- 3. audit_conversations — AI对话记录
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_conversations (
    id          INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    session_id  VARCHAR(100)  NOT NULL                COMMENT 'OpenSquilla session_id',
    project_id  VARCHAR(32)                           COMMENT '关联项目ID',
    page        VARCHAR(100)                          COMMENT '来源页面',
    title       VARCHAR(500)                          COMMENT '对话标题',
    created_at  DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_session (session_id),
    INDEX idx_project (project_id)
) COMMENT 'AI对话记录';

-- ---------------------------------------------------------------------------
-- 4. audit_analysis_tasks — 七步智能分析任务
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_analysis_tasks (
    id             INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id     VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    title          VARCHAR(500)                          COMMENT '分析标题',
    step           TINYINT       DEFAULT 1               COMMENT '当前步骤1-6',
    step_data      JSON                                  COMMENT '各步骤数据',
    agent_results  JSON                                  COMMENT 'Agent返回结果',
    status         VARCHAR(20)   DEFAULT 'draft'         COMMENT 'draft/in_progress/completed/cancelled',
    result         TEXT                                  COMMENT '分析结果',
    created_at     DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at     DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_project (project_id),
    INDEX idx_status (status)
) COMMENT '七步智能分析任务';

-- ---------------------------------------------------------------------------
-- 5. audit_templates — OntoSKU审计模板（1511条）
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_templates (
    id             INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    name           VARCHAR(500)  NOT NULL                COMMENT '模板标识 audit/合同协议类/买卖合同',
    domain         VARCHAR(50)                           COMMENT '领域: audit/finance/medicine/tcm/general/industry/legal',
    category       VARCHAR(100)                          COMMENT '子类: 合同协议类/财务凭证类',
    doc_type       VARCHAR(200)                          COMMENT '文档类型: 买卖合同',
    description    TEXT                                  COMMENT '模板描述',
    guideline      TEXT                                  COMMENT '提取指南',
    output_fields  JSON                                  COMMENT '输出字段定义',
    tags           JSON                                  COMMENT '标签',
    is_active      TINYINT       DEFAULT 1               COMMENT '是否启用',
    created_at     DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE INDEX name (name),
    INDEX idx_domain (domain),
    INDEX idx_category (category)
) COMMENT 'OntoSKU 审计模板';

-- ---------------------------------------------------------------------------
-- 6. audit_violations — 违规行为库
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_violations (
    id              INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    violation_code  VARCHAR(50)                           COMMENT '违规行为编码',
    violation_title TEXT                                  COMMENT '违规行为名称',
    audititem_id    VARCHAR(32)                           COMMENT '关联sys_audititem审计事项分类',
    category_path   VARCHAR(500)                          COMMENT '分类路径',
    severity        VARCHAR(20)   DEFAULT 'medium'        COMMENT 'high/medium/low',
    expression_text   TEXT                                COMMENT '违规表达式伪SQL',
    audit_procedure   MEDIUMTEXT                          COMMENT '审计方法步骤（Markdown）',
    required_data     JSON                                COMMENT '审计所需数据（{items:[{name,material_type,fields}]}）',
    description       TEXT                                COMMENT '违规描述',
    source_file     VARCHAR(255)                          COMMENT '来源文件',
    author          VARCHAR(200)                          COMMENT '来源单位',
    import_batch    VARCHAR(100)                          COMMENT '导入批次',
    is_reviewed     TINYINT       DEFAULT 0               COMMENT '是否已审核',
    review_status   VARCHAR(20)                           COMMENT '审核状态',
    creator         VARCHAR(64)                           COMMENT '创建人',
    create_time     DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time     DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    deleted         BIT(1)        DEFAULT b'0'            COMMENT '是否删除',
    INDEX idx_code (violation_code),
    INDEX idx_audititem (audititem_id),
    INDEX idx_review (is_reviewed, review_status)
) COMMENT '违规行为库—关联sys_audititem审计事项分类';

-- ===========================================================================
-- 知识工坊关联表（违规↔法规 / 案例↔违规 / 案例↔法规 三向关联桥）
-- 与违规库共同构成知识工坊数据底座，详见 docs/KNOWLEDGE_WORKSHOP_SCHEMA_DESIGN.md
-- 注: 这些表已存在于生产 tt 库，此处补全 DDL 记录（参考文档，非执行脚本）
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 6.1 audit_violation_law_refs — 违规↔法规关联
-- law_id 跨库指向 audit_law.sys_core_law_allaudit.id
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_violation_law_refs (
    id            INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    violation_id  INT           NOT NULL                COMMENT '关联 audit_violations.id',
    law_id        VARCHAR(32)   CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT 'sys_core_law_allaudit.id（跨库，排序规则对齐audit_law）',
    law_title     VARCHAR(500)                          COMMENT '法规名称（冗余，方便查询）',
    clause_ref    VARCHAR(500)                          COMMENT '条款引用',
    UNIQUE KEY uk_violation_law (violation_id, law_id),
    INDEX idx_law (law_id),
    CONSTRAINT fk_vlaw_violation FOREIGN KEY (violation_id)
        REFERENCES tt.audit_violations (id) ON DELETE CASCADE
) COMMENT '违规↔法规关联 — 从 YAML 模板 regulation JSON 字段拆解';

-- ---------------------------------------------------------------------------
-- 6.2 audit_cases — 审计案例库（知识工坊·案例 Tab）
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_cases (
    id              INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    title           VARCHAR(500)  NOT NULL                COMMENT '案例标题',
    domain          VARCHAR(100)                          COMMENT '领域（前端按此分Tab+下拉框）',
    case_summary    TEXT                                  COMMENT '案情摘要',
    audit_method    TEXT                                  COMMENT '审计方法（核查手段）',
    involved_amount DECIMAL(20,2)                         COMMENT '涉案金额',
    audit_finding   TEXT                                  COMMENT '审计发现（违规表现）',
    audit_impact    TEXT                                  COMMENT '风险影响',
    source          VARCHAR(500)                          COMMENT '来源',
    created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（列表 ORDER BY）',
    INDEX idx_domain (domain),
    INDEX idx_created_at (created_at)
) COMMENT '审计案例库';

-- ---------------------------------------------------------------------------
-- 6.3 audit_case_violations — 案例↔违规关联
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_case_violations (
    id            INT  AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    case_id       INT  NOT NULL                   COMMENT '关联 audit_cases.id',
    violation_id  INT  NOT NULL                   COMMENT '关联 audit_violations.id',
    UNIQUE KEY uk_cv (case_id, violation_id),
    INDEX idx_violation (violation_id)
) COMMENT '案例↔违规关联';

-- ---------------------------------------------------------------------------
-- 6.4 audit_case_law_refs — 案例↔法规关联
-- law_id 跨库指向 audit_law.sys_core_law_allaudit.id
-- 注: 与 violation_law_refs 不同，此表未冗余 law_title，跨库JOIN失效时无法显示名称
-- ---------------------------------------------------------------------------
CREATE TABLE tt.audit_case_law_refs (
    id       INT          AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    case_id  INT          NOT NULL                COMMENT '关联 audit_cases.id',
    law_id   VARCHAR(32)  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT 'sys_core_law_allaudit.id（跨库，排序规则对齐audit_law）',
    INDEX idx_law (law_id),
    INDEX idx_case (case_id)
) COMMENT '案例↔法规关联';

-- ---------------------------------------------------------------------------
-- 7. data_contracts — 合同协议类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_contracts (
    id                  INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id          VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id   INT                                   COMMENT '溯源锚点ID',
    template_name       VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name            VARCHAR(500)                          COMMENT '文档名称',
    doc_type            VARCHAR(200)                          COMMENT '文档类型',
    party_a             VARCHAR(500)                          COMMENT '甲方',
    party_b             VARCHAR(500)                          COMMENT '乙方',
    amount              DECIMAL(20,2)                         COMMENT '金额',
    currency            VARCHAR(10)                           COMMENT '币种',
    sign_date           DATE                                  COMMENT '签订日期',
    effective_date      DATE                                  COMMENT '生效日期',
    expiry_date         DATE                                  COMMENT '终止日期',
    contract_no         VARCHAR(200)                          COMMENT '合同编号',
    procurement_method  VARCHAR(100)                          COMMENT '采购方式',
    extra_fields        JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text            TEXT                                  COMMENT 'OCR原文片段',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_sign_date (sign_date)
) COMMENT '合同协议类';

-- ---------------------------------------------------------------------------
-- 8. data_finance — 财务凭证/票据/账簿类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_finance (
    id                INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id        VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id INT                                   COMMENT '溯源锚点ID',
    template_name     VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name          VARCHAR(500)                          COMMENT '文档名称',
    doc_type          VARCHAR(200)                          COMMENT '文档类型',
    account_name      VARCHAR(500)                          COMMENT '账户名称',
    account_no        VARCHAR(100)                          COMMENT '账号',
    debit_amount      DECIMAL(20,2)                         COMMENT '借方金额',
    credit_amount     DECIMAL(20,2)                         COMMENT '贷方金额',
    voucher_no        VARCHAR(100)                          COMMENT '凭证号',
    voucher_date      DATE                                  COMMENT '凭证日期',
    bank_name         VARCHAR(500)                          COMMENT '银行名称',
    currency          VARCHAR(10)                           COMMENT '币种',
    extra_fields      JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text          TEXT                                  COMMENT 'OCR原文片段',
    created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_voucher_date (voucher_date)
) COMMENT '财务凭证/票据/账簿类';

-- ---------------------------------------------------------------------------
-- 9. data_legal_docs — 法律文书/审查报告/规章制度类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_legal_docs (
    id                INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id        VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id INT                                   COMMENT '溯源锚点ID',
    template_name     VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name          VARCHAR(500)                          COMMENT '文档名称',
    doc_type          VARCHAR(200)                          COMMENT '文档类型',
    case_no           VARCHAR(200)                          COMMENT '案件编号',
    issuing_body      VARCHAR(500)                          COMMENT '发布机关',
    doc_date          DATE                                  COMMENT '文书日期',
    effective_date    DATE                                  COMMENT '生效日期',
    legal_basis       TEXT                                  COMMENT '法律依据',
    verdict           TEXT                                  COMMENT '判决/结论',
    extra_fields      JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text          TEXT                                  COMMENT 'OCR原文片段',
    created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_doc_date (doc_date)
) COMMENT '法律文书/审查报告/规章制度类';

-- ---------------------------------------------------------------------------
-- 10. data_registers — 登记台账/清单名册/记录留痕类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_registers (
    id                 INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id         VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id  INT                                   COMMENT '溯源锚点ID',
    template_name      VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name           VARCHAR(500)                          COMMENT '文档名称',
    doc_type           VARCHAR(200)                          COMMENT '文档类型',
    register_type      VARCHAR(200)                          COMMENT '登记类型',
    item_name          VARCHAR(500)                          COMMENT '项目名称',
    quantity           DECIMAL(20,2)                         COMMENT '数量',
    unit               VARCHAR(50)                           COMMENT '单位',
    responsible_person VARCHAR(200)                          COMMENT '责任人',
    register_date      DATE                                  COMMENT '登记日期',
    extra_fields       JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text           TEXT                                  COMMENT 'OCR原文片段',
    created_at         DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id)
) COMMENT '登记台账/清单名册/记录留痕类';

-- ---------------------------------------------------------------------------
-- 11. data_credentials — 资质证照/业务单据/影像图件类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_credentials (
    id                INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id        VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id INT                                   COMMENT '溯源锚点ID',
    template_name     VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name          VARCHAR(500)                          COMMENT '文档名称',
    doc_type          VARCHAR(200)                          COMMENT '文档类型',
    cert_type         VARCHAR(200)                          COMMENT '证照类型',
    cert_no           VARCHAR(200)                          COMMENT '证照编号',
    holder            VARCHAR(500)                          COMMENT '持有人',
    issue_date        DATE                                  COMMENT '签发日期',
    expire_date       DATE                                  COMMENT '失效日期',
    issuing_body      VARCHAR(500)                          COMMENT '签发机关',
    extra_fields      JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text          TEXT                                  COMMENT 'OCR原文片段',
    created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_expire (expire_date)
) COMMENT '资质证照/业务单据/影像图件类';

-- ---------------------------------------------------------------------------
-- 12. data_general — 其他杂项/数据表格/政策文件/历史档案类
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_general (
    id                INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id        VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id INT                                   COMMENT '溯源锚点ID',
    template_name     VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name          VARCHAR(500)                          COMMENT '文档名称',
    doc_type          VARCHAR(200)                          COMMENT '文档类型',
    category          VARCHAR(200)                          COMMENT '分类',
    title             VARCHAR(500)                          COMMENT '标题',
    summary           TEXT                                  COMMENT '摘要',
    issuing_body      VARCHAR(500)                          COMMENT '发布机关',
    doc_date          DATE                                  COMMENT '日期',
    extra_fields      JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text          TEXT                                  COMMENT 'OCR原文片段',
    created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_category (category)
) COMMENT '其他杂项/数据表格/政策文件/历史档案/数据信息类';

-- ---------------------------------------------------------------------------
-- 12b. data_procurements — 采购数据表（Phase 5 / 决策8 / M005）
-- ---------------------------------------------------------------------------
CREATE TABLE tt.data_procurements (
    id                  INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id          VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id   INT                                   COMMENT '溯源锚点ID',
    template_name       VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name            VARCHAR(500)                          COMMENT '文档名称',
    doc_type            VARCHAR(200)                          COMMENT '文档类型',
    procurement_method  VARCHAR(100)                          COMMENT '采购方式',
    subject_name        VARCHAR(500)                          COMMENT '采购项目名称',
    supplier            VARCHAR(500)                          COMMENT '供应商',
    budget_amount       DECIMAL(20,2)                         COMMENT '预算金额(元，决策11)',
    contract_amount     DECIMAL(20,2)                         COMMENT '中标/合同金额(元，决策11)',
    bid_date            DATE                                  COMMENT '招标/开标日期',
    sign_date           DATE                                  COMMENT '合同签订日期',
    extra_fields        JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text            TEXT                                  COMMENT 'OCR原文片段',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id),
    INDEX idx_sign_date (sign_date)
) COMMENT '采购数据表（决策8确认）';

-- ---------------------------------------------------------------------------
-- 12c. data_interviews — 访谈数据表（Phase 5 / 决策8 / 占位）
-- ---------------------------------------------------------------------------
-- 注：补 template_name/doc_type 两列（与 _insert_into_data_table 七公共列一致，执行包 §5 原漏）
CREATE TABLE tt.data_interviews (
    id                  INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id          VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    document_trace_id   INT                                   COMMENT '溯源锚点ID',
    template_name       VARCHAR(500)                          COMMENT 'OntoSKU模板名',
    doc_name            VARCHAR(500)                          COMMENT '访谈录音/转写文件名称',
    doc_type            VARCHAR(200)                          COMMENT '文档类型',
    interviewee         VARCHAR(200)                          COMMENT '被访谈人',
    interview_date      DATE                                  COMMENT '访谈日期',
    location            VARCHAR(200)                          COMMENT '访谈地点',
    transcript          LONGTEXT                              COMMENT '转写全文（音频转写接入后填充，决策7）',
    extra_fields        JSON                                  COMMENT '模板特有字段+未来扩展',
    raw_text            TEXT                                  COMMENT '原文片段',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_trace (document_trace_id)
) COMMENT '访谈数据表（决策8确认，占位）';

-- ---------------------------------------------------------------------------
-- 13. project_suspicions — 疑点报告
-- ---------------------------------------------------------------------------
CREATE TABLE tt.project_suspicions (
    id              INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id      VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    analysis_id     INT                                   COMMENT '关联audit_analysis_tasks',
    violation_id    INT                                   COMMENT '关联audit_violations',
    suspicion_items JSON                                  COMMENT '疑点条目',
    evidence_chain  JSON                                  COMMENT '证据溯源链',
    status          VARCHAR(20)   DEFAULT 'draft'         COMMENT 'draft/confirmed/rejected',
    created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_project (project_id),
    INDEX idx_analysis (analysis_id),
    INDEX idx_violation (violation_id)
) COMMENT '疑点报告';

-- =============================================================================
-- 附加表: audit_agents — Agent配置（DESIGN_SPEC.md §9.2）
-- =============================================================================
CREATE TABLE tt.audit_agents (
    id              INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    name            VARCHAR(100)  NOT NULL                COMMENT 'Agent 名称',
    display_name    VARCHAR(200)                          COMMENT '显示名称(中文)',
    role            TEXT                                  COMMENT '角色描述',
    system_prompt   TEXT                                  COMMENT '系统提示词',
    icon            VARCHAR(50)   DEFAULT 'bi-robot'      COMMENT 'Bootstrap图标',
    color           VARCHAR(20)   DEFAULT '#1a3a5c'       COMMENT '主题色',
    model           VARCHAR(100)  DEFAULT 'deepseek-v4-flash' COMMENT '默认模型',
    tools           JSON                                  COMMENT '绑定的MCP工具列表',
    datasets        JSON                                  COMMENT '关联的数据集',
    is_active       TINYINT       DEFAULT 1               COMMENT '是否启用',
    is_system       TINYINT       DEFAULT 0               COMMENT '是否系统预置',
    created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at      DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_name (name)
) COMMENT 'Agent配置';

-- 方案B补充字段: knowledge_base_ids — Agent 绑定的知识库标识（与 tools 分离）
-- 幂等：仅在列不存在时添加
-- 注: MySQL 8 无 ADD COLUMN IF NOT EXISTS，用存储过程做幂等
DROP PROCEDURE IF EXISTS tt._add_kb_ids_col;
DELIMITER $$
CREATE PROCEDURE tt._add_kb_ids_col()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'tt' AND TABLE_NAME = 'audit_agents'
          AND COLUMN_NAME = 'knowledge_base_ids'
    ) THEN
        ALTER TABLE tt.audit_agents
            ADD COLUMN knowledge_base_ids JSON NULL
            COMMENT '绑定的知识库标识列表（如 ["law_db","violation_db"]）' AFTER datasets;
    END IF;
END$$
DELIMITER ;
CALL tt._add_kb_ids_col();
DROP PROCEDURE IF EXISTS tt._add_kb_ids_col;

-- =============================================================================
-- 附加表: audit_agent_traces — 智能体执行溯源链（方案B §2）
-- 记录每次 Agent 执行的完整链路: 输入/输出/工具调用/知识来源/耗时/状态/上下游关联
-- =============================================================================
CREATE TABLE IF NOT EXISTS tt.audit_agent_traces (
    id                  INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    trace_id            VARCHAR(64)   NOT NULL                COMMENT '溯源唯一标识(trace-xxxx)',
    task_id             VARCHAR(64)                           COMMENT '关联分析任务(audit_analysis_tasks.id)',
    project_id          VARCHAR(32)                           COMMENT '关联审计项目',
    agent_id            VARCHAR(100)  NOT NULL                COMMENT '执行的Agent标识',
    agent_name          VARCHAR(200)                          COMMENT 'Agent显示名称',
    step                TINYINT                               COMMENT '执行步骤(1-6)',
    node_name           VARCHAR(100)                          COMMENT '工作流节点名',
    upstream_trace_ids  JSON                                  COMMENT '上游Agent的trace_id列表',
    input_summary       JSON                                  COMMENT '输入摘要(脱敏/截断)',
    output_summary      JSON                                  COMMENT '输出摘要(脱敏/截断)',
    knowledge_sources   JSON                                  COMMENT '引用的知识来源(法规/违规ID等)',
    tool_call_records   JSON                                  COMMENT '工具调用记录(工具名/参数/结果/状态/耗时)',
    llm_raw_response    JSON                                  COMMENT 'LLM原始响应(用于推理溯源)',
    validation_errors   JSON                                  COMMENT '输出校验错误',
    duration_ms         INT                                   COMMENT '总执行耗时(毫秒)',
    status              VARCHAR(20)   DEFAULT 'success'       COMMENT 'success/failed',
    error_message       TEXT                                  COMMENT '失败原因',
    model               VARCHAR(100)                          COMMENT '使用的模型',
    created_at          DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '执行时间',
    INDEX idx_trace (trace_id),
    INDEX idx_task (task_id),
    INDEX idx_project (project_id),
    INDEX idx_agent (agent_id),
    INDEX idx_created (created_at)
) COMMENT '智能体执行溯源链';

-- ---------------------------------------------------------------------------
-- 附加表: audit_items — 审计事项（立项拆分）
-- 每个项目可拆分多个可独立执行的审计事项，含完整核查指引。由
-- /api/audit/projects/split-audit-items 生成，PUT /api/audit/projects/<id>/items 落库。
-- 启动时由 register_audit_routes._ensure_audit_items_table() 幂等建表。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tt.audit_items (
    id                 INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    project_id         VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
    seq                INT           DEFAULT 0               COMMENT '展示顺序',
    title              VARCHAR(200)  NOT NULL                COMMENT '事项名称',
    subtitle           VARCHAR(500)                          COMMENT '一句话描述',
    category           VARCHAR(100)                          COMMENT '分类',
    priority           VARCHAR(20)                           COMMENT '高/中/低',
    common_problems    JSON                                  COMMENT '常见问题表现',
    required_materials JSON                                  COMMENT '审计所需资料',
    common_violations  JSON                                  COMMENT '常见违规行为',
    audit_methods      JSON                                  COMMENT '常用审计方法',
    legal_bases        JSON                                  COMMENT '审计依据',
    tasks              JSON                                  COMMENT '任务分解[{name,plan,status}]',
    create_time        DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time        DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_project (project_id)
) COMMENT '审计事项';
