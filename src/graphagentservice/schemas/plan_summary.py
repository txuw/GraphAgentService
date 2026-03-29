from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class WorkoutItem(BaseModel):
    name: str = Field(default="")
    sets: int = Field(default=0)
    reps: str = Field(default="")


class DayPlan(BaseModel):
    day: str = Field(default="")
    title: str = Field(default="")
    focus: str = Field(default="")
    duration_minutes: int = Field(default=0)
    workouts: list[WorkoutItem] = Field(default_factory=list)
    diet_suggestion: str = Field(default="")


class Overview(BaseModel):
    habit_summary: str = Field(default="")
    average_intake_kcal: int = Field(default=0)
    average_burn_kcal: int = Field(default=0)
    target_intake_kcal: int = Field(default=0)
    notes: str = Field(default="")


class PlanSummary(BaseModel):
    overview: Overview = Field(default_factory=Overview)
    days: list[DayPlan] = Field(default_factory=list)


class PlanAnalyzeSummaryOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    analysis: str = Field(default="")
    plan_summary: PlanSummary = Field(
        default_factory=PlanSummary,
        validation_alias=AliasChoices("plan_summary", "planSummary"),
        serialization_alias="planSummary",
    )
