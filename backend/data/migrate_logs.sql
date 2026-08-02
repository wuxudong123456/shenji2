-- 日志表
CREATE TABLE IF NOT EXISTS tt.audit_logs (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '日志ID',
    log_type    VARCHAR(20)                       COMMENT 'request/operation/llm_call/db_write/trace',
    action      VARCHAR(100)                      COMMENT '动作描述',
    user        VARCHAR(64)                       COMMENT '操作人',
    target_type VARCHAR(50)                       COMMENT '对象类型(project/violation/law)',
    target_id   VARCHAR(64)                       COMMENT '对象ID',
    ip_address  VARCHAR(45)                       COMMENT 'IP地址',
    detail      JSON                              COMMENT '详情(参数/响应/前后值/prompt+response)',
    duration_ms INT                               COMMENT '耗时(毫秒)',
    created_at  DATETIME DEFAULT NOW()            COMMENT '记录时间',
    INDEX idx_type (log_type),
    INDEX idx_target (target_type, target_id),
    INDEX idx_time (created_at)
) COMMENT '统一审计日志表';
