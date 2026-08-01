-- Q1.4 — audit_document_traces 表增加 file_md5 列（去重用）
-- 数据库: tt
ALTER TABLE tt.audit_document_traces
    ADD COLUMN file_md5 VARCHAR(32) DEFAULT NULL COMMENT '文件MD5（去重校验）' AFTER file_name,
    ADD INDEX idx_project_md5 (project_id, file_md5);
