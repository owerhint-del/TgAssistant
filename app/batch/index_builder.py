"""
IndexBuilder: создаёт тематическую папку с INDEX.md, index.json и пронумерованными подпапками.

Структура:
  topics/<slug>/
  ├── INDEX.md
  ├── index.json
  ├── 01_label_slug/
  │   ├── source_url.txt
  │   ├── label.txt
  │   └── artifacts -> /abs/path/...
  └── 02_label_slug/
      └── ...
"""
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.batch.note_parser import slugify

logger = logging.getLogger("tgassistant.batch.index")


@dataclass
class IndexEntry:
    index: int          # 1-based
    url: str
    label: str
    group: str
    status: str         # "done" | "error" | "skipped"
    folder: str         # имя подпапки
    artifact_dir: Optional[str]  # абсолютный путь к артефактам


def build(
    topic: str,
    items: list,  # list[BatchItemResult]
    output_dir: str,
    use_symlinks: bool = True,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> str:
    """
    Создаёт тематическую папку с индексами.

    Returns:
        Абсолютный путь к topic_dir.
    """
    topic_slug = slugify(topic)
    topics_root = Path(output_dir).expanduser() / "collected" / "topics"
    topic_dir = topics_root / topic_slug
    topic_dir.mkdir(parents=True, exist_ok=True)

    # Собираем IndexEntry для каждого элемента
    index_entries: list[IndexEntry] = []
    for item in items:
        idx = item.index
        label_slug = slugify(item.entry.label, max_length=40) if item.entry.label else "link"
        folder_name = f"{idx:02d}_{label_slug}"

        # Определяем статус и путь к артефактам
        if item.success:
            status = "done"
            artifact_dir = item.artifact_dir
        elif item.error:
            status = "error"
            artifact_dir = None
        else:
            status = "skipped"
            artifact_dir = None

        index_entries.append(IndexEntry(
            index=idx,
            url=item.entry.url,
            label=item.entry.label or item.entry.url,
            group=item.entry.group,
            status=status,
            folder=folder_name,
            artifact_dir=artifact_dir,
        ))

        # Создаём подпапку
        entry_dir = topic_dir / folder_name
        entry_dir.mkdir(parents=True, exist_ok=True)

        # source_url.txt
        (entry_dir / "source_url.txt").write_text(item.entry.url + "\n", encoding="utf-8")

        # label.txt
        if item.entry.label:
            (entry_dir / "label.txt").write_text(item.entry.label + "\n", encoding="utf-8")

        # Симлинк / копия артефактов
        if artifact_dir and Path(artifact_dir).exists():
            artifacts_link = entry_dir / "artifacts"
            if artifacts_link.exists() or artifacts_link.is_symlink():
                if artifacts_link.is_symlink():
                    artifacts_link.unlink()
                elif artifacts_link.is_dir():
                    shutil.rmtree(artifacts_link)
            if use_symlinks:
                artifacts_link.symlink_to(Path(artifact_dir).resolve())
            else:
                shutil.copytree(artifact_dir, artifacts_link)

    # Группируем для INDEX.md
    groups = _group_entries(index_entries)
    succeeded = sum(1 for e in index_entries if e.status == "done")
    total = len(index_entries)

    # INDEX.md
    ts = (started_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    md = _build_markdown(topic, ts, succeeded, total, groups)
    (topic_dir / "INDEX.md").write_text(md, encoding="utf-8")

    # index.json
    js = _build_json(topic, topic_slug, succeeded, total, groups, started_at, finished_at)
    (topic_dir / "index.json").write_text(
        json.dumps(js, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    logger.info("Topic index built: %s (%d/%d)", topic_dir, succeeded, total)
    return str(topic_dir)


def _group_entries(entries: list[IndexEntry]) -> list[tuple[str, list[IndexEntry]]]:
    """Группирует записи по group, сохраняя порядок появления."""
    groups: dict[str, list[IndexEntry]] = {}
    for e in entries:
        key = e.group or "BASE"
        if key not in groups:
            groups[key] = []
        groups[key].append(e)
    return list(groups.items())


def _build_markdown(
    topic: str,
    timestamp: str,
    succeeded: int,
    total: int,
    groups: list[tuple[str, list[IndexEntry]]],
) -> str:
    lines = [
        f"# {topic}",
        "",
        f"Batch: {timestamp} | {succeeded}/{total} succeeded",
        "",
    ]

    for group_name, entries in groups:
        lines.append(f"## {group_name}")
        lines.append("")
        lines.append("| # | Label | URL | Status | Path |")
        lines.append("|---|-------|-----|--------|------|")
        for e in entries:
            status_icon = "done" if e.status == "done" else "error" if e.status == "error" else "skip"
            url_display = f"[link]({e.url})"
            path_display = f"[{e.folder}](./{e.folder}/)" if e.status == "done" else "—"
            lines.append(
                f"| {e.index:02d} | {e.label} | {url_display} | {status_icon} | {path_display} |"
            )
        lines.append("")

    return "\n".join(lines)


def _build_json(
    topic: str,
    topic_slug: str,
    succeeded: int,
    total: int,
    groups: list[tuple[str, list[IndexEntry]]],
    started_at: Optional[datetime],
    finished_at: Optional[datetime],
) -> dict:
    return {
        "version": 1,
        "topic": topic,
        "topic_slug": topic_slug,
        "created_at": (started_at or datetime.now()).isoformat(),
        "finished_at": (finished_at or datetime.now()).isoformat(),
        "total": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "groups": [
            {
                "prefix": group_name,
                "entries": [
                    {
                        "index": e.index,
                        "url": e.url,
                        "label": e.label,
                        "status": e.status,
                        "folder": e.folder,
                        "artifact_dir": e.artifact_dir,
                    }
                    for e in entries
                ],
            }
            for group_name, entries in groups
        ],
    }
