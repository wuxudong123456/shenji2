-- Phase 6.2 — 审计案例库 DDL
-- 数据库: tt
CREATE TABLE IF NOT EXISTS tt.audit_cases (
    id              INT AUTO_INCREMENT PRIMARY KEY COMMENT '案例ID',
    title           VARCHAR(500) NOT NULL              COMMENT '案例标题',
    domain          VARCHAR(100)                       COMMENT '审计领域',
    case_summary    TEXT                               COMMENT '案情摘要',
    audit_method    TEXT                               COMMENT '审计方法/核查手段',
    involved_amount DECIMAL(20,2)                      COMMENT '涉及金额',
    audit_finding   TEXT                               COMMENT '审计发现/结论',
    audit_impact    TEXT                               COMMENT '审计影响/处理结果',
    source          VARCHAR(500)                       COMMENT '案例来源',
    created_at      DATETIME DEFAULT NOW(),
    INDEX idx_domain (domain),
    INDEX idx_title (title(100))
) COMMENT '审计案例库';

-- 案例 ↔ 违规关联
CREATE TABLE IF NOT EXISTS tt.audit_case_violations (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    case_id      INT NOT NULL,
    violation_id INT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES audit_cases(id) ON DELETE CASCADE,
    FOREIGN KEY (violation_id) REFERENCES audit_violations(id) ON DELETE CASCADE,
    UNIQUE KEY uk_case_violation (case_id, violation_id)
) COMMENT '案例↔违规关联';

-- 案例 ↔ 法规关联
CREATE TABLE IF NOT EXISTS tt.audit_case_law_refs (
    id      INT AUTO_INCREMENT PRIMARY KEY,
    case_id INT NOT NULL,
    law_id  VARCHAR(32) NOT NULL COMMENT 'sys_core_law_allaudit.id',
    FOREIGN KEY (case_id) REFERENCES audit_cases(id) ON DELETE CASCADE,
    INDEX idx_law (law_id)
) COMMENT '案例↔法规关联';
