from __future__ import annotations

import ast
import operator

from langchain_core.tools import tool

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


@tool
def calculate(expression: str) -> str:
    """Evaluate a basic arithmetic expression."""
    try:
        value = _evaluate_expression(expression)
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        return f"Failed to calculate expression: {exc}"
    return f"The result of {expression} is {value}."


def _evaluate_expression(expression: str) -> float | int:
    parsed = ast.parse(expression, mode="eval")
    return _eval_node(parsed.body)


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value

    if isinstance(node, ast.BinOp):
        operator_fn = _BINARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("unsupported operator")
        return operator_fn(_eval_node(node.left), _eval_node(node.right))

    if isinstance(node, ast.UnaryOp):
        operator_fn = _UNARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("unsupported unary operator")
        return operator_fn(_eval_node(node.operand))

    raise ValueError("unsupported expression")
