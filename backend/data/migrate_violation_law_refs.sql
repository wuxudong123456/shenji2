-- Phase 3 Schema 同步 — 违规↔法规关联表
-- 数据库: tt
-- 来源: DESIGN.md §2.3 audit_violation_law_refs
CREATE TABLE IF NOT EXISTS tt.audit_violation_law_refs (
    id           INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    violation_id INT NOT NULL                        COMMENT '关联 audit_violations.id',
    law_id       VARCHAR(32) NOT NULL                COMMENT '关联 sys_core_law_allaudit.id',
    law_title    VARCHAR(500)                        COMMENT '法规名称（冗余，方便查询）',
    clause_ref   VARCHAR(500)                        COMMENT '条款引用',
    FOREIGN KEY (violation_id) REFERENCES audit_violations(id) ON DELETE CASCADE,
    INDEX idx_law (law_id),
    UNIQUE KEY uk_violation_law (violation_id, law_id)
) COMMENT '违规↔法规关联 — 从 YAML 模板 regulation JSON 字段拆解';
