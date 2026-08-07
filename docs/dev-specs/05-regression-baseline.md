# 05 — API 回归基线（开发规格）

> 用途：记录现有接口在改造前的基准行为。**每个 Phase 开发完成后跑一遍本基线**，任何失败即视为"破坏现有接口"，须修复后才能进下一 Phase。
> 用法：启动后端（`cd backend && python app.py`）后，逐条执行并核对期望响应。
> 建立：Phase 0 P0-9。随接口演化，经确认后更新基线（不能单方面改）。

## 1. 健康检查

```bash
curl -s http://127.0.0.1:5000/api/health | python -m json.tool
# 期望: {"status":"ok","minio":"127.0.0.1:9100"}

curl -s http://127.0.0.1:5000/api/ocr/health | python -m json.tool
# 期望: {"engine":"...","healthy":true}

curl -s http://127.0.0.1:5000/api/llm/health | python -m json.tool
# 期望: {"llm_available":true}
```

## 2. 项目接口（改造前行为，Phase 1 不得破坏）

```bash
# 列表
curl -s http://127.0.0.1:5000/api/audit/projects | python -m json.tool
# 期望: {"success":true,"projects":[...]}，每项含 id/name/status/audit_period 等

# 创建（Phase 1 将改为 draft + 不建 bucket，但响应字段结构保持）
curl -s -X POST http://127.0.0.1:5000/api/audit/projects -H "Content-Type: application/json" \
  -d '{"name":"回归测试项目","audit_period":"2026-01-01至2026-06-30"}' | python -m json.tool
# 期望: {"success":true,"project":{...},"bucket":"audit-project-xxxx"}

# 详情（含 audit_items）
curl -s http://127.0.0.1:5000/api/audit/projects/<PID> | python -m json.tool
# 期望: project 含旧别名 title/unit/domain/level + audit_items
```

## 3. 文件与数据接口

```bash
# 上传（会触发异步 OCR）
curl -s -X POST http://127.0.0.1:5000/api/audit/projects/<PID>/upload -F "file=@<某pdf>" | python -m json.tool
# 期望: {"success":true,"trace_id":N,"task_id":"...","ocr_status":"pending"}

# 数据表统计
curl -s http://127.0.0.1:5000/api/audit/projects/<PID>/data | python -m json.tool
# 期望: 6 张表 rows 计数

# 数据行（当前允许空 project_id 全局查询——Phase 5 改造前基线）
curl -s "http://127.0.0.1:5000/api/audit/data/data_contracts/rows?per_page=5" | python -m json.tool
# 期望: 返回全库行（Phase 5 将拆分全局浏览/项目分析两模式）
```

## 4. 知识接口

```bash
curl -s "http://127.0.0.1:5000/api/audit/knowledge/violations?q=%E6%8B%9B%E6%A0%87&per_page=5" | python -m json.tool
curl -s "http://127.0.0.1:5000/api/audit/knowledge/regulations?q=%E6%8B%9B%E6%A0%87&per_page=5" | python -m json.tool
curl -s "http://127.0.0.1:5000/api/audit/knowledge/regulation/<LAW_ID>/graph" | python -m json.tool
# 期望: 均 success:true
```

## 5. 表达式与分析接口

```bash
curl -s -X POST http://127.0.0.1:5000/api/audit/expression/execute -H "Content-Type: application/json" \
  -d '{"expression":"金额 > 1000000","table":"data_contracts","project_id":""}' | python -m json.tool
# 期望: 返回 total/hits（当前允许空 project_id）

curl -s -X POST http://127.0.0.1:5000/api/audit/analysis -H "Content-Type: application/json" \
  -d '{"intent":"审计某市教育局2026年采购合规","project_id":""}' | python -m json.tool
# 期望: 返回 task_id + matches/primary_laws（LangGraph 跑至断点）
```

## 6. 旧版接口（app.py，保持不破）

```bash
curl -s http://127.0.0.1:5000/api/projects | python -m json.tool
curl -s http://127.0.0.1:5000/api/ocr/health | python -m json.tool
curl -s "http://127.0.0.1:5000/api/templates?q=%E5%90%88%E5%90%8C" | python -m json.tool
# 期望: 均正常
```

## 7. 回归执行约定

- 每个 Phase 验收脚本跑完后再跑本基线，两条都绿才算 Phase 通过。
- 本基线是"改造前基准"；某项接口的**预期行为**被 Phase 有意识地改变（如 POST /projects 不再建 bucket）时，须在对应 PHASE_N.md 的验收里**显式标注该行为变更**，并经确认后更新本基线——不允许静默破坏。

**已知延迟**：`/api/audit/templates` 每次冷加载 1000+ YAML 模板，首次调用可能 >10s（2026-08-06 实测，冒烟脚本对模板检查放宽到 30s）。如后续改造未涉及模板加载，此项延迟视为正常基线，不判失败。
