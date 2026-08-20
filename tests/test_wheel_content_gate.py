"""The release wheel must stay inside its explicit public module boundary."""

from __future__ import annotations

import stat
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from scripts.check_sdist import check_sdist
from scripts.check_wheel import (
    PUBLIC_PACKAGE_PATHS,
    SDIST_EGG_INFO_PATHS,
    SDIST_ROOT_PATHS,
    WHEEL_METADATA_PATHS,
    check_wheel,
)


def _content(name: str) -> bytes:
    path = Path(name)
    return path.read_bytes() if path.is_file() else b""


def _wheel(tmp_path, *extra_paths: str, content_by_path=None):
    path = tmp_path / "release.whl"
    dist_info = "echr_py-0.2.0.dist-info"
    names = {
        *PUBLIC_PACKAGE_PATHS,
        *(f"{dist_info}/{name}" for name in WHEEL_METADATA_PATHS),
        *extra_paths,
    }
    content_by_path = content_by_path or {}
    with zipfile.ZipFile(path, "w") as archive:
        for name in sorted(names):
            archive.writestr(name, content_by_path.get(name, _content(name)))
    return path


def test_public_wheel_allowlist_accepts_exact_supported_surface(tmp_path):
    check_wheel(_wheel(tmp_path))


def test_public_wheel_requires_rights_notice(tmp_path):
    wheel = _wheel(tmp_path)
    without_notice = tmp_path / "without-notice.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(without_notice, "w") as target:
        for name in source.namelist():
            if not name.endswith(".dist-info/licenses/RIGHTS.md"):
                target.writestr(name, source.read(name))
    with pytest.raises(SystemExit, match="missing files"):
        check_wheel(without_notice)


@pytest.mark.parametrize(
    "unexpected",
    [
        "hudoc_py/unapproved_module.py",
        "hudoc_py_extra/__init__.py",
        "echr_py_boot.pth",
        "payload.js",
    ],
)
def test_public_wheel_allowlist_rejects_every_unapproved_member(tmp_path, unexpected):
    with pytest.raises(SystemExit, match="wheel content check failed"):
        check_wheel(_wheel(tmp_path, unexpected))


def test_public_wheel_rejects_modified_vendored_payload(tmp_path):
    path = "hudoc_py/graphs/assets/d3.v7.min.js"
    wheel = _wheel(
        tmp_path,
        content_by_path={path: b"modified payload"},
    )
    with pytest.raises(SystemExit, match="invalid payloads"):
        check_wheel(wheel)


def test_public_wheel_rejects_dynamic_source_execution(tmp_path):
    path = "hudoc_py/thesaurus.py"
    wheel = _wheel(tmp_path, content_by_path={path: b"exec(b'untrusted')\n"})
    with pytest.raises(SystemExit, match=r"call to exec\(\)"):
        check_wheel(wheel)


def test_public_wheel_rejects_symbolic_links(tmp_path):
    wheel = _wheel(tmp_path)
    linked = tmp_path / "linked.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(linked, "w") as target:
        for member in source.infolist():
            if member.filename == "hudoc_py/__init__.py":
                member.external_attr = (stat.S_IFLNK | 0o777) << 16
                target.writestr(member, "../outside")
            else:
                target.writestr(member, source.read(member.filename))
    with pytest.raises(SystemExit, match="symbolic links"):
        check_wheel(linked)


def _sdist(tmp_path, *extra_paths: str, content_by_path=None):
    path = tmp_path / "echr_py-0.2.0.tar.gz"
    names = {
        *PUBLIC_PACKAGE_PATHS,
        *SDIST_ROOT_PATHS,
        *SDIST_EGG_INFO_PATHS,
        *extra_paths,
    }
    content_by_path = content_by_path or {}
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(names):
            content = content_by_path.get(name, _content(name))
            member = tarfile.TarInfo(f"echr_py-0.2.0/{name}")
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
    return path


def test_public_sdist_allowlist_accepts_exact_supported_surface(tmp_path):
    check_sdist(_sdist(tmp_path))


@pytest.mark.parametrize(
    "unexpected",
    [
        "hudoc_py/unapproved_module.py",
        "hudoc_py_extra/__init__.py",
        "setup.py",
        "install_hook.pth",
    ],
)
def test_public_sdist_rejects_every_unapproved_member(tmp_path, unexpected):
    with pytest.raises(SystemExit, match="sdist content check failed"):
        check_sdist(_sdist(tmp_path, unexpected))


def test_public_sdist_rejects_modified_data_payload(tmp_path):
    path = "hudoc_py/data/citation_authority_supplements.json"
    with pytest.raises(SystemExit, match="invalid payloads"):
        check_sdist(
            _sdist(
                tmp_path,
                content_by_path={path: b"{}"},
            )
        )


def test_public_sdist_rejects_symbolic_links(tmp_path):
    source = _sdist(tmp_path)
    linked = tmp_path / "linked.tar.gz"
    with tarfile.open(source, "r:gz") as old, tarfile.open(linked, "w:gz") as new:
        for member in old.getmembers():
            if member.name.endswith("/hudoc_py/__init__.py"):
                link = tarfile.TarInfo(member.name)
                link.type = tarfile.SYMTYPE
                link.linkname = "../outside"
                new.addfile(link)
            else:
                payload = old.extractfile(member) if member.isfile() else None
                new.addfile(member, payload)
    with pytest.raises(SystemExit, match="unsupported archive members"):
        check_sdist(linked)
