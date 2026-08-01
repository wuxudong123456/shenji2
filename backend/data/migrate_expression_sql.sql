-- Q2.2 — 聚合表达式 SQL 缓存表
-- 存储 LLM 生成的 MySQL SQL，经人工确认后缓存复用
CREATE TABLE IF NOT EXISTS tt.audit_expression_sql (
    id              INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    expression_text TEXT NOT NULL                  COMMENT '原始违规表达式（伪SQL）',
    expression_hash CHAR(64) NOT NULL              COMMENT '表达式SHA256（快速查重）',
    generated_sql   TEXT                           COMMENT 'LLM生成的MySQL SQL',
    target_table    VARCHAR(100)                   COMMENT '目标数据表',
    review_status   VARCHAR(20) DEFAULT 'pending'  COMMENT 'pending/approved/rejected/disabled',
    reviewed_by     VARCHAR(64)                    COMMENT '审核人',
    reviewed_at     DATETIME                       COMMENT '审核时间',
    last_executed_at DATETIME                      COMMENT '最后执行时间',
    hit_count       INT DEFAULT 0                  COMMENT '累计命中次数',
    error_msg       TEXT                           COMMENT '执行错误记录',
    created_at      DATETIME DEFAULT NOW(),
    UNIQUE KEY uk_hash (expression_hash),
    INDEX idx_status (review_status),
    INDEX idx_table (target_table)
) COMMENT '聚合表达式SQL缓存（LLM生成+人工确认）';
