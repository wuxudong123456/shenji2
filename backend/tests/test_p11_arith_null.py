r"""Phase11 — 算术比较 NULL 假阳性修复验收

背景：违规模型 9704「采购合同金额超出预算批复金额」在清清项目误报 3 条命中。
表达式 `合同金额 > 预算金额 * 1.0 && 合同项目名称 = 预算项目名称` 里 `* 1.0`
走 _eval_arith 算术分支，预算金额为 NULL 时被强转成 0（旧 `float(val) if val is not None else 0`），
于是 `合同金额 > 0` 恒真 → "没录预算"被误判成"超预算"。

修复：_eval_arith 字段为空(None/"")时返回 _MISSING 哨兵并沿 ARITH 链传播；
      ARITH_CMP 任一侧 _MISSING 即判不命中（SQL 三值逻辑，与普通 GT/LT 节点
      L56-57 "字段为空→False" 行为对齐）。

测试（纯函数，零 DB/LLM 依赖）：
  ① NULL 右字段 → 不命中（修复点，旧逻辑误命中）
  ② 空串字段 → 同 NULL → 不命中
  ③ happy path：合同>预算（真值）→ 仍命中（防过度修复）
  ④ 合同==预算 → 不命中
  ⑤ 嵌套 ARITH NULL 传播：budget*1.0 中 budget 为 NULL → 不命中
  ⑥ 普通 GT（非算术）NULL 字段 → 不命中（既有行为守护）

用法：cd backend && python tests\test_p11_arith_null.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.expression_parser import parse_expression  # noqa: E402
from services.expression_engine import _eval_ast, _MISSING  # noqa: E402


def check(label, got, expect):
    ok = got == expect
    print(f"  {'✅' if ok else '❌'} {label}: got={got!r} 期望={expect!r}")
    return ok


def main():
    print("[test] Phase11 算术比较 NULL 假阳性修复验收\n")
    passed = 0
    failed = 0

    # 算术比较表达式（含 * 1.0 → ARITH_CMP）
    expr_arith = "采购合同.合同金额 > 明细表.预算金额 * 1.0"
    ast_arith = parse_expression(expr_arith)

    cases = [
        ("① NULL 右字段不命中（修复点）", {"contract_amount": 200, "budget_amount": None},
         False, expr_arith),
        ("② 空串字段同 NULL", {"contract_amount": 200, "budget_amount": ""},
         False, expr_arith),
        ("③ happy path 合同>预算仍命中", {"contract_amount": 200, "budget_amount": 100},
         True, expr_arith),
        ("④ 合同==预算不命中", {"contract_amount": 200, "budget_amount": 200},
         False, expr_arith),
        ("⑤ 嵌套ARITH NULL传播(budget*1.0)", {"contract_amount": 200, "budget_amount": None},
         False, "合同金额 > 预算金额 * 1.0"),
        ("⑥ NULL 左字段不命中", {"contract_amount": None, "budget_amount": 100},
         False, expr_arith),
    ]
    for label, row, expect, expr in cases:
        ast = parse_expression(expr) if expr != expr_arith else ast_arith
        if check(label, _eval_ast(ast, row), expect):
            passed += 1
        else:
            failed += 1

    # 守护：_MISSING 哨兵确实是 object，区别于 0/None/False
    print()
    if check("_MISSING 非零非None非False", _MISSING not in (0, None, False, ""), True):
        passed += 1
    else:
        failed += 1

    # 守护：普通 GT（非算术）NULL 字段仍不命中（既有行为未回归）
    ast_plain = parse_expression("合同金额 > 100")
    if check("⑦ 普通GT NULL字段不命中(守护)", _eval_ast(ast_plain, {"contract_amount": None}), False):
        passed += 1
    else:
        failed += 1

    print(f"\n{'='*50}")
    print(f"Phase11 算术比较 NULL 假阳性：PASS={passed}  FAIL={failed}")
    print(f"{'='*50}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
