"""The dose guard (roadmap step 8, health rules): no AI text that suggests an insulin dose or a change to the
insulin settings is ever stored or shown.

The prompt already forbids it; this is the check that does not trust the prompt. Every sentence (and list
item) that mentions insulin (the word, a bolus, basal, a dose, a ratio, the ISF, a correction, units, an
insulin name, or an amount like "2 u") is removed when it also:
  - advises: should, could, consider, try, recommend, suggest, may/might want, next time, instead, aim,
    avoid, plan, worth, better to, would help ...
  - starts with an instruction: "Take", "Inject", "Reduce", "Lower", "Skip", "Use" ...
  - puts a change verb right before the insulin: "reduce your bolus", "lowering the basal", "cut the dose"
Plain reports of what happened stay: "IOB at the start was 2.1 u", "glucose dropped 40 mg/dL with 2.1 u on
board", "you injected 4 u at 08:20".

"Review suggested" (the analysis flagging something for the endocrinologist) and sentences about keeping the
log complete ("log every dose") are allowed as long as they hold no amount of insulin and no change verb on it.

Deliberately broad: a wrongly removed sentence costs a little reading, a missed one could cost a low. Each
removal leaves a visible note in its place, and the report records how many were removed.
"""

import re

NOTE = "[Removed: this sentence suggested an insulin change. Insulin settings are only changed by you, with your endocrinologist.]"

_AMOUNT = r"\d+(?:[.,]\d+)?\s*(?:u|iu|units?)(?!\w)"
_INSULIN = re.compile(
    r"\b(?:insulin|bolus(?:es|ing)?|pre-?bolus\w*|basal|dos(?:e|es|ed|ing)|ratios?|isf|sensitivity factor|"
    r"corrections?|units?|novorapid|humalog|lyumjev|tresiba|toujeo|lantus|levemir|pens?)(?!\w)|" + _AMOUNT,
    re.IGNORECASE,
)
_ADVICE = re.compile(
    r"\b(?:should|could|consider\w*|try|trying|recommend\w*|suggest\w*|(?:may|might) (?:want|wish|need)|"
    r"next time|instead|aim\w*|avoid\w*|plan\w*|worth|better to|would (?:help|reduce|lower|prevent|avoid)|"
    r"you (?:can|need to|have to|must)|it (?:may|might) help)\b",
    re.IGNORECASE,
)
_CHANGE = r"(?:reduc\w*|lower\w*|decreas\w*|increas\w*|rais\w*|cut\w*|skip\w*|add\w*|adjust\w*|chang\w*|modif\w*|tweak\w*|halv\w*|doubl\w*|split\w*|delay\w*|drop\w*|omit\w*)"
_IMPERATIVE = re.compile(
    rf"^\W*(?:take|taking|inject\w*|give|giving|bolus\w*|use|using|{_CHANGE})\b", re.IGNORECASE
)
_CHANGE_OBJECT = re.compile(
    rf"\b{_CHANGE}\s+(?:(?:your|the|a|that|this|its|my|each|every|lunch|breakfast|dinner|morning|evening|pre-?run|meal)\s+){{0,3}}"
    r"(?:insulin|bolus\w*|pre-?bolus\w*|basal|dos(?:e|es|ing)|ratios?|isf|corrections?|units?)\b",
    re.IGNORECASE,
)
# Sentences end at . ! ? followed by space; lines (list items, headings) are handled one by one.
_SPLIT = re.compile(r"(?<=[.!?])[ \t]+")
_REVIEW = re.compile(r"review suggested", re.IGNORECASE)
# A size put on the insulin ("a smaller bolus", "less insulin"): never a plain logging remark.
_SIZE = re.compile(
    r"\b(?:smaller|bigger|larger|less|more|lower|higher|extra|half|double|reduced|increased)\s+(?:\w+\s+){0,2}"
    r"(?:insulin|bolus\w*|basal|dos(?:e|es)|corrections?|units?)\b",
    re.IGNORECASE,
)
# About keeping the log complete ("log every dose"), which is not a dose suggestion.
_LOGGING = re.compile(r"\b(?:log|logs|logged|logging|unlogged)\b", re.IGNORECASE)


def _bad(sentence: str) -> bool:
    if not _INSULIN.search(sentence):
        return False
    plain = not (re.search(_AMOUNT, sentence, re.IGNORECASE) or _CHANGE_OBJECT.search(sentence) or _SIZE.search(sentence))
    if plain and (_REVIEW.search(sentence) or _LOGGING.search(sentence)):
        return False
    return bool(_ADVICE.search(sentence) or _IMPERATIVE.search(sentence) or _CHANGE_OBJECT.search(sentence))


def clean(text: str) -> tuple[str, int]:
    """The text with every dose-suggesting sentence replaced by NOTE, and how many were replaced."""
    out_lines, removed = [], 0
    for line in text.split("\n"):
        prefix = re.match(r"^\s*(?:[-*]|\d+\.)\s+|^\s*#+\s+", line)
        lead = prefix.group(0) if prefix else ""
        parts = [p for p in _SPLIT.split(line[len(lead):]) if p.strip()]
        if not parts:
            out_lines.append(line)
            continue
        kept, noted = [], False
        for p in parts:
            if _bad(p):
                removed += 1
                if not noted:  # one note per line, however many sentences went
                    kept.append(NOTE)
                    noted = True
            else:
                kept.append(p)
        out_lines.append(lead + " ".join(kept))
    return "\n".join(out_lines), removed
