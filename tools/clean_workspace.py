"""Remove regenerable bulk from the project tree and record exactly what was removed.

Three categories, all reconstructible, none of them evidence:

* **virtual environments** — identified by `pyvenv.cfg`, not by directory name, so mypy's
  bundled `typeshed/stdlib/venv` stubs are not mistaken for one. The active environment
  `blackridge-systems/.venv` is always kept.
* **tool caches** — `__pycache__`, `.ruff_cache`, `.mypy_cache`, `.pytest_cache`.
* **model weights** — only with `--weights`. These belong to the scientific-auditor line, which
  is a separate project. They are public checkpoints, their SHA-256 values are recorded in
  `benchmarks/scientific-claim-auditor-v1/manual-findings.md`, and every metric, report, and
  manual finding derived from them stays on disk.

Retained JSON evidence, reports, manual findings, frozen archives, and git history are never
touched. Default is a dry run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ACTIVE_VENV = (ROOT / "blackridge-systems" / ".venv").resolve()
CACHE_NAMES = {"__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache"}
WEIGHT_SUFFIXES = {".bin", ".safetensors", ".pt", ".pth", ".onnx", ".ckpt"}


def _directory_size(path: Path) -> tuple[int, int]:
    total = 0
    count = 0
    for directory, _subdirs, names in os.walk(path, onerror=lambda _: None):
        for name in names:
            try:
                total += os.stat(os.path.join(directory, name)).st_size
                count += 1
            except OSError:
                continue
    return total, count


def _collect() -> tuple[list[Path], list[Path], list[Path]]:
    """Find virtual environments, caches, and weight files in one walk."""

    venvs: list[Path] = []
    caches: list[Path] = []
    weights: list[Path] = []
    skip_prefixes: list[str] = []

    for directory, subdirs, names in os.walk(ROOT, onerror=lambda _: None):
        current = Path(directory)
        if any(directory.startswith(prefix) for prefix in skip_prefixes):
            subdirs[:] = []
            continue
        if "pyvenv.cfg" in names:
            if current.resolve() != ACTIVE_VENV:
                venvs.append(current)
                skip_prefixes.append(directory + os.sep)
            subdirs[:] = []
            continue
        for name in list(subdirs):
            if name in CACHE_NAMES:
                caches.append(current / name)
                subdirs.remove(name)
        for name in names:
            if Path(name).suffix.lower() in WEIGHT_SUFFIXES:
                weights.append(current / name)
    return venvs, caches, weights


def _gb(value: int) -> str:
    return f"{value / 1024**3:.2f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete.")
    parser.add_argument(
        "--weights",
        action="store_true",
        help="Also remove model weights belonging to the separate scientific-auditor line.",
    )
    args = parser.parse_args()

    venvs, caches, weights = _collect()
    manifest: dict[str, object] = {"schema_version": "1", "root": str(ROOT), "removed": {}}
    removed: dict[str, object] = manifest["removed"]  # type: ignore[assignment]
    total_bytes = 0
    total_files = 0

    for label, paths, restore in (
        ("virtual_environments", venvs, "python -m venv <path> && pip install -e '.[dev]'"),
        ("tool_caches", caches, "regenerated automatically by the next run"),
    ):
        entries = []
        for path in paths:
            size, count = _directory_size(path)
            total_bytes += size
            total_files += count
            entries.append({"path": str(path.relative_to(ROOT)), "bytes": size, "files": count})
            if args.apply:
                shutil.rmtree(path, ignore_errors=True)
        removed[label] = {"restore": restore, "count": len(entries), "entries": entries}
        print(
            f"{label:<22} {len(entries):>4} paths  "
            f"{_gb(sum(int(e['bytes']) for e in entries)):>10}  "
            f"{sum(int(e['files']) for e in entries):,} files"
        )

    if args.weights:
        entries = []
        for path in weights:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total_bytes += size
            total_files += 1
            entries.append({"path": str(path.relative_to(ROOT)), "bytes": size})
            if args.apply:
                path.unlink(missing_ok=True)
        removed["model_weights"] = {
            "restore": (
                "Public checkpoints. Identities and SHA-256 values are recorded in "
                "blackridge-systems/benchmarks/scientific-claim-auditor-v1/manual-findings.md; "
                "re-extract with tools/extract_multivers_checkpoint.py."
            ),
            "count": len(entries),
            "entries": entries,
        }
        print(
            f"{'model_weights':<22} {len(entries):>4} files  "
            f"{_gb(sum(int(e['bytes']) for e in entries)):>10}"
        )
    else:
        size = sum(p.stat().st_size for p in weights if p.exists())
        print(f"{'model_weights':<22} {len(weights):>4} files  {_gb(size):>10}  (pass --weights)")

    print(f"\ntotal: {_gb(total_bytes)} across {total_files:,} files")
    if args.apply:
        manifest_path = ROOT / "blackridge-systems" / "evidence" / "workspace-cleanup.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        print(f"manifest: {manifest_path.relative_to(ROOT)}")
    else:
        print("dry run; nothing was modified")
    print("evidence JSON, reports, manual findings, archives, and git history were not touched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
