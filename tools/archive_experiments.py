"""Report and reclaim disk in the sibling experiment tree without losing evidence.

`blackridge-experiments` holds about 25.7 GB. Most of it is not evidence: identical model
weights copied into every generated bundle, regenerable bytecode caches, and virtual
environments. The retained JSON probes, reports, metrics, and manual findings are the actual
evidence and this tool never touches them.

The project rule is that failed experiments and probes are preserved, so nothing here deletes
a weight file. Duplicates are collapsed with hard links, which keeps every path readable and
every recorded SHA-256 valid because the bytes are unchanged.

Default is a dry run. Nothing is modified without `--apply`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from collections import defaultdict
from pathlib import Path

WEIGHT_SUFFIXES = {".bin", ".safetensors", ".pt", ".pth", ".onnx", ".ckpt"}
# A tamper control's whole purpose is to hold bytes that differ from the trusted copy. Linking
# one to its source would destroy the control.
TAMPER_MARKERS = ("tampered", "tamper-", "broken", "mutated")


def _is_tamper_control(path: Path) -> bool:
    lowered = str(path).lower()
    return any(marker in lowered for marker in TAMPER_MARKERS)


def _walk_files(root: Path) -> list[Path]:
    """Walk the tree, skipping virtual environments and entries the OS cannot stat.

    Experiment venvs contain broken POSIX symlinks that raise on Windows, and they never hold
    weights worth deduplicating.
    """

    files: list[Path] = []
    for directory, subdirectories, names in os.walk(root, onerror=lambda _: None):
        subdirectories[:] = [
            name for name in subdirectories if name not in {".venv", "venv", "site-packages"}
        ]
        for name in names:
            candidate = Path(directory) / name
            try:
                if candidate.is_file():
                    files.append(candidate)
            except OSError:
                continue
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _format_gb(value: int) -> str:
    return f"{value / 1024**3:.2f} GB"


def _duplicate_weights(root: Path) -> dict[tuple[int, str], list[Path]]:
    """Group candidate weight files by size then content hash."""

    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in _walk_files(root):
        if path.suffix.lower() not in WEIGHT_SUFFIXES or _is_tamper_control(path):
            continue
        try:
            by_size[path.stat().st_size].append(path)
        except OSError:
            continue

    groups: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            groups[(size, _sha256(path))].append(path)
    return {key: sorted(paths) for key, paths in groups.items() if len(paths) > 1}


def _cache_directories(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, _ in os.walk(root, onerror=lambda _: None):
        if "__pycache__" in subdirectories:
            found.append(Path(directory) / "__pycache__")
    return found


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in _walk_files(path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent.parent.parent / "blackridge-experiments",
    )
    parser.add_argument("--apply", action="store_true", help="Perform the reported actions.")
    parser.add_argument(
        "--purge-caches",
        action="store_true",
        help="Also remove __pycache__ directories, which Python regenerates on demand.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        print(f"experiment root not found: {root}")
        return 1

    print(f"root: {root}")
    print(f"mode: {'APPLY' if args.apply else 'dry run'}\n")

    reclaimable = 0
    groups = _duplicate_weights(root)
    if groups:
        print("Duplicate model weights (hard-link candidates):")
    for (size, digest), paths in sorted(groups.items(), key=lambda item: -item[0][0]):
        keeper, *copies = paths
        linkable = [path for path in copies if not _same_file(keeper, path)]
        if not linkable:
            continue
        saved = size * len(linkable)
        reclaimable += saved
        print(f"  {digest[:16]}  {_format_gb(size)} x {len(linkable)} copies = {_format_gb(saved)}")
        print(f"    keep: {keeper.relative_to(root)}")
        for path in linkable:
            print(f"    link: {path.relative_to(root)}")
            if args.apply:
                temporary = path.with_suffix(path.suffix + ".relink")
                os.link(keeper, temporary)
                os.replace(temporary, path)

    caches = _cache_directories(root)
    cache_bytes = sum(_directory_size(path) for path in caches)
    if caches:
        print(f"\n__pycache__ directories: {len(caches)} totalling {_format_gb(cache_bytes)}")
        if args.purge_caches:
            reclaimable += cache_bytes
            if args.apply:
                for path in caches:
                    shutil.rmtree(path, ignore_errors=True)
        else:
            print("  (pass --purge-caches to include them)")

    print(f"\nreclaimable: {_format_gb(reclaimable)}")
    if not args.apply:
        print("nothing was modified; re-run with --apply to act")
    print("evidence JSON, reports, metrics, and manual findings were not inspected or touched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
