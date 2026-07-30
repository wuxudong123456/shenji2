-- Phase 5 — 后台任务队列表
-- 数据库: tt (192.168.3.164:3306)
CREATE TABLE IF NOT EXISTS tt.audit_task_queue (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '任务ID',
    task_name    VARCHAR(500) NOT NULL              COMMENT '任务名称',
    task_type    VARCHAR(50)  NOT NULL              COMMENT 'ocr / extract / analysis / export / archive',
    status       VARCHAR(20)  DEFAULT 'pending'     COMMENT 'pending / processing / completed / failed / cancelled',
    progress     INT          DEFAULT 0             COMMENT '进度百分比 0-100',
    project_id   VARCHAR(32)                        COMMENT '关联项目ID',
    result       JSON                               COMMENT '执行结果JSON',
    error_msg    TEXT                               COMMENT '错误信息',
    retry_count  INT          DEFAULT 0             COMMENT '已重试次数',
    max_retries  INT          DEFAULT 3             COMMENT '最大重试次数',
    created_at   DATETIME     DEFAULT NOW()         COMMENT '创建时间',
    started_at   DATETIME                           COMMENT '开始执行时间',
    completed_at DATETIME                           COMMENT '完成时间',
    INDEX idx_project (project_id),
    INDEX idx_status (status),
    INDEX idx_type (task_type)
) COMMENT '后台任务队列';
