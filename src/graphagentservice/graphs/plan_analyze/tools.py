"""plan_analyze Graph 的本地工具定义。

当前包含：
  - ask_user_questions：通过 LangGraph interrupt 机制向用户提出多项选择问题，
    收集用户偏好后以 ToolMessage 形式返回给 Agent。
"""

from __future__ import annotations

import json
from typing import Annotated

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field


# ── Schema 定义 ─────────────────────────────────────────────────────


class OptionSchema(BaseModel):
    """单个选项的定义。"""

    label: str = Field(description="选项显示文本")
    description: str = Field(description="选项说明，解释选择后的影响")
    preview: str | None = Field(
        default=None,
        description="可选预览内容（代码片段、配置示例等）",
    )


class QuestionSchema(BaseModel):
    """单个问题的定义。"""

    question_id: str = Field(description="问题的唯一标识符，用于关联答案")
    header: str = Field(description="简短标签，显示为 chip/tag（最多 12 字符）")
    question: str = Field(description="完整问题文本")
    options: list[OptionSchema] = Field(
        description="可选答案列表，2-4 个选项",
        min_length=2,
        max_length=4,
    )
    multiSelect: bool = Field(default=False, description="是否允许多选")


class AskUserQuestionsInput(BaseModel):
    """ask_user_questions 工具的输入 Schema。"""

    questions: Annotated[
        list[QuestionSchema],
        "1-4 个问题的列表",
    ] = Field(
        description="要询问用户的问题列表，最多 4 个问题",
        min_length=1,
        max_length=4,
    )


# ── 工具实现 ───────────────────────────────────────────────────────


@tool(args_schema=AskUserQuestionsInput)
def ask_user_questions(questions: list[QuestionSchema]) -> str:
    """询问用户多项选择问题来收集信息、澄清歧义、了解偏好、做出决定或为他们提供选择。

    使用场景：
    - 收集用户偏好或要求（如选择库、框架、配置）
    - 澄清不明确的指令（如确认实现方式）
    - 做出实施选择的决定（如认证方法、数据格式）
    - 为用户提供选择方向（如功能优先级）

    注意事项：
    - 用户始终可以选择"其他"来提供自定义文本输入
    - multiSelect=true 时允许选择多个选项（逗号分隔）
    - 推荐选项应放在列表第一位，标签末尾添加"（推荐）"
    - multiSelect 问题与非 multiSelect 问题必须分开收集，各自的答案列表独立
    """
    # 将问题列表序列化为 JSON，作为 interrupt payload 传递给前端
    questions_payload = json.dumps(
        [q.model_dump() for q in questions],
        ensure_ascii=False,
        indent=2,
    )

    # 触发 LangGraph 中断，暂停执行等待用户回复
    answers_input = interrupt(questions_payload)

    # 解析用户返回的答案
    # resume 端点 body.answers 会被透传为 Command(resume=body.answers)
    # 因此 interrupt() 返回值就是 body.answers 本身
    answers: dict[str, str | list[str]] = {}
    if isinstance(answers_input, dict):
        answers = {
            k: v for k, v in answers_input.items()
            if isinstance(v, (str, list))
        }

    if answers:
        answer_summary = "\n".join(
            f"- {q}: {a}" for q, a in answers.items()
        )
        return f"用户已回答问题：\n{answer_summary}"
    return "用户未提供有效答案"
