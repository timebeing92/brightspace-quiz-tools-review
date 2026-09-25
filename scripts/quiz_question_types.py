"""Shared presentation catalog; build support comes only from the capability registry."""
from quiz_build_support import load_capability_registry

LABELS = {
    'multiple_choice': 'Multiple Choice', 'true_false': 'True/False',
    'multi_select': 'Multi-Select', 'long_answer': 'Written Response',
    'short_answer': 'Short Answer', 'multi_short_answer': 'Multi-Short Answer',
    'fill_in_blanks': 'Fill in the Blanks', 'matching': 'Matching', 'ordering': 'Ordering',
}

def question_type_catalog():
    capabilities = load_capability_registry()['question_kinds']
    return [dict(kind=kind, label=label, **capabilities[kind]) for kind, label in LABELS.items()]


def canonical_question_type(value):
    aliases = {name.casefold(): kind for kind, label in LABELS.items() for name in (kind, label)}
    aliases['long answer'] = 'long_answer'
    try:
        return aliases[str(value).strip().casefold()]
    except KeyError:
        raise ValueError('Unknown question type; use a catalog label or canonical kind.') from None
