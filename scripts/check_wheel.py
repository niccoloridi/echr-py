"""Fail closed when an echr-py distribution differs from its public contract."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import stat
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath


def _load_public_package_paths() -> frozenset[str]:
    manifest = Path(__file__).with_name("public_package_files.txt")
    return frozenset(
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


PUBLIC_PACKAGE_PATHS = _load_public_package_paths()

def _load_payload_hashes() -> dict[str, str]:
    manifest = Path(__file__).with_name("public_payload_hashes.json")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in value.items()
    ):
        raise RuntimeError("public payload hash manifest must be a string mapping")
    return value


PUBLIC_PAYLOAD_HASHES = _load_payload_hashes()

WHEEL_METADATA_PATHS = frozenset(
    {
        "METADATA",
        "WHEEL",
        "entry_points.txt",
        "top_level.txt",
        "RECORD",
        "licenses/LICENSE",
        "licenses/RIGHTS.md",
    }
)

SDIST_ROOT_PATHS = frozenset(
    {
        "CITATION.cff",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "RIGHTS.md",
        "SECURITY.md",
        "pyproject.toml",
        "release-requirements.txt",
        "setup.cfg",
        "scripts/check_sdist.py",
        "scripts/check_wheel.py",
        "scripts/public_package_files.txt",
        "scripts/public_payload_hashes.json",
        "scripts/verify_release.py",
    }
)

SDIST_EGG_INFO_PATHS = frozenset(
    {
        "echr_py.egg-info/PKG-INFO",
        "echr_py.egg-info/SOURCES.txt",
        "echr_py.egg-info/dependency_links.txt",
        "echr_py.egg-info/entry_points.txt",
        "echr_py.egg-info/requires.txt",
        "echr_py.egg-info/top_level.txt",
    }
)


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and "\\" not in name and ".." not in path.parts


def _python_diagnostics(name: str, payload: bytes) -> list[str]:
    """Reject executable-source primitives that have no place in this package."""
    try:
        tree = ast.parse(payload.decode("utf-8"), filename=name)
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [f"{name}: invalid Python source ({exc})"]
    diagnostics: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec"}
        ):
            diagnostics.append(f"{name}:{node.lineno}: call to {node.func.id}()")
    return diagnostics


def _check_package_payloads(
    names: set[str],
    read_bytes: Callable[[str], bytes],
) -> list[str]:
    diagnostics: list[str] = []
    for name, expected in PUBLIC_PAYLOAD_HASHES.items():
        if name not in names:
            continue
        actual = hashlib.sha256(read_bytes(name)).hexdigest()
        if actual != expected:
            diagnostics.append(f"{name}: expected {expected}, got {actual}")
    for name in sorted(names & PUBLIC_PACKAGE_PATHS):
        if name.endswith(".py"):
            diagnostics.extend(_python_diagnostics(name, read_bytes(name)))
    return diagnostics


def _wheel_allowed_paths(names: set[str]) -> tuple[set[str], list[str]]:
    roots = {name.split("/", 1)[0] for name in names if ".dist-info/" in name}
    valid_roots = {
        root
        for root in roots
        if root.startswith("echr_py-") and root.endswith(".dist-info")
    }
    diagnostics: list[str] = []
    if roots != valid_roots or len(valid_roots) != 1:
        diagnostics.append(
            "expected exactly one echr_py-*.dist-info directory, "
            f"got {sorted(roots)}"
        )
        return set(PUBLIC_PACKAGE_PATHS), diagnostics
    root = next(iter(valid_roots))
    metadata = {f"{root}/{path}" for path in WHEEL_METADATA_PATHS}
    return set(PUBLIC_PACKAGE_PATHS) | metadata, diagnostics


def check_archive_contents(
    names: Iterable[str],
    read_bytes: Callable[[str], bytes],
    *,
    artifact: str,
) -> None:
    """Validate every member of an unpacked wheel or source distribution."""
    members = [name for name in names if name and not name.endswith("/")]
    counts = Counter(members)
    duplicate = sorted(name for name, count in counts.items() if count > 1)
    unsafe = sorted(name for name in members if not _safe_member_name(name))
    member_set = set(members)

    if artifact == "wheel":
        allowed, structure = _wheel_allowed_paths(member_set)
    elif artifact == "sdist":
        allowed = set(PUBLIC_PACKAGE_PATHS | SDIST_ROOT_PATHS | SDIST_EGG_INFO_PATHS)
        structure = []
    else:
        raise ValueError(f"unsupported artifact kind: {artifact}")

    unexpected = sorted(member_set - allowed)
    missing = sorted(allowed - member_set)
    payload = _check_package_payloads(member_set, read_bytes)
    if duplicate or unsafe or structure or unexpected or missing or payload:
        details = []
        if duplicate:
            details.append(f"duplicate members: {duplicate}")
        if unsafe:
            details.append(f"unsafe member paths: {unsafe}")
        if structure:
            details.append(f"invalid archive structure: {structure}")
        if unexpected:
            details.append(f"unexpected files: {unexpected}")
        if missing:
            details.append(f"missing files: {missing}")
        if payload:
            details.append(f"invalid payloads: {payload}")
        raise SystemExit(f"{artifact} content check failed: " + "; ".join(details))


def check_wheel(path: str | Path) -> None:
    wheel = Path(path)
    with zipfile.ZipFile(wheel) as archive:
        symlinks = sorted(
            member.filename
            for member in archive.infolist()
            if stat.S_ISLNK(member.external_attr >> 16)
        )
        if symlinks:
            raise SystemExit(f"wheel content check failed: symbolic links: {symlinks}")
        check_archive_contents(archive.namelist(), archive.read, artifact="wheel")
    print(f"wheel content check passed: {wheel}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel")
    args = parser.parse_args()
    check_wheel(args.wheel)


if __name__ == "__main__":
    main()
