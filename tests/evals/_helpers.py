from collections import defaultdict
from typing import Any


async def collect_scores(results: Any) -> dict[str, list[float]]:
    """Drain an aevaluate() result iterator once and bucket every numeric
    score by evaluator key. Use this instead of iterating `results` twice —
    the underlying async iterator can only be consumed one time."""
    by_key: dict[str, list[float]] = defaultdict(list)
    async for row in results:
        for r in row.get("evaluation_results", {}).get("results", []):
            key = getattr(r, "key", None) or (r.get("key") if isinstance(r, dict) else None)
            score = getattr(r, "score", None)
            if score is None and isinstance(r, dict):
                score = r.get("score")
            if key is None or score is None:
                continue
            by_key[str(key)].append(float(score))
    return dict(by_key)


def mean(scores: list[float]) -> float:
    """Mean of a non-empty list of scores. Raises if empty (no examples ran)."""
    if not scores:
        raise AssertionError("No scores produced — did the dataset have examples?")
    return sum(scores) / len(scores)
