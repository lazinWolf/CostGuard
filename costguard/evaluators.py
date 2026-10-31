"""How an answer is scored. Evaluators are registered by name so new kinds can be added.

An evaluator returns a quality score between 0 and 1, or None when it cannot judge; the
gate treats None as missing evidence, never as a pass.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

import httpx


@dataclass(frozen=True)
class Score:
    quality: Decimal | None
    evaluator: str | None


NO_SCORE = Score(None, None)
Evaluator = Callable[[object, object, str, httpx.Client], Score]
REGISTRY: dict[str, Evaluator] = {}


def register(name: str):
    def decorator(function: Evaluator) -> Evaluator:
        REGISTRY[name] = function
        return function
    return decorator


def normalize(text: str) -> str:
    """Case, surrounding punctuation, quotes and Markdown emphasis do not matter."""
    return " ".join(text.split()).strip(" .,;:!?\"'`*_").casefold()


def _label_score(name: str, matches: bool) -> Score:
    return Score(Decimal("1") if matches else Decimal("0"), f"{name}:v1")


@register("exact")
def exact(spec, case, answer: str, client: httpx.Client) -> Score:
    if case.expected_text is None:
        return NO_SCORE
    return _label_score("exact", answer.strip().casefold() == case.expected_text.strip().casefold())


@register("normalized")
def normalized(spec, case, answer: str, client: httpx.Client) -> Score:
    if case.expected_text is None:
        return NO_SCORE
    return _label_score("normalized", normalize(answer) == normalize(case.expected_text))


def evaluate(spec, case, answer: str, client: httpx.Client) -> Score:
    """Score `answer` with the evaluator the experiment names (default: exact)."""
    return REGISTRY[spec.evaluator](spec, case, answer, client)
