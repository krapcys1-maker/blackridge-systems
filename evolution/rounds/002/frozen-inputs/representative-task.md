# Deterministic duplicate-file finder

Build a portable Python 3.11+ command-line program that recursively finds duplicate regular
files by SHA-256. It must never modify input files, directories, permissions, or timestamps. It
must emit deterministic JSON containing duplicate groups and explicit unreadable-file errors,
exclude its output file when that file is inside the scanned tree, reject an output path that is
the same file as any input, handle symbolic links without escaping the requested tree, and include
executable tests. Prefer a verified component only when it materially reduces new code; otherwise
use the Python standard library and state that choice honestly.
