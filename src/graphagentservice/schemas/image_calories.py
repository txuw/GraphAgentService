from __future__ import annotations

from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field



class FoodItem(BaseModel):
    name: str = Field(default="", description="食物名称")
    weight: Decimal | None = Field(default=None, description="估计重量（克）")
    calories: Decimal | None = Field(default=None, description="热量（千卡）")
    protein: Decimal | None = Field(default=None, description="蛋白质（克）")
    fat: Decimal | None = Field(default=None, description="脂肪（克）")
    carbohydrate: Decimal | None = Field(default=None, description="碳水化合物（克）")


class CalorieInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    foods: list[FoodItem] = Field(default_factory=list, description="识别到的食物列表")
    total_calories: Decimal | None = Field(
        default=None,
        description="总热量（千卡）",
        validation_alias=AliasChoices("total_calories", "totalCalories"),
    )
    total_protein: Decimal | None = Field(
        default=None,
        description="总蛋白质（克）",
        validation_alias=AliasChoices("total_protein", "totalProtein"),
    )
    total_fat: Decimal | None = Field(
        default=None,
        description="总脂肪（克）",
        validation_alias=AliasChoices("total_fat", "totalFat"),
    )
    total_carbohydrate: Decimal | None = Field(
        default=None,
        description="总碳水化合物（克）",
        validation_alias=AliasChoices("total_carbohydrate", "totalCarbohydrate"),
    )

class ImageCaloriesRequest(BaseModel):
    text: str = Field(default="")
    image_url: str = Field(min_length=1)


class ImageCaloriesOutput(BaseModel):
    answer: CalorieInfo