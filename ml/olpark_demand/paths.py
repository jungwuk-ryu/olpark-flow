"""Where the two halves of the repository are.

The Python side (this package with its data, artifacts, experiments and tests) lives under
``ml/``, and every command is run from there. The dashboard's files (``src/data``), the
documents and Team B's files are in the repository around it.
"""
from pathlib import Path

ML = Path(__file__).resolve().parents[1]
REPO = ML.parent


def repo_of(root) -> Path:
    """The repository that holds a Python-side root."""
    return Path(root).resolve().parent
