"""Validate curated identities without rebuilding canonical knowledge."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge.catalog import CARDS  # noqa: E402
from utils.knowledge import KnowledgeStore  # noqa: E402


def build(path):
    """Check curated keys without replacing edited knowledge.

    Args:
        path: Database to inspect.

    Returns:
        Number of curated cards.

    Raises:
        ValueError: A curated card is missing.
    """
    store = KnowledgeStore(path)
    with store.connect() as db:
        keys = {r[0] for r in db.execute("SELECT key FROM entries")}
    missing = {f"card:{c[0]}" for c in CARDS} - keys
    if missing:
        raise ValueError(f"Missing cards: {missing}")
    return {"cards": len(CARDS)}


if __name__ == "__main__":
    print(build(sys.argv[1]))
