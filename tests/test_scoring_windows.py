import pandas as pd
from pipeline.analysis.scoring_engine import _revenue_growth_trend


def _annual(revs):
    return pd.DataFrame({"sale": revs})


def test_four_years_windows_do_not_overlap_and_years_match():
    # 100 -> 121 over 2 periods = 10%/yr in both halves: delta must be ~0
    delta, desc = _revenue_growth_trend(_annual([100, 110, 121, 133.1]))
    assert delta is not None
    assert abs(delta) < 0.005, f"steady growth must read as stable, got {desc}"


def test_acceleration_detected_with_five_years():
    # prior half flat, recent half +20%/yr
    delta, desc = _revenue_growth_trend(_annual([100, 100, 100, 120, 144]))
    assert delta is not None and delta > 0.02
    assert "Accelerating" in desc


def test_seven_years_unchanged_behavior():
    revs = [100, 105, 110, 115, 130, 150, 175]
    delta, _ = _revenue_growth_trend(_annual(revs))
    assert delta is not None and delta > 0
