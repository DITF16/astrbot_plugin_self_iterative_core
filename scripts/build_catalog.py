"""Materialize the explicitly authored editorial catalog and check coverage."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge.catalog import ARCHIVE_ONLY, CARDS, REPLACEMENTS  # noqa: E402
from utils.knowledge import KnowledgeStore  # noqa: E402


def source_key(member):
    return member[1:] if member.startswith("@") else f"doc:{member}"


def build(path):
    """Copy editorial decisions, failing if any old entry is unaccounted for.

    Args:
        path: Seed database to update.

    Returns:
        Number of curated cards and original entries accounted for.

    Raises:
        ValueError: The manual catalog omits an original entry.
    """
    store = KnowledgeStore(path)
    with store.connect() as db:
        originals = {
            r["key"]: dict(r)
            for r in db.execute("SELECT * FROM entries WHERE key NOT LIKE 'card:%'")
        }
    covered = {source_key(m) for card in CARDS for m in card[3]}
    covered.update(source_key(m) for m in ARCHIVE_ONLY)
    covered.update(source_key(m) for m in REPLACEMENTS)
    if set(originals) - covered or covered - set(originals):
        raise ValueError(
            f"Catalog coverage mismatch: omitted={set(originals) - covered}, missing={covered - set(originals)}"
        )
    for key, category, title, members, summary, aliases, apis, related in CARDS:
        content = f"# {title}\n\n{summary}\n\n" + "\n\n".join(
            f"来源：{originals[source_key(m)]['source']}\n{originals[source_key(m)]['content']}"
            for m in members
        )
        kind = "experience" if category.startswith("经验/") else "reference"
        store.save(f"card:{key}", category, title, content, "; ".join(members), kind)
    for key, category, title, members, summary, aliases, apis, related in CARDS:
        store.set_profile(
            f"card:{key}",
            summary,
            aliases,
            apis,
            [f"card:{r}" for r in related],
            [source_key(m) for m in members],
            applicability="原文适用版本见正文；手工整理基于 AstrBot 4.29.1，示例未逐项运行",
            verification="documented",
            origin="packaged",
        )
    for key in originals:
        store.set_profile(key, status="archived", origin="packaged")
    for member, replacement in REPLACEMENTS.items():
        store.set_profile(
            source_key(member),
            status="superseded",
            replacement=f"card:{replacement}",
            origin="packaged",
        )
    return {"cards": len(CARDS), "original_entries_accounted_for": len(originals)}


if __name__ == "__main__":
    print(build(sys.argv[1]))
