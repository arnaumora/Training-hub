"""The dose guard (app/ai_guard.py): dose suggestions go, reports of what happened stay."""

import pytest

from app.ai_guard import NOTE, clean

REMOVED = [
    "Consider reducing your lunch bolus by 1 u on run days.",
    "Next time, take 2 u less before a long run.",
    "Take 2 units less at breakfast.",
    "Reduce the bolus before morning runs.",
    "Lowering your basal the night before could help.",
    "You may want to inject a smaller dose.",
    "A temporary basal of 50 % would help here.",
    "Try a pre-bolus of 15 minutes instead.",
    "Your insulin-to-carb ratio should be revisited.",
    "Cut the correction dose when you plan to run.",
    "Skip the correction before training.",
    "Using 1 u less Humalog may prevent this.",
    "Review suggested: reduce the ratio to 1 u per 15 g.",
    "You should log a smaller bolus before runs.",
]

KEPT = [
    "IOB at the start was 2.1 u, higher than in the similar runs.",
    "Glucose dropped 40 mg/dL with 2.1 u on board.",
    "You injected 4 u of Humalog at 08:20, 40 minutes before the start.",
    "The fastest fall came in the second half, at 165 bpm.",
    "Consider 20 g of carbs 15 minutes before a run like this.",
    "Review suggested: lunch ends above 180 on most days, with the data above.",
    "Coverage was 100 %, so the curve is reliable.",
    "Grade-adjusted pace was 5:28 /km against 5:33 moving pace.",
    "Insulin on board was 0 per the log; if a dose went unlogged, you should log it so this is right.",
]


@pytest.mark.parametrize("sentence", REMOVED)
def test_removed(sentence):
    text, n = clean(sentence)
    assert n == 1 and text == NOTE


@pytest.mark.parametrize("sentence", KEPT)
def test_kept(sentence):
    assert clean(sentence) == (sentence, 0)


def test_only_the_bad_sentence_goes_and_the_layout_stays():
    text = (
        "## Glucose\n"
        "\n"
        "Glucose fell from 158 to 115. Consider reducing your bolus before runs. The low came at 47 min.\n"
        "- IOB was 2.1 u at the start.\n"
        "- Take 1 u less next time. Also lower the basal.\n"
    )
    out, n = clean(text)
    assert n == 3
    assert out == (
        "## Glucose\n"
        "\n"
        f"Glucose fell from 158 to 115. {NOTE} The low came at 47 min.\n"
        "- IOB was 2.1 u at the start.\n"
        f"- {NOTE}\n"
    )
