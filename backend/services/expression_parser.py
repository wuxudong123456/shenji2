"""Phase 4.1 — 违规表达式解析器: 伪 SQL → AST

支持的语法:
  - 比较运算: >  <  =  !=  >=  <=  <>
  - 范围运算: field BETWEEN val1 AND val2
  - 逻辑运算: && / AND , || / OR
  - 括号分组: ( ... )
  - 字段引用: 表名.字段名 (解析时还原为纯字段名)
  - 值: 数字(含小数)、单引号字符串、NULL

示例:
  输入: "(采购合同.金额 > 1000000) && (采购合同.采购方式 = '询价')"
  输出: {"type":"AND","left":{"type":"GT","field":"金额","value":1000000},...}
"""
import re
from typing import Any


# ── Token 定义 ──
# Q2.1 补强：增加 NOT IN / IN / 算术运算符 / 逗号
_TOKEN_SPEC = [
    ("BETWEEN", r"\bBETWEEN\b"),
    ("AND_OP",  r"\bAND\b"),
    ("OR_OP",   r"\bOR\b"),
    ("NOT_IN",  r"\bNOT\s+IN\b"),
    ("IN",      r"\bIN\b"),
    ("AND",     r"&&"),
    ("OR",      r"\|\|"),
    ("GTE",     r">="),
    ("LTE",     r"<="),
    ("NE",      r"!=|<>"),
    ("GT",      r">"),
    ("LT",      r"<"),
    ("EQ",      r"="),
    ("PLUS",    r"\+"),
    ("MINUS",   r"-"),
    ("MUL",     r"\*"),
    ("DIV",     r"/"),
    ("LPAREN",  r"\("),
    ("RPAREN",  r"\)"),
    ("LBRACK",  r"\["),
    ("RBRACK",  r"\]"),
    ("COMMA",   r","),
    ("NUMBER",  r"\d+\.?\d*"),
    ("STRING",  r"'[^']*'|\"[^\"]*\""),
    ("NULL",    r"\bNULL\b"),
    # FIELD 必须在 STRING 之后，避免中文字符被误匹配到引号内的内容
    ("FIELD",   r"[a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*(\.[a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)*"),
    ("SKIP",    r"[ \t\n\r]+"),
    ("MISMATCH", r"."),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC), re.IGNORECASE)


def _tokenize(expression: str) -> list[tuple[str, str]]:
    """词法分析: 字符串 → Token 流"""
    tokens = []
    for m in _TOKEN_RE.finditer(expression):
        kind = m.lastgroup
        value = m.group()
        if kind == "SKIP":
            continue
        if kind == "MISMATCH":
            raise SyntaxError(f"无法识别的字符: {value} (位置 {m.start()})")
        tokens.append((kind, value))
    return tokens


class _Parser:
    """递归下降解析器"""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> tuple[str, str] | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _consume(self, *kinds: str) -> tuple[str, str]:
        tok = self._peek()
        if tok is None:
            raise SyntaxError(f"期望 {kinds}，但表达式已结束")
        if kinds and tok[0] not in kinds:
            raise SyntaxError(f"期望 {kinds}，实际 {tok} (位置 {self.pos})")
        self.pos += 1
        return tok

    def parse(self) -> dict:
        """入口: expression → or_expr"""
        node = self._or_expr()
        if self.pos < len(self.tokens):
            tok = self._peek()
            raise SyntaxError(f"多余的 token: {tok}")
        return node

    def _or_expr(self) -> dict:
        """or_expr → and_expr (('||' | 'OR') and_expr)*"""
        left = self._and_expr()
        while self._peek() and self._peek()[0] in ("OR", "OR_OP"):
            op = self._consume("OR", "OR_OP")[0]
            right = self._and_expr()
            left = {"type": "OR", "left": left, "right": right}
        return left

    def _and_expr(self) -> dict:
        """and_expr → compare (('&&' | 'AND') compare)*"""
        left = self._compare()
        while self._peek() and self._peek()[0] in ("AND", "AND_OP"):
            op = self._consume("AND", "AND_OP")[0]
            right = self._compare()
            left = {"type": "AND", "left": left, "right": right}
        return left

    def _compare(self) -> dict:
        """compare → arith (比较运算符 arith)? | arith BETWEEN arith AND arith
                    | arith IN (list) | arith NOT IN (list)

        Q2.1 补强：增加 IN / NOT IN / 算术表达式支持
        """
        left = self._arithmetic()

        # 如果 left 已经是完整的比较/逻辑节点（来自括号内的表达式），直接返回
        if left.get("type") in ("GT", "LT", "EQ", "NE", "GTE", "LTE",
                                "BETWEEN", "AND", "OR", "IN", "NOT_IN", "TRUTHY"):
            return left

        tok = self._peek()

        # BETWEEN（仅对 field/literal 有效，ARITH 不支持 BETWEEN）
        if tok and tok[0] == "BETWEEN" and left.get("type") != "ARITH":
            self._consume("BETWEEN")
            lo = self._arithmetic()
            self._consume("AND_OP")  # BETWEEN ... AND ...
            hi = self._arithmetic()
            return {"type": "BETWEEN", "field": left["value"], "low": lo["value"], "high": hi["value"]}

        # IN / NOT IN（仅对 field 有效）
        if tok and tok[0] in ("IN", "NOT_IN") and left.get("type") == "field":
            op = self._consume(tok[0])[0]
            values = self._parse_list()
            node_type = "NOT_IN" if op == "NOT_IN" else "IN"
            return {"type": node_type, "field": left["value"], "values": values}

        # 比较运算符
        if tok and tok[0] in ("GT", "LT", "EQ", "NE", "GTE", "LTE"):
            op = self._consume(tok[0])[0]
            right = self._arithmetic()
            op_map = {"GT": "GT", "LT": "LT", "EQ": "EQ", "NE": "NE", "GTE": "GTE", "LTE": "LTE"}
            # 若右侧或左侧是算术表达式，包成 ARITH_CMP
            if right.get("type") == "ARITH" or left.get("type") == "ARITH":
                return {"type": "ARITH_CMP", "op": op_map[op],
                        "left": left, "right": right}
            return {"type": op_map[op], "field": left["value"], "value": right["value"]}

        # ARITH 节点（如括号内的 amount/10000）：原样返回，让外层匹配比较符
        if left.get("type") == "ARITH":
            return left

        # 单值字段（如审批文件 IS NULL 场景）：视为布尔真值
        return {"type": "TRUTHY", "value": left.get("value", left)}

    def _arithmetic(self) -> dict:
        """arith → term (('+' | '-') term)*

        Q2.1 新增：算术加减（用于 (金额/合同金额) > 0.03 这类比例表达式）
        """
        left = self._term()
        while self._peek() and self._peek()[0] in ("PLUS", "MINUS"):
            op = self._consume(self._peek()[0])[0]
            right = self._term()
            left = {"type": "ARITH", "op": "+" if op == "PLUS" else "-",
                    "left": left, "right": right}
        return left

    def _term(self) -> dict:
        """term → primary (('*' | '/') primary)*"""
        left = self._primary()
        while self._peek() and self._peek()[0] in ("MUL", "DIV"):
            op = self._consume(self._peek()[0])[0]
            right = self._primary()
            left = {"type": "ARITH", "op": "*" if op == "MUL" else "/",
                    "left": left, "right": right}
        return left

    def _parse_list(self) -> list:
        """解析 IN/NOT IN 后的值列表: ( v1, v2, v3 ) 或 [ v1, v2 ] 或 v1、v2、v3

        支持圆括号、方括号、中文顿号分隔
        """
        values = []
        # 吃掉开括号
        if self._peek() and self._peek()[0] in ("LPAREN", "LBRACK"):
            self._consume(self._peek()[0])

        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok[0] in ("RPAREN", "RBRACK"):
                self._consume(tok[0])
                break
            if tok[0] == "STRING":
                v = self._consume("STRING")[1]
                if (v.startswith("'") and v.endswith("'")) or \
                   (v.startswith('"') and v.endswith('"')):
                    v = v[1:-1]
                values.append(v)
            elif tok[0] == "NUMBER":
                v = self._consume("NUMBER")[1]
                values.append(float(v) if "." in v else int(v))
            elif tok[0] == "FIELD":
                # 列表里的裸词也当作字符串值（如 NOT IN ('公开招标', '邀请招标')）
                values.append(self._consume("FIELD")[1])
            elif tok[0] in ("COMMA",):
                self._consume("COMMA")
                continue
            else:
                break

        return values

    def _primary(self) -> dict:
        """primary → NUMBER | STRING | NULL | FIELD | '(' expression ')'"""
        tok = self._peek()
        if tok is None:
            raise SyntaxError("表达式不完整")

        if tok[0] == "NUMBER":
            val = self._consume("NUMBER")[1]
            if "." in val:
                return {"type": "literal", "value": float(val)}
            return {"type": "literal", "value": int(val)}

        if tok[0] == "STRING":
            val = self._consume("STRING")[1]
            # 去掉首尾引号（支持单引号和双引号）
            if (val.startswith("'") and val.endswith("'")) or \
               (val.startswith('"') and val.endswith('"')):
                val = val[1:-1]
            return {"type": "literal", "value": val}

        if tok[0] == "NULL":
            self._consume("NULL")
            return {"type": "literal", "value": None}

        if tok[0] == "LPAREN":
            self._consume("LPAREN")
            node = self._or_expr()
            # 可能是 (expr) 也可能是 list 的开始（IN 场景已由 _parse_list 处理）
            if self._peek() and self._peek()[0] == "RPAREN":
                self._consume("RPAREN")
            return node

        if tok[0] == "FIELD":
            raw = self._consume("FIELD")[1]
            # 表名.字段名 → 取字段名部分
            field_name = raw.split(".")[-1] if "." in raw else raw
            return {"type": "field", "value": field_name}

        raise SyntaxError(f"意外的 token: {tok}")


def parse_expression(expression: str) -> dict:
    """解析违规表达式伪SQL，返回AST

    Args:
        expression: 伪SQL表达式字符串

    Returns:
        AST 字典

    Raises:
        SyntaxError: 表达式语法错误
    """
    expression = expression.strip()
    if not expression:
        return {"type": "literal", "value": True}

    # 预处理: 替换中文符号
    expression = expression.replace("（", "(").replace("）", ")")
    expression = expression.replace("，", ",").replace("；", ";")

    tokens = _tokenize(expression)
    return _Parser(tokens).parse()


def ast_to_str(ast: dict) -> str:
    """AST → 可读字符串（用于调试和日志）"""
    t = ast.get("type", "?")

    if t == "AND":
        return f"({ast_to_str(ast['left'])} && {ast_to_str(ast['right'])})"
    if t == "OR":
        return f"({ast_to_str(ast['left'])} || {ast_to_str(ast['right'])})"
    if t == "GT":
        return f"{ast['field']} > {ast['value']}"
    if t == "LT":
        return f"{ast['field']} < {ast['value']}"
    if t == "EQ":
        return f"{ast['field']} = {repr(ast['value'])}"
    if t == "NE":
        return f"{ast['field']} != {repr(ast['value'])}"
    if t == "GTE":
        return f"{ast['field']} >= {ast['value']}"
    if t == "LTE":
        return f"{ast['field']} <= {ast['value']}"
    if t == "BETWEEN":
        return f"{ast['field']} BETWEEN {ast['low']} AND {ast['high']}"
    if t == "TRUTHY":
        return f"TRUTHY({ast['value']})"
    if t == "IN":
        return f"{ast['field']} IN {ast['values']}"
    if t == "NOT_IN":
        return f"{ast['field']} NOT IN {ast['values']}"
    if t == "ARITH":
        return f"({ast_to_str(ast['left'])} {ast['op']} {ast_to_str(ast['right'])})"
    if t == "ARITH_CMP":
        return f"({ast_to_str(ast['left'])} {ast['op']} {ast_to_str(ast['right'])})"
    if t in ("literal", "field"):
        return str(ast.get("value", "?"))
    return str(ast)
