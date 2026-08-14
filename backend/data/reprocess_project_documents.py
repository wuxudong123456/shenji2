#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""项目级文档重处理（qingyue-procurement-six-items-repair 阶段B / 原计划 Task 7）

把"已上传到 MinIO 但从未跑过抽取"的 PDF 批量喂进标准分类+字段映射+证据链写入流程，
灌满 data_contracts/data_registers/data_finance 等结构化表，供阶段C跨文档规则读取。

抽取路径：pypdf 文本层 → ingest_preextracted_document（与 task_worker._run_ocr_task
同一条 enrich→classify→map→写trace+data+chunks+溯源 链，仅文本来源不同）。
已验证：案例 PDF 均为带文本层的合成件，pypdf 抽取干净、确定性、零外部依赖，
关键字段（合同金额/签约日期/发票号/送货日期/批次）全部命中，优于 OntoSKU（快且无非确定性）。

五模式（项目级隔离，只碰 project_id=PID 的数据）：
  --dry-run            列出待处理 PDF + 由文件名推断的文档角色/目标表，不写库
  --backup             把项目现有 trace 集合 + data_* 全行快照到带时间戳 JSON（含 sha256）
  --apply              为无 trace 的 PDF：下载→pypdf→建 trace→ingest；单份失败不阻断
  --verify             核验各 data_* 表行数 + 关键字段填充率（阶段B口径，不依赖规则引擎）
  --rollback <json>    按备份快照回滚：删除快照之后新增的 trace 及其 data_* 行

用法：
  python backend/data/reprocess_project_documents.py --project 3bf1fcf4fafb --dry-run
  python backend/data/reprocess_project_documents.py --project 3bf1fcf4fafb --backup
  python backend/data/reprocess_project_documents.py --project 3bf1fcf4fafb --apply
  python backend/data/reprocess_project_documents.py --project 3bf1fcf4fafb --verify
  python backend/data/reprocess_project_documents.py --project 3bf1fcf4fafb --rollback <backup.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from pypdf import PdfReader  # noqa: E402
from services import minio_client as M  # noqa: E402
from services.db import execute, insert, query, query_one  # noqa: E402
from services.task_worker import ingest_preextracted_document  # noqa: E402

DATABASE = "tt"
DATA_TABLES = ("data_contracts", "data_finance", "data_registers",
               "data_general", "data_procurements", "data_interviews")

# 文件名 → 文档角色/目标表（与 task_worker.enrich role_specs 同源，供 dry-run/verify 报告）
_ROLE_KEYWORDS = (
    (("设备采购合同", "采购合同"), "合同→data_contracts"),
    (("送货清单",), "送货→data_registers"),
    (("安装调试记录",), "安装→data_registers"),
    (("验收报告",), "验收→data_registers"),
    (("固定资产登记",), "资产登记→data_registers"),
    (("报价函",), "报价函→data_procurements"),
    (("资格审查",), "供应商审查→data_procurements"),
    (("市场询价",), "市场询价→data_procurements"),
    (("采购审批表",), "审批表→data_procurements"),
    (("评审记录", "成交意见"), "评审→data_general"),
    (("成交通知",), "成交通知→data_general"),
    (("增值税发票",), "发票→data_finance"),
    (("付款申请",), "付款申请→data_finance"),
    (("银行电子回单", "银行回单"), "银行回单→data_finance"),
    (("记账凭证",), "记账凭证→data_finance"),
    (("采购计划",), "采购计划→data_general"),
    (("采购需求申请",), "需求申请→data_general"),
)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value).__name__)


def _clean_filename(basename: str) -> str:
    """剥离 MinIO 对象名的 12 位 hex 前缀，还原 file_name。"""
    basename = basename.split("/")[-1]
    m = re.match(r"^[0-9a-f]{12}\.(.+)$", basename)
    return m.group(1) if m else basename


def _guess_role(filename: str) -> str:
    for keywords, label in _ROLE_KEYWORDS:
        if any(k in filename for k in keywords):
            return label
    return "?"


def _project_bucket(pid: str) -> str:
    row = query_one("SELECT minio_bucket FROM audit_projects WHERE id=%s", (pid,), database=DATABASE)
    return (row.get("minio_bucket") if row else None) or f"audit-project-{pid}"


def _audit_year(pid: str) -> int:
    row = query_one(
        "SELECT audit_year FROM audit_document_traces WHERE project_id=%s "
        "AND audit_year IS NOT NULL LIMIT 1", (pid,), database=DATABASE)
    return int(row["audit_year"]) if row and row.get("audit_year") else 2026


def _list_pdfs(bucket: str):
    objs = M.list_objects(prefix="", bucket=bucket, recursive=True)
    return [(o["name"], _clean_filename(o["name"])) for o in objs if o["name"].lower().endswith(".pdf")]


def _existing_traces(pid: str) -> dict:
    rows = query(
        "SELECT id, file_name, parse_status FROM audit_document_traces "
        "WHERE project_id=%s AND deleted_at IS NULL", (pid,), database=DATABASE)
    return {r["file_name"]: r for r in rows}


def _extract_pdf_text(file_bytes: bytes) -> tuple[str, list[str]]:
    """pypdf 抽文本层。无文本层（扫描件）抛异常，由调用方记录失败。"""
    import io
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = [(p.extract_text() or "").strip() for p in reader.pages]
    text = "\n\n".join(pages).strip()
    if len(text) < 20:
        raise RuntimeError("PDF无可用文本层（扫描件需OCR）")
    return text, pages


# ── dry-run ──
def cmd_dry_run(pid: str):
    bucket = _project_bucket(pid)
    pdfs = _list_pdfs(bucket)
    existing = _existing_traces(pid)
    todo = [(k, f) for k, f in pdfs if f not in existing]
    print(f"[dry-run] 项目 {pid}  bucket={bucket}")
    print(f"  MinIO PDF 总数: {len(pdfs)}")
    done = sum(1 for r in existing.values() if r["parse_status"] == "done")
    print(f"  已有 trace: {len(existing)} (done={done}, 非done={len(existing)-done})")
    print(f"  待处理(无trace): {len(todo)}")
    print("-" * 64)
    by_role = {}
    for k, f in sorted(todo):
        role = _guess_role(f)
        by_role[role] = by_role.get(role, 0) + 1
        print(f"  {role:<24} {f}")
    print("-" * 64)
    print(f"按角色汇总: {by_role}")
    print("（不写库）")


# ── backup ──
def cmd_backup(pid: str):
    bucket = _project_bucket(pid)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap = {"project_id": pid, "timestamp": ts, "bucket": bucket,
            "pre_trace_ids": [], "tables": {}}
    rows = query(
        "SELECT id, file_name FROM audit_document_traces WHERE project_id=%s AND deleted_at IS NULL",
        (pid,), database=DATABASE)
    snap["pre_trace_ids"] = [r["id"] for r in rows]
    snap["pre_trace_count"] = len(rows)
    for t in ("audit_document_traces",) + DATA_TABLES:
        snap["tables"][t] = query(f"SELECT * FROM {t} WHERE project_id=%s", (pid,), database=DATABASE)
    payload = json.dumps(snap, ensure_ascii=False, default=_json_default)
    snap["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    fname = BACKEND_DIR / "data" / f"_backup_{pid}_{ts}.json"
    fname.write_text(json.dumps(snap, ensure_ascii=False, default=_json_default), encoding="utf-8")
    counts = {t: len(v) for t, v in snap["tables"].items()}
    print(f"[backup] 快照 → {fname}")
    print(f"  备份时 trace 数: {snap['pre_trace_count']}  (ids 已记录供回滚)")
    print(f"  各表行数: {counts}")
    print(f"  sha256(前16): {snap['sha256']}")


# ── apply ──
def _ingest(pid, trace_id, bucket, full_key, filename, *, create_trace, audit_year=None):
    """下载→pypdf→(建trace或复用)→ingest。返回 (ok, table, detail)。"""
    data = M.download_file(full_key, bucket=bucket)
    md5 = hashlib.md5(data).hexdigest()
    text, pages = _extract_pdf_text(data)
    if create_trace:
        trace_id = insert(
            "INSERT INTO audit_document_traces "
            "(project_id, file_name, file_md5, minio_path, ocr_version, created_at, "
            "audit_year, file_category, file_subcategory, minio_bucket, file_size, parse_status) "
            "VALUES (%s,%s,%s,%s,0,NOW(),%s,'text','pdf',%s,%s,'pending')",
            (pid, filename, md5, full_key, audit_year, bucket, len(data)),
            database=DATABASE,
        )
    res = ingest_preextracted_document(pid, trace_id, filename, text, {}, "pypdf", True, pages)
    return True, res.get("table", "?"), f"mapped={res.get('fields_mapped')} extra={res.get('fields_extra')}"


def cmd_apply(pid: str, force: bool = False):
    bucket = _project_bucket(pid)
    audit_year = _audit_year(pid)
    if force:
        # 重新处理所有已有 trace：用改进后的 field_mapper 重抽，填充新列（batch_no 等）。
        # ingest 内部 _insert_into_data_table 会先 _delete_trace_data_rows，故幂等。
        rows = query(
            "SELECT id, file_name, minio_path, minio_bucket FROM audit_document_traces "
            "WHERE project_id=%s AND deleted_at IS NULL ORDER BY id", (pid,), database=DATABASE)
        todo = [(r["id"], r["file_name"], r["minio_path"] or "", r["minio_bucket"] or bucket) for r in rows]
        print(f"[apply --force] 项目 {pid}  重新处理 {len(todo)} 条已有 trace")
        print(f"  目的：用 Phase A 新增的 field_mapper 别名重抽，填充 batch_no/invoice_no/amount 等新列")
        if not todo:
            print("  无 trace 可重处理。"); return
        ok, fail, by_table = 0, [], {}
        for i, (tid, f, key, bk) in enumerate(todo, 1):
            print(f"  [{i:02d}/{len(todo)}] {f} ...", end=" ", flush=True)
            try:
                success, table, detail = _ingest(pid, tid, bk, key, f, create_trace=False)
            except Exception as e:
                success, table, detail = False, "?", f"{type(e).__name__}: {str(e)[:120]}"
            if success:
                ok += 1; by_table[table] = by_table.get(table, 0) + 1
                print(f"✅ {table} ({detail})")
            else:
                fail.append((f, detail)); print(f"❌ {detail}")
        print("-" * 64)
        print(f"[apply --force] 完成: 成功 {ok}/{len(todo)}，失败 {len(fail)}")
        print(f"  落表分布: {by_table}")
        if fail:
            print("  失败清单:")
            for f, d in fail:
                print(f"    - {f}: {d}")
        return

    pdfs = _list_pdfs(bucket)
    existing = _existing_traces(pid)
    todo = [(k, f) for k, f in pdfs if f not in existing]
    print(f"[apply] 项目 {pid}  bucket={bucket}  audit_year={audit_year}")
    print(f"  待处理: {len(todo)} 份（已有 trace 的 {len(pdfs)-len(todo)} 份跳过；要重抽已有件用 --force）")
    if not todo:
        print("  无待处理文件。"); return
    ok, fail, by_table = 0, [], {}
    for i, (k, f) in enumerate(sorted(todo), 1):
        print(f"  [{i:02d}/{len(todo)}] {f} ...", end=" ", flush=True)
        try:
            success, table, detail = _ingest(pid, None, bucket, k, f, create_trace=True, audit_year=audit_year)
        except Exception as e:
            success, table, detail = False, "?", f"{type(e).__name__}: {str(e)[:120]}"
        if success:
            ok += 1; by_table[table] = by_table.get(table, 0) + 1
            print(f"✅ {table} ({detail})")
        else:
            fail.append((f, detail)); print(f"❌ {detail}")
    print("-" * 64)
    print(f"[apply] 完成: 成功 {ok}/{len(todo)}，失败 {len(fail)}")
    print(f"  落表分布: {by_table}")
    if fail:
        print("  失败清单:")
        for f, d in fail:
            print(f"    - {f}: {d}")


# ── verify（阶段B口径：数据填充，不依赖规则引擎）──
def cmd_verify(pid: str):
    print(f"[verify] 项目 {pid} 数据表填充核验")
    traces = query_one(
        "SELECT COUNT(*) n, SUM(parse_status='done') done_n FROM audit_document_traces "
        "WHERE project_id=%s AND deleted_at IS NULL", (pid,), database=DATABASE)
    print(f"  trace: {traces['n']} 条 (done={traces['done_n']})")
    checks = {
        "data_contracts": ("合同", ["amount", "sign_date", "batch_no"]),
        "data_registers": ("履约", ["register_date", "register_type", "batch_no"]),
        "data_finance": ("财务", ["invoice_no", "voucher_no", "batch_no"]),
        "data_procurements": ("采购/报价", ["supplier", "batch_no"]),
        "data_general": ("综合", ["doc_date", "title"]),
    }
    total = 0
    for t, (desc, cols) in checks.items():
        rows = query(f"SELECT * FROM {t} WHERE project_id=%s", (pid,), database=DATABASE)
        n = len(rows)
        total += n
        no_trace = sum(1 for r in rows if not r.get("document_trace_id"))
        filled = {c: sum(1 for r in rows if r.get(c) not in (None, "")) for c in cols}
        print(f"  {t} [{desc}]: {n} 行  填充{filled}  缺trace行={no_trace}")
    print("-" * 64)
    # 履约按类型分布（GP-CONTRACT/ACCEPT 要按 delivery/installation/acceptance 分行）
    try:
        rr = query(
            "SELECT register_type, COUNT(*) c FROM data_registers WHERE project_id=%s "
            "GROUP BY register_type", (pid,), database=DATABASE)
        print("  data_registers 类型分布: " + ", ".join(f"{r['register_type']}={r['c']}" for r in rr))
    except Exception:
        pass
    # 批次覆盖（每张表按 batch_no 看批次齐全度）
    try:
        for t in ("data_contracts", "data_registers", "data_finance"):
            rr = query(f"SELECT batch_no, COUNT(*) c FROM {t} WHERE project_id=%s GROUP BY batch_no",
                       (pid,), database=DATABASE)
            dist = ", ".join(f"{r['batch_no']}={r['c']}" for r in rr if r.get("batch_no"))
            print(f"  {t} 批次分布: {dist or '(无batch_no)'}")
    except Exception:
        pass
    print(f"\n  总结构化行数: {total}")


# ── rollback（项目级删除回滚：删掉快照之后新增的 trace + 其 data 行）──
def cmd_rollback(pid: str, backup_file: str):
    snap = json.loads(Path(backup_file).read_text(encoding="utf-8"))
    pre_ids = set(snap["pre_trace_ids"])
    print(f"[rollback] 项目 {pid}  备份时间 {snap.get('timestamp')}  备份时 trace 数 {len(pre_ids)}")
    del_rows = 0
    for t in DATA_TABLES:
        rows = query(f"SELECT id, document_trace_id FROM {t} WHERE project_id=%s", (pid,), database=DATABASE)
        kill = [r["id"] for r in rows if r.get("document_trace_id") and r["document_trace_id"] not in pre_ids]
        for rid in kill:
            execute(f"DELETE FROM {t} WHERE id=%s", (rid,), database=DATABASE)
        del_rows += len(kill)
        if kill:
            print(f"  {t}: 删除 {len(kill)} 行")
    rows = query("SELECT id FROM audit_document_traces WHERE project_id=%s AND deleted_at IS NULL",
                 (pid,), database=DATABASE)
    kill_trace = [r["id"] for r in rows if r["id"] not in pre_ids]
    for tid in kill_trace:
        execute("DELETE FROM audit_document_traces WHERE id=%s", (tid,), database=DATABASE)
    print(f"  删除新增 trace: {len(kill_trace)}  删除新增 data_* 行: {del_rows}")
    print("[rollback] 完成（项目数据已恢复到备份时刻）")


def main():
    ap = argparse.ArgumentParser(description="项目级文档重处理（阶段B）")
    ap.add_argument("--project", required=True)
    ap.add_argument("--force", action="store_true",
                    help="--apply 时重抽所有已有 trace（用新 field_mapper 填充新列）")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--backup", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--rollback", metavar="BACKUP_JSON")
    args = ap.parse_args()
    if args.dry_run:
        cmd_dry_run(args.project)
    elif args.backup:
        cmd_backup(args.project)
    elif args.apply:
        cmd_apply(args.project, force=args.force)
    elif args.verify:
        cmd_verify(args.project)
    elif args.rollback:
        cmd_rollback(args.project, args.rollback)


if __name__ == "__main__":
    main()
