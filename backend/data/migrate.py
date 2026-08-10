#!/usr/bin/env python
"""AuditWorkbench 数据库迁移脚本（幂等）

每个步骤先查 information_schema 确认当前 schema 状态，已应用则跳过——
重复执行安全。连接配置从 .env 的 MYSQL_* 读取（与后端 services.db 共用）。

用法（在仓库根目录运行）:
    python backend/data/migrate.py

设计说明:
  - MySQL 不支持 ALTER TABLE ADD COLUMN IF NOT EXISTS（那是 MariaDB 语法），
    所以用 information_schema 预检实现幂等。
  - 不在应用启动时自动跑 DDL——审计系统对生产库结构变更应留痕、由人触发，
    开发期可随时手动执行本脚本。
"""
import os
import sys

# 把 backend/ 加入 sys.path，以便复用 services.db 的连接池（连接配置统一）
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from services.db import query, execute  # noqa: E402

DATABASE = "tt"


# ── 幂等预检 ──

def _table_exists(table: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s LIMIT 1",
        (DATABASE, table), database=DATABASE,
    )
    return len(rows) > 0


def _column_exists(table: str, column: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s LIMIT 1",
        (DATABASE, table, column), database=DATABASE,
    )
    return len(rows) > 0


def _index_exists(table: str, index: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s AND index_name = %s LIMIT 1",
        (DATABASE, table, index), database=DATABASE,
    )
    return len(rows) > 0


# ── 迁移步骤 ──

def migrate_trace_md5():
    """Q1.4 — audit_document_traces 增加 file_md5 列 + 索引（上传去重校验）"""
    table = "audit_document_traces"

    if not _column_exists(table, "file_md5"):
        execute(
            f"ALTER TABLE {DATABASE}.{table} "
            "ADD COLUMN file_md5 VARCHAR(32) DEFAULT NULL "
            "COMMENT '文件MD5（去重校验）' AFTER file_name",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.file_md5 列")
    else:
        print(f"[migrate] = {table}.file_md5 已存在，跳过")

    if not _index_exists(table, "idx_project_md5"):
        execute(
            f"ALTER TABLE {DATABASE}.{table} "
            "ADD INDEX idx_project_md5 (project_id, file_md5)",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.idx_project_md5 索引")
    else:
        print(f"[migrate] = {table}.idx_project_md5 已存在，跳过")


def migrate_expression_sql():
    """Q2.2 — 聚合表达式 SQL 缓存表（LLM 生成 + 人工确认）"""
    table = "audit_expression_sql"
    if _table_exists(table):
        print(f"[migrate] = 表 {table} 已存在，跳过")
        return

    execute(f"""CREATE TABLE {DATABASE}.{table} (
        id               INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
        expression_text  TEXT NOT NULL                  COMMENT '原始违规表达式（伪SQL）',
        expression_hash  CHAR(64) NOT NULL              COMMENT '表达式SHA256（快速查重）',
        generated_sql    TEXT                           COMMENT 'LLM生成的MySQL SQL',
        target_table     VARCHAR(100)                   COMMENT '目标数据表',
        review_status    VARCHAR(20) DEFAULT 'pending'  COMMENT 'pending/approved/rejected/disabled',
        reviewed_by      VARCHAR(64)                    COMMENT '审核人',
        reviewed_at      DATETIME                       COMMENT '审核时间',
        last_executed_at DATETIME                       COMMENT '最后执行时间',
        hit_count        INT DEFAULT 0                  COMMENT '累计命中次数',
        error_msg        TEXT                           COMMENT '执行错误记录',
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_hash (expression_hash),
        INDEX idx_status (review_status),
        INDEX idx_table (target_table)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='聚合表达式SQL缓存（LLM生成+人工确认）'""", database=DATABASE)
    print(f"[migrate] + 表 {table}")


def migrate_project_context_columns():
    """Phase1 — audit_projects 增加立项业务字段（项目上下文持久化）

    让"被审计单位/审计类型/层级/编号/目标"等立项信息真正落库，
    供 /api/audit/analysis 按 project_id 读取注入 Agent 上下文。
    幂等：每列/索引先查 information_schema，已存在则跳过。
    """
    table = "audit_projects"
    columns = [
        ("project_code", "VARCHAR(64) DEFAULT NULL COMMENT '项目编号（如审通〔2026〕001号）'"),
        ("audited_unit", "VARCHAR(128) DEFAULT NULL COMMENT '被审计单位'"),
        ("audit_type", "VARCHAR(32) DEFAULT NULL COMMENT '审计类型（预算执行/专项调查/经济责任等）'"),
        ("audit_method", "VARCHAR(32) DEFAULT NULL COMMENT '审计方式（就地/送达/联网）'"),
        ("target_level", "VARCHAR(16) DEFAULT NULL COMMENT '单位层级（省级/市级/县级）'"),
        ("leader", "VARCHAR(32) DEFAULT NULL COMMENT '审计组长'"),
        ("auditor", "VARCHAR(64) DEFAULT NULL COMMENT '审计员'"),
        ("objective", "TEXT DEFAULT NULL COMMENT '审计目标'"),
        ("scope", "TEXT DEFAULT NULL COMMENT '审计范围'"),
        ("amount", "DECIMAL(14,2) DEFAULT NULL COMMENT '涉及金额'"),
    ]
    for col, ddl in columns:
        if not _column_exists(table, col):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {table}.{col}")
        else:
            print(f"[migrate] = {table}.{col} 已存在，跳过")
    for idx, col in [("idx_unit", "audited_unit"), ("idx_type", "audit_type")]:
        if not _index_exists(table, idx):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD INDEX {idx} ({col})", database=DATABASE)
            print(f"[migrate] + {table}.{idx}")
        else:
            print(f"[migrate] = {table}.{idx} 已存在，跳过")


def migrate_audit_violations_columns():
    """知识工坊 — audit_violations 加 audit_procedure / required_data 两列

    import_excel.py 文档要求"前提：已执行 ALTER 加这两列"——此前纯人工、无脚本，
    新库冷启动会缺。现纳入幂等迁移（_column_exists 预检）。
    audit_procedure 存审计方法步骤(Markdown)，required_data 存所需数据(JSON)，
    DDL 与 schema.sql 文档对齐（AFTER expression_text 保持列序一致）。
    audit_violations 主表本身不在 migrate.py（更上游历史表）；若主表不存在
    （全新空库）则跳过告警，保证 migrate.py 不中断。
    """
    table = "audit_violations"
    if not _table_exists(table):
        print(f"[migrate] ! {table} 主表不存在，加列跳过（更上游 bootstrap 未建表）")
        return
    columns = [
        ("audit_procedure", "MEDIUMTEXT COMMENT '审计方法步骤（Markdown）' AFTER expression_text"),
        ("required_data", "JSON COMMENT '审计所需数据（{items:[{name,material_type,fields}]}）' AFTER audit_procedure"),
    ]
    for col, ddl in columns:
        if not _column_exists(table, col):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {table}.{col}")
        else:
            print(f"[migrate] = {table}.{col} 已存在，跳过")


def migrate_knowledge_tables():
    """知识工坊 — 4 张关联/案例表（此前靠手动跑 migrate_cases.sql /
    migrate_violation_law_refs.sql，冷启动易漏，现纳入幂等迁移）

    audit_violation_law_refs(违规↔法规，FK→audit_violations)、audit_cases(案例库)、
    audit_case_violations(案例↔违规)、audit_case_law_refs(案例↔法规)。
    DDL 与 schema.sql 文档逐字对齐；audit_violation_law_refs 含指向 audit_violations
    的 FK，主表不存在时跳过该表并告警（其余 3 表无 FK，照建）。
    """
    # audit_violation_law_refs：FK 依赖 audit_violations，先预检主表
    if not _table_exists("audit_violations"):
        print("[migrate] ! audit_violations 主表不存在，audit_violation_law_refs 跳过（FK 依赖）")
    elif _table_exists("audit_violation_law_refs"):
        print("[migrate] = 表 audit_violation_law_refs 已存在，跳过")
    else:
        execute(f"""CREATE TABLE {DATABASE}.audit_violation_law_refs (
  id            INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  violation_id  INT           NOT NULL                COMMENT '关联 audit_violations.id',
  law_id        VARCHAR(32)   CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT 'sys_core_law_allaudit.id（跨库，排序规则对齐audit_law）',
  law_title     VARCHAR(500)                          COMMENT '法规名称（冗余，方便查询）',
  clause_ref    VARCHAR(500)                          COMMENT '条款引用',
  UNIQUE KEY uk_violation_law (violation_id, law_id),
  INDEX idx_law (law_id),
  CONSTRAINT fk_vlaw_violation FOREIGN KEY (violation_id)
      REFERENCES {DATABASE}.audit_violations (id) ON DELETE CASCADE
) COMMENT '违规↔法规关联 — 从 YAML 模板 regulation JSON 字段拆解'""", database=DATABASE)
        print("[migrate] + 表 audit_violation_law_refs")

    # 其余 3 表无 FK，CREATE 幂等 + _table_exists 预检打日志
    tables = [
        (
            "audit_cases",
            f"""CREATE TABLE {DATABASE}.audit_cases (
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
) COMMENT '审计案例库'""",
        ),
        (
            "audit_case_violations",
            f"""CREATE TABLE {DATABASE}.audit_case_violations (
  id            INT  AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  case_id       INT  NOT NULL                   COMMENT '关联 audit_cases.id',
  violation_id  INT  NOT NULL                   COMMENT '关联 audit_violations.id',
  UNIQUE KEY uk_cv (case_id, violation_id),
  INDEX idx_violation (violation_id)
) COMMENT '案例↔违规关联'""",
        ),
        (
            "audit_case_law_refs",
            f"""CREATE TABLE {DATABASE}.audit_case_law_refs (
  id       INT          AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  case_id  INT          NOT NULL                COMMENT '关联 audit_cases.id',
  law_id   VARCHAR(32)  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT 'sys_core_law_allaudit.id（跨库，排序规则对齐audit_law）',
  INDEX idx_law (law_id),
  INDEX idx_case (case_id)
) COMMENT '案例↔法规关联'""",
        ),
    ]
    for table, ddl in tables:
        if _table_exists(table):
            print(f"[migrate] = 表 {table} 已存在，跳过")
            continue
        execute(ddl, database=DATABASE)
        print(f"[migrate] + 表 {table}")


def migrate_case_indexes():
    """性能加固 — 案例库查询索引（知识工坊案例列表 + 违规关联聚合提速）

    背景：Excel 导入后 audit_cases 数千行，案例列表接口的
    ORDER BY created_at DESC、GROUP_CONCAT 关联聚合变慢。
    补三类索引，幂等：先查 information_schema.statistics。
    """
    # 1. audit_cases.created_at（列表 ORDER BY created_at DESC LIMIT n）
    if not _index_exists("audit_cases", "idx_created_at"):
        execute(
            "ALTER TABLE tt.audit_cases ADD INDEX idx_created_at (created_at)",
            database=DATABASE,
        )
        print("[migrate] + audit_cases.idx_created_at 索引")
    else:
        print("[migrate] = audit_cases.idx_created_at 已存在，跳过")

    # 2. audit_case_violations.violation_id（违规→案例聚合查询 GROUP BY violation_id）
    if not _index_exists("audit_case_violations", "idx_violation"):
        execute(
            "ALTER TABLE tt.audit_case_violations ADD INDEX idx_violation (violation_id)",
            database=DATABASE,
        )
        print("[migrate] + audit_case_violations.idx_violation 索引")
    else:
        print("[migrate] = audit_case_violations.idx_violation 已存在，跳过")

    # 3. audit_case_law_refs.case_id（案例→法规关联 JOIN WHERE cl.case_id = ?）
    if not _index_exists("audit_case_law_refs", "idx_case"):
        execute(
            "ALTER TABLE tt.audit_case_law_refs ADD INDEX idx_case (case_id)",
            database=DATABASE,
        )
        print("[migrate] + audit_case_law_refs.idx_case 索引")
    else:
        print("[migrate] = audit_case_law_refs.idx_case 已存在，跳过")


def migrate_law_refs_collation():
    """性能加固 — 关联表 law_id 字符集对齐 audit_law.sys_core_law_allaudit.id

    背景：tt 库 law_id 用 utf8mb4_unicode_ci，audit_law.id 用 utf8mb4_0900_ai_ci，
    跨库 JOIN 时代码写 COLLATE utf8mb4_0900_ai_ci 转换 → 左列加 COLLATE 使
    audit_law.id 主键索引失效 → 全表扫描法规表（含大段正文）→ 冷启动 ~2s。
    把两列 COLLATE 改为 utf8mb4_0900_ai_ci 后 JOIN 可直接用主键索引。幂等。
    """
    for table in ("audit_case_law_refs", "audit_violation_law_refs"):
        rows = query(
            "SELECT COLLATION_NAME FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = 'law_id'",
            (DATABASE, table), database=DATABASE,
        )
        collation = rows[0]["COLLATION_NAME"] if rows else None
        if collation == "utf8mb4_0900_ai_ci":
            print(f"[migrate] = {table}.law_id 已是 utf8mb4_0900_ai_ci，跳过")
            continue
        execute(
            f"ALTER TABLE {DATABASE}.{table} MODIFY law_id VARCHAR(32) "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL "
            "COMMENT 'sys_core_law_allaudit.id'",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.law_id COLLATE → utf8mb4_0900_ai_ci")


def migrate_phase2_trace_columns():
    """Phase2 — audit_document_traces 增加资料空间管理列（年度/分类/桶/大小/软删）

    PHASE_2 §5 M002：6 列 + 2 索引。资料空间年度树/分类过滤/软删/manifest 对账依赖。
    幂等：每列/索引先查 information_schema，已存在则跳过。
    """
    table = "audit_document_traces"
    columns = [
        ("audit_year", "VARCHAR(4) DEFAULT NULL COMMENT '审计年度（决策12派生）'"),
        ("file_category", "VARCHAR(20) DEFAULT NULL COMMENT '一级分类 text/image/audio/video/other'"),
        ("file_subcategory", "VARCHAR(20) DEFAULT NULL COMMENT '二级分类 word/pdf/excel/txt/original/...'"),
        ("minio_bucket", "VARCHAR(80) DEFAULT NULL COMMENT '所在 bucket（audit-project-{pid}）'"),
        ("file_size", "BIGINT DEFAULT NULL COMMENT '文件字节数（manifest 对账用）'"),
        ("deleted_at", "DATETIME DEFAULT NULL COMMENT '软删时间（NULL=未删，留痕可恢复）'"),
    ]
    for col, ddl in columns:
        if not _column_exists(table, col):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {table}.{col}")
        else:
            print(f"[migrate] = {table}.{col} 已存在，跳过")
    for idx, cols in [("idx_audit_year", "audit_year"), ("idx_project_cat", "project_id, file_category")]:
        if not _index_exists(table, idx):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD INDEX {idx} ({cols})", database=DATABASE)
            print(f"[migrate] + {table}.{idx}")
        else:
            print(f"[migrate] = {table}.{idx} 已存在，跳过")


def migrate_phase3_trace_parse_columns():
    """Phase3 — audit_document_traces 增加解析技术标识列（PHASE_3 §5 M003 ①）

    5 列 + 2 索引：external_document_id/external_job_id（OntoSKU 标识）、
    parse_engine（ontosku/liteparse/local-llm）、parse_status（pending/running/done/failed）、
    parsed_at。OntoSKU 调用/降级/状态同步/重新解析依赖。幂等：列/索引先查 information_schema。
    """
    table = "audit_document_traces"
    columns = [
        ("external_document_id", "VARCHAR(100) DEFAULT NULL COMMENT 'OntoSKU document_id'"),
        ("external_job_id", "VARCHAR(100) DEFAULT NULL COMMENT 'OntoSKU job_id'"),
        ("parse_engine", "VARCHAR(50) DEFAULT NULL COMMENT '实际解析引擎 ontosku/liteparse/local-llm'"),
        ("parse_status", "VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT '解析状态 pending/running/done/failed'"),
        ("parsed_at", "DATETIME DEFAULT NULL COMMENT '解析完成时间'"),
    ]
    for col, ddl in columns:
        if not _column_exists(table, col):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {table}.{col}")
        else:
            print(f"[migrate] = {table}.{col} 已存在，跳过")
    for idx, cols in [("idx_parse_status", "parse_status"), ("idx_external_doc", "external_document_id")]:
        if not _index_exists(table, idx):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD INDEX {idx} ({cols})", database=DATABASE)
            print(f"[migrate] + {table}.{idx}")
        else:
            print(f"[migrate] = {table}.{idx} 已存在，跳过")


def migrate_phase3_task_payload():
    """Phase3 — audit_task_queue 加 payload 列（PHASE_3 §5 M003 ②）

    分离任务输入(payload)与结果(result)：payload 存入参（trace_id/minio_bucket/minio_path/
    filename/project_id/sku_profile 等），result 专存最终结果。现状 result 双向复用，P3-2 起
    worker 改读 payload（payload 优先，result 过渡兜底兼容在途任务）。幂等：列先查 information_schema。
    """
    table = "audit_task_queue"
    if not _column_exists(table, "payload"):
        execute(
            f"ALTER TABLE {DATABASE}.{table} ADD COLUMN payload JSON DEFAULT NULL "
            "COMMENT '任务输入参数（trace_id/minio_bucket/minio_path/sku_profile 等）'",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.payload")
    else:
        print(f"[migrate] = {table}.payload 已存在，跳过")


def migrate_phase4_provenance_tables():
    """Phase4 — 文档与字段溯源三张表（PHASE_4 §5 M004）

    audit_document_chunks（文档切片，含页码/坐标/原文/ocr_version/status active|superseded）、
    audit_source_refs（统一证据引用，result↔source）、audit_field_sources（结构化字段→chunk）。
    DDL 列定义逐字照搬执行包 §5；⑥/⑦ 不另设状态列——失效靠 chunk.status + ocr_version
    推导（§3.3/P4-10）。CREATE TABLE IF NOT EXISTS 天然幂等，逐表 _table_exists 预检打日志。
    """
    tables = [
        (
            "audit_document_chunks",
            f"""CREATE TABLE {DATABASE}.audit_document_chunks (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  trace_id      INT NOT NULL COMMENT '关联 audit_document_traces',
  project_id    VARCHAR(32) NOT NULL,
  chunk_id      VARCHAR(100) COMMENT 'OntoSKU chunk_id',
  chunk_type    VARCHAR(20) COMMENT 'text/image/table/page',
  page_nums     JSON COMMENT '页码列表',
  bbox          JSON COMMENT '坐标 [x0,y0,x1,y1]',
  text          LONGTEXT COMMENT '切片原文',
  section_path  VARCHAR(500) COMMENT '章节路径',
  ocr_version   INT DEFAULT 1 COMMENT '所属解析版本（重解析+1）',
  status        VARCHAR(20) DEFAULT 'active' COMMENT 'active/superseded',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_trace (trace_id), INDEX idx_project (project_id), INDEX idx_status (status)
) COMMENT '文档切片—可逐页逐段查询'""",
        ),
        (
            "audit_source_refs",
            f"""CREATE TABLE {DATABASE}.audit_source_refs (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  project_id    VARCHAR(32) NOT NULL,
  result_type   VARCHAR(30) COMMENT 'audit_item/law_recommendation/analysis_hit/suspicion/document/data_row',
  result_id     VARCHAR(64) NOT NULL,
  source_type   VARCHAR(30) COMMENT 'document_chunk/data_row/law_clause/violation/case',
  source_id     VARCHAR(64) NOT NULL,
  document_id   INT COMMENT '来源文档 trace_id（如适用）',
  file_name     VARCHAR(500),
  page_number   INT,
  bbox          JSON,
  quote         TEXT COMMENT '支撑结论的原文片段',
  relation      VARCHAR(20) DEFAULT 'supports' COMMENT 'supports/contradicts/derived_from',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_result (result_type, result_id),
  INDEX idx_project (project_id)
) COMMENT '结论证据引用—统一溯源'""",
        ),
        (
            "audit_field_sources",
            f"""CREATE TABLE {DATABASE}.audit_field_sources (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  project_id    VARCHAR(32) NOT NULL,
  table_name    VARCHAR(100) COMMENT 'data_* 表名',
  row_id        INT NOT NULL,
  field_name    VARCHAR(100) NOT NULL COMMENT '列名 或 extra_fields->$.字段名',
  chunk_id      INT COMMENT '关联 audit_document_chunks.id',
  ocr_version   INT COMMENT '所属解析版本',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_row (table_name, row_id), INDEX idx_chunk (chunk_id)
) COMMENT '结构化字段来源'""",
        ),
    ]
    for table, ddl in tables:
        if _table_exists(table):
            print(f"[migrate] = 表 {table} 已存在，跳过")
            continue
        execute(ddl, database=DATABASE)
        print(f"[migrate] + 表 {table}")


def migrate_phase5_data_tables():
    """Phase5 — 数据工坊两张新表（PHASE_5 §5 M005，决策8）

    data_procurements（采购数据表，充实字段）、data_interviews（访谈数据表，决策7 占位）。
    DDL 照搬执行包 §5；**data_interviews 补 template_name/doc_type 两列**——_insert_into_data_table
    （task_worker.py:604）硬编码七公共列（project_id/document_trace_id/template_name/doc_name/
    doc_type/extra_fields/raw_text），八表须一致否则访谈类插入报错。执行包 §5 原始 data_interviews
    DDL 漏此两列，此为依现状修正（非臆造）。逐表 _table_exists 预检幂等。
    """
    tables = [
        (
            "data_procurements",
            f"""CREATE TABLE {DATABASE}.data_procurements (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  project_id          VARCHAR(32) NOT NULL COMMENT '关联项目ID',
  document_trace_id   INT COMMENT '溯源锚点ID',
  template_name       VARCHAR(500) COMMENT 'OntoSKU模板名',
  doc_name            VARCHAR(500) COMMENT '文档名称',
  doc_type            VARCHAR(200) COMMENT '文档类型',
  procurement_method  VARCHAR(100) COMMENT '采购方式',
  subject_name        VARCHAR(500) COMMENT '采购项目名称',
  supplier            VARCHAR(500) COMMENT '供应商',
  budget_amount       DECIMAL(20,2) COMMENT '预算金额(元，决策11)',
  contract_amount     DECIMAL(20,2) COMMENT '中标/合同金额(元，决策11)',
  bid_date            DATE COMMENT '招标/开标日期',
  sign_date           DATE COMMENT '合同签订日期',
  extra_fields        JSON COMMENT '扩展字段',
  raw_text            TEXT COMMENT 'OCR原文片段',
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_project (project_id), INDEX idx_trace (document_trace_id)
) COMMENT '采购数据表（决策8确认）'""",
        ),
        (
            "data_interviews",
            f"""CREATE TABLE {DATABASE}.data_interviews (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  project_id          VARCHAR(32) NOT NULL COMMENT '关联项目ID',
  document_trace_id   INT COMMENT '溯源锚点ID',
  template_name       VARCHAR(500) COMMENT 'OntoSKU模板名',
  doc_name            VARCHAR(500) COMMENT '访谈录音/转写文件名称',
  doc_type            VARCHAR(200) COMMENT '文档类型',
  interviewee         VARCHAR(200) COMMENT '被访谈人',
  interview_date      DATE COMMENT '访谈日期',
  location            VARCHAR(200) COMMENT '访谈地点',
  transcript          LONGTEXT COMMENT '转写全文（音频转写接入后填充，决策7）',
  extra_fields        JSON COMMENT '扩展字段',
  raw_text            TEXT COMMENT '原文片段',
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_project (project_id), INDEX idx_trace (document_trace_id)
) COMMENT '访谈数据表（决策8确认，占位）'""",
        ),
    ]
    for table, ddl in tables:
        if _table_exists(table):
            print(f"[migrate] = 表 {table} 已存在，跳过")
            continue
        execute(ddl, database=DATABASE)
        print(f"[migrate] + 表 {table}")


def migrate_engine_rules():
    """Phase7 — 智能分析引擎两张映射表（PHASE_7 §5 M006，反填初始化）

    audit_engine_rules（违规模型→分析规则映射：target_table/expression/field_mapping/threshold，
    供 Phase8 Step5 确定性取 target_table，替代 audit_analyzer._detect_target_table 运行时猜表）、
    audit_item_methods（违规模型→审计方法→数据字段要求：data_requirements 由 expression 解析派生；
    method_name/method_desc 本轮留空——YAML violations[] 无对应字段，无数据源，用户确认）。
    DDL 逐字照抄执行包 §5 不增减列（audit_item_methods 无 created_at，照抄不加）。两表均逻辑关联
    audit_violations.id（不设 FK，与 audit_cases 系列一致），主表不存在时跳过告警。
    逐表 _table_exists 预检幂等。
    """
    if not _table_exists("audit_violations"):
        print("[migrate] ! audit_violations 主表不存在，audit_engine_rules/audit_item_methods 跳过")
        return
    tables = [
        (
            "audit_engine_rules",
            f"""CREATE TABLE {DATABASE}.audit_engine_rules (
  id            INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  violation_id  INT NOT NULL                COMMENT '关联 audit_violations',
  target_table  VARCHAR(100)                COMMENT '目标 data_* 表',
  expression    TEXT                        COMMENT '分析规则伪SQL（缺省引用 violation.expression_text）',
  field_mapping JSON                        COMMENT '模型字段→表字段映射（复用 field_mapper）',
  threshold     JSON                        COMMENT '阈值配置',
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_violation (violation_id)
) COMMENT '违规模型→分析规则映射（引擎执行）'""",
        ),
        (
            "audit_item_methods",
            f"""CREATE TABLE {DATABASE}.audit_item_methods (
  id                INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  violation_id      INT NOT NULL                COMMENT '关联 audit_violations',
  method_name       VARCHAR(200)               COMMENT '审计方法名称',
  method_desc       TEXT                       COMMENT '方法说明',
  data_requirements JSON                       COMMENT '数据字段要求清单',
  INDEX idx_violation (violation_id)
) COMMENT '违规模型→审计方法→数据字段要求'""",
        ),
    ]
    for table, ddl in tables:
        if _table_exists(table):
            print(f"[migrate] = 表 {table} 已存在，跳过")
            continue
        execute(ddl, database=DATABASE)
        print(f"[migrate] + 表 {table}")


def migrate_phase8_contract_tables():
    """Phase8 — 七步智能分析契约层三表 + analysis_tasks 三列（PHASE_8 §5 M008）

    执行包 §4/§110 写 `backend/data/migrations/M008_*.sql`，但项目无 migrations/ 目录、
    迁移走 migrate.py 函数式——落地为函数（偏差已在执行方案标注）。
    执行包 §5 假设 project_suspicions/audit_agent_traces 已存在只 ALTER 加 verify_status，
    但 DB 实测两表 + audit_step_summaries **全未建**——故 M008 须 CREATE 三表
    （project_suspicions 建表即含 verify_status，§5 ⑪列），非 ALTER。

    ① project_suspicions（schema.sql:387 DDL + §5 ⑪ verify_status 合并）：疑点报告 +
       五态 verify_status（MODEL_FOUND→WAIT_CONFIRM→{CONFIRMED|REJECTED|NEED_MORE_EVIDENCE}）。
    ② audit_agent_traces（schema.sql:447 DDL 逐字）：Agent 执行溯源链——本 Phase P8-11
       `_persist_trace` 落库目标表（trace_id/input/output/knowledge_sources/tool_call_records/
       llm_raw_response/duration_ms/status）。
    ③ audit_step_summaries（§5 ⑧ DDL 逐字）：七步正式总结，UNIQUE(analysis_task_id,step_no)，
       固定消息ID step-N-summary 覆盖。
    ④ audit_analysis_tasks 加 focus_item_id/analysis_target/analysis_scope（附录A §2 落库增量列）。
    逐表/列 _table_exists/_column_exists 预检幂等。
    """
    tables = [
        (
            "project_suspicions",
            f"""CREATE TABLE {DATABASE}.project_suspicions (
  id              INT           AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  project_id      VARCHAR(32)   NOT NULL                COMMENT '关联项目ID',
  analysis_id     INT                                   COMMENT '关联audit_analysis_tasks',
  violation_id    INT                                   COMMENT '关联audit_violations',
  suspicion_items JSON                                  COMMENT '疑点条目',
  evidence_chain  JSON                                  COMMENT '证据溯源链',
  status          VARCHAR(20)   DEFAULT 'draft'         COMMENT 'draft/confirmed/rejected（原状态语义保留）',
  verify_status   VARCHAR(30)   DEFAULT 'MODEL_FOUND'   COMMENT 'MODEL_FOUND/WAIT_CONFIRM/CONFIRMED/REJECTED/NEED_MORE_EVIDENCE（五态核实流转）',
  created_at      DATETIME      DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  INDEX idx_project (project_id),
  INDEX idx_analysis (analysis_id),
  INDEX idx_violation (violation_id)
) COMMENT '疑点报告（含五态核实流转）'""",
        ),
        (
            "audit_agent_traces",
            f"""CREATE TABLE {DATABASE}.audit_agent_traces (
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
) COMMENT '智能体执行溯源链'""",
        ),
        (
            "audit_step_summaries",
            f"""CREATE TABLE {DATABASE}.audit_step_summaries (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  analysis_task_id VARCHAR(64) NOT NULL                COMMENT '关联分析任务',
  step_no          TINYINT NOT NULL                    COMMENT '步骤号(1-7)',
  message_id       VARCHAR(30)                         COMMENT 'step-1-summary ... step-7-summary 固定消息ID',
  content          TEXT                                COMMENT '正式总结文本',
  structured       JSON                                COMMENT '结构化总结',
  source_refs      JSON                                COMMENT '来源引用列表',
  version          INT DEFAULT 1                       COMMENT '版本（返回修改+1覆盖）',
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_step (analysis_task_id, step_no)
) COMMENT '七步正式总结—固定消息ID覆盖'""",
        ),
    ]
    for table, ddl in tables:
        if _table_exists(table):
            print(f"[migrate] = 表 {table} 已存在，跳过")
            continue
        execute(ddl, database=DATABASE)
        print(f"[migrate] + 表 {table}")

    # ④ audit_analysis_tasks 增量列（附录A §2 落库：focus_item_id/analysis_target/analysis_scope）
    #    current_step/step_data/step 已存在（DB 实测），本 Phase 起启用 current_step 为唯一权威源
    at = "audit_analysis_tasks"
    columns = [
        ("focus_item_id", "INT COMMENT '本次聚焦的审计事项ID（audit_items.id，附录A §2 focus_item_id）' AFTER audit_item_id"),
        ("analysis_target", "VARCHAR(500) COMMENT '分析对象（附录A §2 target，audited_unit/事项核查对象）' AFTER focus_item_id"),
        ("analysis_scope", "TEXT COMMENT '分析边界（附录A §2 scope）' AFTER analysis_target"),
    ]
    for col, ddl in columns:
        if not _column_exists(at, col):
            execute(f"ALTER TABLE {DATABASE}.{at} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {at}.{col}")
        else:
            print(f"[migrate] = {at}.{col} 已存在，跳过")


def main():
    print(f"[migrate] 开始迁移，目标库: {DATABASE}")
    try:
        migrate_trace_md5()
        migrate_expression_sql()
        migrate_project_context_columns()
        migrate_audit_violations_columns()
        migrate_knowledge_tables()
        migrate_case_indexes()
        migrate_law_refs_collation()
        migrate_phase2_trace_columns()
        migrate_phase3_trace_parse_columns()
        migrate_phase3_task_payload()
        migrate_phase4_provenance_tables()
        migrate_phase5_data_tables()
        migrate_engine_rules()
        migrate_phase8_contract_tables()
    except Exception as e:
        print(f"[migrate] X 迁移失败: {e}")
        raise
    print("[migrate] DONE 迁移完成")


if __name__ == "__main__":
    main()
