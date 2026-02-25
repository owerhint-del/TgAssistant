# v0.4.0 — Relative Export Paths + Media Cleanup CLI

## Relative paths in DB

- New export records store file paths relative to `output_dir` (e.g. `collected/channel/123/text.txt`)
- If you change `OUTPUT_DIR` in config, new exports resolve correctly through the new location
- Resolution happens transparently at the database layer — all consumers (web UI, bot, batch runner) work without changes

## Backward compatibility

- Old absolute paths in existing records are read as-is — no migration needed
- Mixed DB (old absolute + new relative) works correctly
- `_resolve_path()` detects absolute vs relative and handles each appropriately

## Media cleanup CLI

```
python run.py --cleanup                     # delete video/audio older than 7 days
python run.py --cleanup --older-than 30     # older than 30 days
python run.py --cleanup --dry-run           # preview only, no deletion
```

- Targets: `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.wav`, `.ogg`, `.mp3`, `.aac`, `.flac` and more
- Preserves: `text.txt`, `transcript.txt`, `meta.json`, `manifest.json`, images
- Scans `collected/` recursively, including `external/` subdirectories

## Symlink safety

- Cleanup skips symlinks (batch index `artifacts` links) to prevent deleting files outside `collected/`
- `rglob` traversal guards against symlink-following

## Bug fixes

- `exports.py` download route now uses thread-safe `db.get_export_by_id()` instead of raw SQL
- `--cleanup --dry-run` no longer triggers orphan cleanup side effects

## Tests

- 220 passed, 4 skipped (pre-existing PDF font skips)
- 13 new tests for relative path conversion + backward compatibility
- 12 new tests for media cleanup logic
