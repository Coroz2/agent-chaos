"""Verify release archives contain the expected Agent Chaos package files."""

from __future__ import annotations

import argparse
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

DISALLOWED_PARTS = {
    ".agentchaos",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    expected_name = project["name"]
    expected_version = project["version"]
    wheel = _only(args.dist_dir.glob("*.whl"), "wheel")
    source = _only(args.dist_dir.glob("*.tar.gz"), "source distribution")

    _verify_wheel(wheel, expected_name, expected_version)
    _verify_source(source)
    print(f"Verified {wheel.name} and {source.name}")


def _only(paths: Iterable[Path], label: str) -> Path:
    found = list(paths)
    if len(found) != 1:
        raise SystemExit(f"expected exactly one {label}, found {len(found)}")
    return found[0]


def _verify_wheel(path: Path, expected_name: str, expected_version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        _reject_local_artifacts(names, path)
        _require(names, "agentchaos/py.typed", path)
        _require_suffix(names, ".dist-info/licenses/LICENSE", path)
        metadata_name = _single_suffix(names, ".dist-info/METADATA", path)
        metadata = BytesParser().parsebytes(archive.read(metadata_name))

    if metadata["Name"] != expected_name:
        raise SystemExit(
            f"{path}: expected package name {expected_name!r}, got {metadata['Name']!r}"
        )
    if metadata["Version"] != expected_version:
        raise SystemExit(
            f"{path}: expected version {expected_version!r}, got {metadata['Version']!r}"
        )


def _verify_source(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    _reject_local_artifacts(names, path)
    for suffix in (
        "/LICENSE",
        "/README.md",
        "/pyproject.toml",
        "/src/agentchaos/py.typed",
    ):
        _require_suffix(names, suffix, path)


def _reject_local_artifacts(names: list[str], path: Path) -> None:
    rejected = [name for name in names if DISALLOWED_PARTS.intersection(PurePosixPath(name).parts)]
    if rejected:
        raise SystemExit(f"{path}: contains local artifacts: {', '.join(rejected[:5])}")


def _require(names: list[str], expected: str, path: Path) -> None:
    if expected not in names:
        raise SystemExit(f"{path}: missing {expected}")


def _require_suffix(names: list[str], suffix: str, path: Path) -> None:
    if not any(name.endswith(suffix) for name in names):
        raise SystemExit(f"{path}: missing *{suffix}")


def _single_suffix(names: list[str], suffix: str, path: Path) -> str:
    found = [name for name in names if name.endswith(suffix)]
    if len(found) != 1:
        raise SystemExit(f"{path}: expected one *{suffix}, found {len(found)}")
    return found[0]


if __name__ == "__main__":
    main()
