from __future__ import annotations

import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, tool

_WEATHER_SNAPSHOTS = {
    "beijing": "Beijing weather is sunny, 26C, with light wind.",
    "hangzhou": "Hangzhou weather is cloudy, 24C, with possible light rain.",
    "shanghai": "Shanghai weather is cloudy, 25C, with humid air.",
    "shenzhen": "Shenzhen weather is warm, 29C, with scattered clouds.",
}

_CITY_TIMEZONES = {
    "beijing": "Asia/Shanghai",
    "hangzhou": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "san francisco": "America/Los_Angeles",
}

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
def lookup_weather(location: str) -> str:
    """Look up a canned weather snapshot for a city."""
    normalized = location.strip().lower()
    if not normalized:
        return "No location was provided."
    return _WEATHER_SNAPSHOTS.get(
        normalized,
        f"No weather snapshot is configured for {location}.",
    )


@tool
def lookup_local_time(city: str) -> str:
    """Look up the current local time for a supported city."""
    normalized = city.strip().lower()
    timezone_name = _CITY_TIMEZONES.get(normalized)
    if timezone_name is None:
        return f"No timezone mapping is configured for {city}."

    current_time = datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M:%S")
    return f"The local time in {city} is {current_time} ({timezone_name})."


@tool
def calculate(expression: str) -> str:
    """Evaluate a basic arithmetic expression."""
    try:
        value = _evaluate_expression(expression)
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        return f"Failed to calculate expression: {exc}"
    return f"The result of {expression} is {value}."


def build_toolset() -> list[BaseTool]:
    return [lookup_weather, lookup_local_time, calculate]


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
