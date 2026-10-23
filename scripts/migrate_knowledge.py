"""Import Markdown losslessly; delete originals only after database verification."""

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.knowledge import KnowledgeStore  # noqa: E402

TOPICS = {
    "plugin-config": "插件配置",
    "astrbot-config": "宿主配置",
    "plugin-new": "开发环境与发布",
    "simple": "插件结构与生命周期",
    "env": "开发环境与发布",
    "plugin": "插件结构与生命周期",
    "ai": "LLM与回复",
    "listen-message-event": "事件与指令",
    "send-message": "消息发送",
    "storage": "数据存储",
    "session-control": "会话控制",
    "html-to-pic": "图片渲染",
    "openapi": "HTTP接口",
    "other": "平台接口与插件信息",
    "plugin-platform-adapter": "平台适配器",
}


def sections(text):
    chunks, current, title = [], [], "导言"
    fence = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            char = marker.group()[0]
            if fence is None:
                fence = char
            elif fence == char:
                fence = None
        heading = re.match(r"#{1,4}\s+(.+)", line) if fence is None else None
        if heading:
            if current:
                chunks.append((title, "".join(current)))
            title, current = heading.group(1).strip(), []
        current.append(line)
    if current:
        chunks.append((title, "".join(current)))
    return chunks


def migrate(source, destination, delete=False):
    """Preserve raw source bytes and index Markdown sections.

    Args:
        source: Directory containing Markdown sources.
        destination: SQLite database outside the source directory.
        delete: Delete only verified originals after all checks succeed.

    Returns:
        Source count, section count and deletion status.

    Raises:
        ValueError: Source content conflicts or integrity verification fails.
    """
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError("Database must be outside the Markdown source directory")
    files = sorted(source.glob("*.md"))
    if not files:
        raise ValueError("No Markdown sources found")
    store = KnowledgeStore(destination)
    originals = []
    count = 0
    for path in files:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8-sig")
        with store.connect() as db:
            old = db.execute(
                "SELECT sha256 FROM sources WHERE name=?", (path.name,)
            ).fetchone()
            if old and old[0] != digest:
                raise ValueError(
                    f"Source changed since previous migration: {path.name}"
                )
            db.execute(
                "INSERT OR IGNORE INTO sources VALUES(?,?,?)", (path.name, digest, raw)
            )
        for i, (title, content) in enumerate(sections(text)):
            category = TOPICS.get(path.stem, "其他资料")
            if path.stem == "ai":
                if any(k in title for k in ("Tool", "Agent", "工具")):
                    category = "LLM工具与Agent"
                elif any(k in title for k in ("conversation", "会话", "对话")):
                    category = "会话与历史"
                elif any(k in title for k in ("persona", "人格")):
                    category = "人格管理"
            if any(
                k in title
                for k in ("消息链", "消息段", "富媒体", "消息对象", "message` 字段")
            ):
                category = "消息格式"
            store.save(
                f"doc:{path.name}:{i}",
                category,
                title,
                content if content.strip() else "（空章节）",
                f"{path.name}#{title}",
            )
            count += 1
        originals.append((path, raw, digest))
    with store.connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        for path, raw, digest in originals:
            saved = db.execute(
                "SELECT raw,sha256 FROM sources WHERE name=?", (path.name,)
            ).fetchone()
            if bytes(saved[0]) != raw or saved[1] != digest:
                raise ValueError(f"Byte verification failed: {path.name}")
    if delete:
        # Preflight every source before deleting any original file.
        for path, raw, _ in originals:
            if path.read_bytes() != raw or path.parent != source or path.is_symlink():
                raise ValueError("Source changed or linked before deletion")
        for path, _, _ in originals:
            path.unlink()
    return {"sources": len(originals), "sections": count, "deleted": delete}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--delete-originals", action="store_true")
    args = parser.parse_args()
    print(migrate(args.source, args.destination, args.delete_originals))
