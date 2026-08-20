"""Verify that release refs and distribution filenames match pyproject metadata."""

from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path

WHEEL_RE = re.compile(r"^echr_py-(?P<version>[^-]+)-[^/]+\.whl$")
SDIST_RE = re.compile(r"^echr_py-(?P<version>[^-]+)\.tar\.gz$")


def project_version(root: Path = Path(".")) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def release_tag(*, event_tag: str = "") -> str | None:
    if event_tag:
        return event_tag
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        return os.environ.get("GITHUB_REF_NAME") or None
    return None


def verify_release(
    artifacts: list[Path],
    *,
    event_tag: str = "",
    require_tag: bool = False,
    root: Path = Path("."),
) -> str:
    version = project_version(root)
    tag = release_tag(event_tag=event_tag)
    if require_tag and tag is None:
        raise ValueError("a release or tag-triggered publication requires a tag")
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag {tag!r} does not match project version {version!r}")
    for artifact in artifacts:
        match = WHEEL_RE.fullmatch(artifact.name) or SDIST_RE.fullmatch(artifact.name)
        if match is None:
            raise ValueError(f"unexpected distribution filename: {artifact.name}")
        if match.group("version") != version:
            raise ValueError(
                f"artifact {artifact.name!r} does not match project version {version!r}"
            )
    return version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="*", type=Path)
    parser.add_argument("--event-tag", default="")
    parser.add_argument("--require-tag", action="store_true")
    args = parser.parse_args()
    version = verify_release(
        args.artifacts,
        event_tag=args.event_tag,
        require_tag=args.require_tag,
    )
    print(f"release metadata verified: {version}")


if __name__ == "__main__":
    main()
