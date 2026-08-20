"""Fail closed if a public echr-py source distribution crosses its boundary."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath

if __package__:
    from .check_wheel import check_archive_contents
else:  # pragma: no cover - exercised by the release command
    from check_wheel import check_archive_contents


def _normalise_member(name: str) -> str:
    parts = PurePosixPath(name).parts
    return PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else ""


def check_sdist(path: str | Path) -> None:
    sdist = Path(path)
    with tarfile.open(sdist, "r:gz") as archive:
        all_members = archive.getmembers()
        unsafe_types = sorted(
            member.name for member in all_members if not (member.isfile() or member.isdir())
        )
        if unsafe_types:
            raise SystemExit(
                f"sdist content check failed: unsupported archive members: {unsafe_types}"
            )
        files = [member for member in all_members if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in all_members}
        if len(roots) != 1:
            raise SystemExit(f"sdist content check failed: expected one archive root, got {roots}")
        normalised = [_normalise_member(member.name) for member in files]
        members = {name: member for name, member in zip(normalised, files, strict=True)}

        def read_bytes(name: str) -> bytes:
            extracted = archive.extractfile(members[name])
            if extracted is None:
                raise SystemExit(f"sdist content check failed: could not read {name}")
            return extracted.read()

        check_archive_contents(normalised, read_bytes, artifact="sdist")
    print(f"sdist content check passed: {sdist}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sdist")
    args = parser.parse_args()
    check_sdist(args.sdist)


if __name__ == "__main__":
    main()
