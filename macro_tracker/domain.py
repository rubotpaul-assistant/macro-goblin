"""Pure macronutrient value objects and arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import StrEnum


class SourceType(StrEnum):
    """Provenance for a nutrient profile."""

    LABEL = "label"
    ESTIMATE = "estimate"
    MANUAL = "manual"


@dataclass(frozen=True)
class MacroProfile:
    """Macronutrients for one declared serving."""

    calories: Decimal
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal
    fiber_g: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if not value.is_finite():
                raise ValueError("macro values must be finite")
            if value < 0:
                raise ValueError("macro values must be non-negative")
            if value.as_tuple().exponent < -3:
                raise ValueError("macro values support at most three decimal places")
            maximum = Decimal("100000") if field.name == "calories" else Decimal("10000")
            if value > maximum:
                raise ValueError("macro value exceeds reasonable maximum")

    def scale(self, servings: Decimal) -> MacroProfile:
        """Scale one serving without binary floating-point arithmetic."""
        if servings <= 0:
            raise ValueError("servings must be positive")
        precision = Decimal("0.001")
        return MacroProfile(
            calories=(self.calories * servings).quantize(precision),
            protein_g=(self.protein_g * servings).quantize(precision),
            carbs_g=(self.carbs_g * servings).quantize(precision),
            fat_g=(self.fat_g * servings).quantize(precision),
            fiber_g=(self.fiber_g * servings).quantize(precision),
        )

    def as_strings(self) -> dict[str, str]:
        """Return stable fixed-point strings for JSON and persistence."""
        return {
            field.name: format(getattr(self, field.name).quantize(Decimal("0.001")), "f")
            for field in fields(self)
        }
