"""Phase 6.3 — 审计文书生成服务

四类审计文书（P8-8: 报告改读已确认疑点，不再重调 Agent）:
  1. 取证单 (Evidence Sheet)
  2. 审计底稿 (Working Paper)
  3. 审计报告 (Audit Report)
  4. 审理复核意见书 (Review Opinion)
"""
import json
from datetime import datetime


def generate_document(doc_type: str, context: dict) -> dict:
    """生成单个审计文书

    Args:
        doc_type: evidence / workpaper / report / review
        context: {project_title, suspicions, laws, analysis_summary}

    Returns:
        {success, document: {title, content, generated_at}}
    """
    templates = {
        "evidence": _build_evidence_template,
        "workpaper": _build_workpaper_template,
        "report": _build_report_template,
        "review": _build_review_template,
    }
    builder = templates.get(doc_type)
    if not builder:
        return {"success": False, "error": f"不支持的文书类型: {doc_type}"}

    doc = builder(context)
    return {"success": True, "document": doc}


def batch_generate(context: dict) -> dict:
    """批量生成全部四件套

    Returns:
        {success, documents: {evidence, workpaper, report, review}}
    """
    documents = {}
    for doc_type in ["evidence", "workpaper", "report", "review"]:
        result = generate_document(doc_type, context)
        if result["success"]:
            documents[doc_type] = result["document"]
    return {"success": True, "documents": documents}


def _build_evidence_template(ctx: dict) -> dict:
    """取证单"""
    project_title = ctx.get("project_title", "未命名项目")
    suspicions = ctx.get("suspicions", [])
    laws = ctx.get("laws", [])

    items = []
    for s in suspicions[:10]:
        items.append({
            "audit_item": s.get("violation_title", s.get("title", "")),
            "finding": s.get("description", ""),
            "amount": s.get("involved_amount", ""),
            "period": s.get("involved_period", ctx.get("audit_period", "")),
            "legal_basis": [
                {"law": l.get("law_title", ""), "clause": l.get("clause", "")}
                for l in laws[:3]
            ],
        })

    return {
        "doc_type": "evidence",
        "title": f"审计取证单 — {project_title}",
        "code": f"ZJ-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "project": project_title,
        "audit_items": items,
        "auditor": ctx.get("auditor", ""),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
    }


def _build_workpaper_template(ctx: dict) -> dict:
    """审计底稿"""
    return {
        "doc_type": "workpaper",
        "title": f"审计工作底稿 — {ctx.get('project_title', '')}",
        "code": f"WP-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "procedures": [
            "了解被审计单位内部控制制度",
            "收集并审查相关合同、凭证、账簿等资料",
            "执行分析性复核",
            "对异常事项进行延伸核查",
        ],
        "findings": ctx.get("analysis_summary", ""),
        "evidence_list": ctx.get("evidence_list", []),
        "generated_at": datetime.now().isoformat(),
    }


def _build_report_template(ctx: dict) -> dict:
    """审计报告（P8-8: 不再重调 suspicion_generator Agent——读已确认疑点）

    原 _build_report_template 第三次调 suspicion_generator Agent（继 graph step_6、
    /suspicion/generate 之外），不接收 selected_laws 法规链断。P8-8 改读 ctx["suspicions"]
    （由路由从 project_suspicions(CONFIRMED) + 证据链装配），AI 只组织语言不创造事实。
    """
    suspicions = ctx.get("suspicions", [])
    # 仅取已确认（CONFIRMED）疑点进报告；无来源（待人工核实）的禁入文书（§3.3）
    confirmed = [s for s in suspicions
                 if s.get("verify_status") == "CONFIRMED" or s.get("status") == "confirmed"]
    # 改动①:透传判定来源到文书——按 violation_id 确定性匹配（不依赖 LLM 重写疑点时的名称）
    # analysis_results(build_and_execute 产物)带 judge_source；疑点记录带 violation_id。
    # LLM 判定的疑点(judge_source='llm')在报告里标注脚注，供审计人员重点核实（法律责任）
    judge_map = {}
    for r in ctx.get("analysis_results", []):
        vid = r.get("violation_id")
        if vid is not None and r.get("judge_source"):
            judge_map[str(vid)] = r["judge_source"]
    if judge_map:
        for s in confirmed:
            vid = s.get("violation_id")
            if vid is not None and str(vid) in judge_map:
                s["judge_source"] = judge_map[str(vid)]
    total = len(confirmed)
    high = sum(1 for s in confirmed
               if (s.get("risk_level") or s.get("risk") or "") in ("高", "high"))

    return {
        "doc_type": "report",
        "title": f"审计报告 — {ctx.get('project_title', '')}",
        "code": f"AR-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "summary": ctx.get("analysis_summary", ""),
        "suspicions": confirmed,
        "high_risk_count": high,
        "total_suspicions": total,
        "recommendations": [
            "建议被审计单位针对上述问题逐项整改",
            "完善内部控制制度，堵塞管理漏洞",
            "对涉及违规资金依法依规处理",
        ],
        "generated_at": datetime.now().isoformat(),
    }


def _build_review_template(ctx: dict) -> dict:
    """审理复核意见书"""
    return {
        "doc_type": "review",
        "title": f"审理复核意见书 — {ctx.get('project_title', '')}",
        "code": f"RV-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "review_items": [
            {"item": "审计程序合规性", "ai_assessment": "程序完整", "human_review": ""},
            {"item": "证据充分性", "ai_assessment": "证据链闭合", "human_review": ""},
            {"item": "法规适用准确性", "ai_assessment": "法规引用恰当", "human_review": ""},
            {"item": "定性结论恰当性", "ai_assessment": "结论合理", "human_review": ""},
        ],
        "generated_at": datetime.now().isoformat(),
    }
