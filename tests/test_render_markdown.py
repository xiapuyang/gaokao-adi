"""Tests for render_markdown advice generation.

Focus: the appetite advice line must only claim a tie-break "effect" when the
risk appetite actually reordered the ranking — never as unconditional boilerplate.
"""

from scripts.render_markdown import _appetite_advice

# A truthy weight vector + an appetite that has a narrative entry; concrete values
# are irrelevant here since _appetite_advice only branches on the meta flags.
_AVERSE_WEIGHTS = {"paths": 0.09, "reach": 0.38, "correct": 0.09, "recover": 0.44}


def _meta(**overrides) -> dict:
    base = {
        "risk_appetite": "averse",
        "appetite_contradiction": False,
        "appetite_tie_break_weights": _AVERSE_WEIGHTS,
        "appetite_changed_order": False,
        "appetite_promoted": None,
        "appetite_demoted": None,
    }
    base.update(overrides)
    return base


def test_advice_names_pair_when_appetite_reorders():
    out = _appetite_advice(
        _meta(
            appetite_changed_order=True,
            appetite_promoted="数据科学",
            appetite_demoted="计算机",
        )
    )
    text = "\n".join(out)
    assert "起了作用" in text
    assert "数据科学" in text and "计算机" in text
    assert "排到了" in text


def test_advice_states_no_effect_when_order_unchanged():
    out = _appetite_advice(_meta(appetite_changed_order=False))
    text = "\n".join(out)
    assert "未改变名次" in text
    # The old unconditional boilerplate must be gone.
    assert "决出先后" not in text
    assert "起了作用" not in text


def test_advice_contradiction_short_circuits():
    out = _appetite_advice(
        _meta(appetite_contradiction=True, risk_appetite="contradiction")
    )
    text = "\n".join(out)
    assert "自相矛盾" in text
    assert "起了作用" not in text and "未改变名次" not in text


def test_advice_empty_without_tie_break_weights():
    assert _appetite_advice(_meta(appetite_tie_break_weights=None)) == []
