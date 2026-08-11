# 上线检查单（Release Checklist）— U4

> 用途：AuditWorkbench 上线前的逐项签字门禁。每一项须有负责人签字 + 证据指针，全绿方可上线。
> 来源：`docs/phase-exec/PHASE_9.md` §6.9 + 本项目实际状态。
> 配套：[ROLLBACK_PLAN.md](ROLLBACK_PLAN.md)（回滚预案，本检查单第 6 项引用）。
> 建立时间：2026-08-10｜分支：phase2

---

## 0. 前置：服务就绪

| # | 检查项 | 命令 / 证据 | 状态 | 签字 |
|---|--------|------------|:----:|:----:|
| 0.1 | 后端 API 健康 | `curl http://localhost:5000/api/health` → `{"status":"ok"}` | ☐ | |
| 0.2 | LLM 服务可达 | `curl http://localhost:5000/api/llm/health` → `{"llm_available":true}` | ☐ | |
| 0.3 | OCR 引擎健康 | `curl http://localhost:5000/api/ocr/health` → `{"healthy":true}` | ☐ | |
| 0.4 | MinIO 对象存储可达 | health 响应含 minio 地址；bucket 可读写 | ☐ | |
| 0.5 | MySQL 连接 + 迁移已应用 | `python backend/data/migrate.py` 全部「= 已存在」或「+」无报错 | ☐ | |
| 0.6 | 配置就位 | 仓库根 `.env`：MINIO_*/MYSQL_*/OCR_ENGINE/LLM_API_BASE/LLM_MODEL/FLASK_* | ☐ | |

---

## 1. 验收门（Phase 1-8 + Phase 9 T1-T8）

| # | 检查项 | 证据 | 状态 | 签字 |
|---|--------|------|:----:|:----:|
| 1.1 | Phase 1-8 验收门全过 | [TEST_REPORT_PHASE_8.md](TEST_REPORT_PHASE_8.md)：契约层 47/47 绿 + P5/P7 回归绿 | ✅ | |
| 1.2 | Phase 9 T1 全链路（真 LLM） | [TEST_REPORT_PHASE_9.md](TEST_REPORT_PHASE_9.md) §2：PASS=26/0，current_step 1→7 | ✅ | |
| 1.3 | §0 溯源穿透（铁律） | §7：PASS=10/0，source_refs 3/3 非空可解析页码 | ✅ | |
| 1.4 | T2 OCR 门禁 | §8：PASS=5/0，未完成拦/完成放 | ✅ | |
| 1.5 | T3 恢复分析 | §8：PASS=6/0，后端权威 resume | ✅ | |
| 1.6 | T4 跨项目隔离 | §10：PASS=8/0（数据/文件面 ✅；analysis 面=网关鉴权，见 §1 备注） | ✅ | |
| 1.7 | T5 金额边界 | §11：PASS=16/0，阈值无万倍误判 | ✅ | |
| 1.8 | T6 LLM 停机降级 | §12：PASS=10/0，不白屏/不 500 | ✅ | |
| 1.9 | T7 大数据扫描 | §13：PASS=20/0，10 万行游标分页+超时保护 | ✅ | |
| 1.10 | T8 并发编辑事项 | §9：PASS=15/0，乐观锁冲突提示 | ✅ | |

> **§1 备注（T4 analysis 面）**：`GET /analysis/{task_id}`、`/documents/batch`、`/suspicion/*` 按 task_id 直读，无 project 交叉校验——系统无 auth/user 模型，user→project 归属鉴权是**目标架构 OpenSquilla 网关的职责**（网关未实现）。**上线前网关须就位**或接受「task_id 即能力令牌」的现状（不可猜测，但非正式鉴权）。见 [TEST_REPORT_PHASE_9.md](TEST_REPORT_PHASE_9.md) §10.3。

---

## 2. 回归基线

| # | 检查项 | 证据 | 状态 | 签字 |
|---|--------|------|:----:|:----:|
| 2.1 | API 回归基线全量通过 | [docs/dev-specs/05-regression-baseline.md](dev-specs/05-regression-baseline.md) 逐条 curl 核对期望响应 | ☐ | |
| 2.2 | Phase 1-6 数据回归 | `python tests/test_p5_data.py` → PASS=23/0 | ✅ | |
| 2.3 | Phase 7 引擎回归 | `python tests/test_p7_rules.py` → PASS=18/0 | ✅ | |
| 2.4 | Phase 8 契约层回归 | `python tests/test_p8_seven_step.py` → PASS=47/0 | ✅ | |

---

## 3. 上线动作（U1-U3，U4=本检查单）

| # | 检查项 | 证据 | 状态 | 签字 |
|---|--------|------|:----:|:----:|
| 3.1 | U1 旧接口灰度开关 | 前端 feature flag 可切换新旧接口；新异常可切回旧 | ✅ [TEST_REPORT §17](TEST_REPORT_PHASE_9.md)：实模式/演示模式开关，集中门禁替分散 .catch，PASS=23/0（含 settings.html 静默失效预存缺陷修复） | |
| 3.2 | U2 溯源抽样验收 | 抽样 N≥10 条 AI 结论，逐条回溯到 chunk/页/原文 | ✅ [TEST_REPORT §16](TEST_REPORT_PHASE_9.md)：20 条结论 0 断链 | |
| 3.3 | U3 性能并发压测 | locust（或同等）达标：大数据表扫描 + 七步并发不超时 | ✅ [TEST_REPORT §18](TEST_REPORT_PHASE_9.md)：自写并发脚本（零新依赖），5 万行并发扫描 8 并发深度 p95<3s + 七步并发 B1 15 线程/B2 3 并发 LLM 0 超时，PASS=18/0；顺带修 POST /analysis 无 focus_item 500 缺陷 | |

---

## 4. 备份

| # | 检查项 | 命令 / 证据 | 状态 | 签字 |
|---|--------|------------|:----:|:----:|
| 4.1 | 数据库备份 | `mysqldump -u <user> -p tt > backup_tt_<date>.sql`；audit_law 法规库同理 | ☐ | |
| 4.2 | 配置备份 | 复制 `.env` → `.env.backup_<date>`；记录 LLM/OCR/MinIO 端点 | ☐ | |
| 4.3 | MinIO 数据备份（可选） | 生产bucket快照 / mc mirror 到异地 | ☐ | |
| 4.4 | 代码回滚点打标 | `git tag pre-release-<date>` 于当前 HEAD（含 Phase 9 全部 T 修复） | ☐ | |

---

## 5. 回滚预案

| # | 检查项 | 证据 | 状态 | 签字 |
|---|--------|------|:----:|:----:|
| 5.1 | 回滚预案文档就位 | [ROLLBACK_PLAN.md](ROLLBACK_PLAN.md)（DDL 回滚 + 代码回滚点 + 灰度切回） | ✅ | |
| 5.2 | 回滚演练通过 | 在**测试环境**执行 ROLLBACK_PLAN §4 演练流程，验证可回退到 pre-release | ☐ 待演练 | |

---

## 6. 上线签字

| 角色 | 姓名 | 签字 | 日期 |
|------|------|------|------|
| 开发负责人 | | | |
| 测试负责人 | | | |
| 审计业务负责人 | | | |
| 运维 / DBA | | | |

> **上线准则**：第 0-5 节全部 ✅（或「待」项有书面放行）+ 第 6 节四方签字 → 准予上线。
> **当前阻塞**（截至 2026-08-10）：U1 灰度开关（§3.1 ✅，PASS=23/0）+ U2 溯源抽样（§3.2 ✅，20 条 0 断链）+ **U3 压测**（§3.3 ✅，PASS=18/0）均已完成。剩 **网关鉴权**（§1 备注，OpenSquilla 网关职责）。**此 1 项闭环前不可正式上线**。

---

## 附：复现命令速查

```bash
cd backend && python app.py                          # 后端
python tests/test_e2e_flow.py                        # T1 全链路（真 LLM）
python tests/test_p9_t2_t3_gate_resume.py            # T2/T3 门禁+恢复
python tests/test_p9_t4_isolation.py                 # T4 跨项目隔离
python tests/test_p9_t5_amount.py                    # T5 金额边界
python tests/test_p9_t6_llm_down.py                  # T6 LLM 停机降级
python tests/test_p9_t7_large_scan.py                # T7 大数据扫描（~40s）
python tests/test_p9_t8_concurrency.py               # T8 并发编辑
python tests/test_p9_u2_provenance_sampling.py        # U2 溯源抽样（20 条结论 0 断链）
python tests/test_p9_u3_perf.py                       # U3 性能并发压测（~3-5min，真 LLM）
python tests/test_p8_seven_step.py                   # 回归：契约层
python tests/test_p5_data.py                         # 回归：Phase1-6
python tests/test_p7_rules.py                        # 回归：Phase7
```
