# 数据库迁移目录

> 用途：Phase 0 P0-8 建立。所有业务表 DDL 变更走此目录的幂等迁移脚本。
> 铁律：每张新表/新列配回滚语句；每个脚本单独 commit；不直接改 `schema.sql`（它只是参考文档，非执行脚本）。

## 命名

```
M{三位序号}_{Phase}_{描述}.sql
例：M001_phase1_project_lifecycle.sql
```

## 幂等模板

MySQL 8 无 `ADD COLUMN IF NOT EXISTS`，用存储过程做幂等（沿用 `migrate_logs.sql` 的模式）：

```sql
-- 示例：给 audit_projects 加 setup_stage（幂等）
DROP PROCEDURE IF EXISTS tt._add_setup_stage;
DELIMITER $$
CREATE PROCEDURE tt._add_setup_stage()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'tt' AND TABLE_NAME = 'audit_projects'
          AND COLUMN_NAME = 'setup_stage'
    ) THEN
        ALTER TABLE tt.audit_projects
            ADD COLUMN setup_stage VARCHAR(20) DEFAULT 'basic'
            COMMENT 'basic/target_scope/items/workspace';
    END IF;
END$$
DELIMITER ;
CALL tt._add_setup_stage();
DROP PROCEDURE IF EXISTS tt._add_setup_stage;
```

## 回滚模板

每个脚本底部附注释形式的回滚语句（开发期用，不自动执行）：

```sql
-- ROLLBACK
-- ALTER TABLE tt.audit_projects DROP COLUMN setup_stage;
```

## 执行方式

```bash
# 在 MySQL 客户端执行（需按实际主机/账号）
mysql -h 192.168.3.164 -u <user> -p tt < backend/data/migrations/M001_phase1_project_lifecycle.sql
```

## 规则

1. 每 Phase 的 DDL 独立脚本、独立 commit。
2. 脚本可重复执行（幂等），不报错。
3. 回滚 DDL 随脚本提供，但**不在生产自动执行**，由开发/运维人工操作。
4. `schema.sql` 保持为参考文档，不随迁移更新（或按需手动同步注释）。
