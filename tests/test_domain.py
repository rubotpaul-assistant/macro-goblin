from decimal import Decimal

import pytest

from macro_tracker.domain import MacroProfile, SourceType


def test_scale_profile_uses_decimal_arithmetic() -> None:
    profile = MacroProfile(
        calories=Decimal("120"),
        protein_g=Decimal("20"),
        carbs_g=Decimal("5.5"),
        fat_g=Decimal("2"),
        fiber_g=Decimal("1.25"),
    )

    assert profile.scale(Decimal("1.5")) == MacroProfile(
        calories=Decimal("180.000"),
        protein_g=Decimal("30.000"),
        carbs_g=Decimal("8.250"),
        fat_g=Decimal("3.000"),
        fiber_g=Decimal("1.875"),
    )


def test_profile_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        MacroProfile(Decimal("-1"), Decimal("0"), Decimal("0"), Decimal("0"))


def test_source_types_are_auditable() -> None:
    assert {source.value for source in SourceType} == {"label", "estimate", "manual"}


@pytest.mark.parametrize("value", ["Infinity", "NaN", "1.2345", "100001"])
def test_profile_rejects_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError, match=r"finite|decimal places|reasonable maximum"):
        MacroProfile(Decimal(value), Decimal("0"), Decimal("0"), Decimal("0"))
