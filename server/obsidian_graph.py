from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]")


@dataclass
class NoteLookup:
    by_path: dict[str, Any]
    by_path_stem: dict[str, list[Any]]
    by_basename: dict[str, list[Any]]
    by_title: dict[str, list[Any]]


def extract_wikilinks(content: str) -> list[str]:
    return [
        normalized
        for match in WIKILINK_RE.findall(content or "")
        if (normalized := str(match).strip())
    ]


def build_note_lookup(notes: Iterable[Any]) -> NoteLookup:
    by_path: dict[str, Any] = {}
    by_path_stem: dict[str, list[Any]] = {}
    by_basename: dict[str, list[Any]] = {}
    by_title: dict[str, list[Any]] = {}
    for note in notes:
        by_path[note.path] = note
        path_stem = note.path[:-3] if note.path.endswith(".md") else note.path
        by_path_stem.setdefault(path_stem, []).append(note)
        by_basename.setdefault(Path(note.path).stem, []).append(note)
        by_title.setdefault(note.title, []).append(note)
    return NoteLookup(
        by_path=by_path,
        by_path_stem=by_path_stem,
        by_basename=by_basename,
        by_title=by_title,
    )


def resolve_wikilink(target_name: str, lookup_or_notes: Any) -> Any | None:
    lookup = (
        lookup_or_notes
        if isinstance(lookup_or_notes, NoteLookup)
        else build_note_lookup(lookup_or_notes)
    )
    stem_matches = lookup.by_path_stem.get(target_name, [])
    if len(stem_matches) == 1:
        return stem_matches[0]
    if target_name in lookup.by_path:
        return lookup.by_path[target_name]
    basename_matches = lookup.by_basename.get(target_name, [])
    if len(basename_matches) == 1:
        return basename_matches[0]
    title_matches = lookup.by_title.get(target_name, [])
    if len(title_matches) == 1:
        return title_matches[0]
    return None


def desired_wikilink_edges(
    source_notes: Iterable[Any], all_notes: Iterable[Any]
) -> set[tuple[Any, Any, str]]:
    lookup = build_note_lookup(all_notes)
    edges: set[tuple[Any, Any, str]] = set()
    for note in source_notes:
        for target_name in extract_wikilinks(note.content or ""):
            target = resolve_wikilink(target_name, lookup)
            if target is not None and target.note_id != note.note_id:
                edges.add((note.note_id, target.note_id, "wikilink"))
    return edges
