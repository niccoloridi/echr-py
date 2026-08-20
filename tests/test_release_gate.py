from pathlib import Path

import pytest

from scripts.verify_release import verify_release


def _project(tmp_path: Path, version: str = "0.2.0") -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "echr-py"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_release_gate_accepts_matching_tag_and_artifacts(tmp_path):
    root = _project(tmp_path)
    assert verify_release(
        [Path("echr_py-0.2.0-py3-none-any.whl"), Path("echr_py-0.2.0.tar.gz")],
        event_tag="v0.2.0",
        require_tag=True,
        root=root,
    ) == "0.2.0"


def test_release_gate_rejects_wrong_tag(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        verify_release([], event_tag="v0.1.0", require_tag=True, root=_project(tmp_path))


def test_release_gate_rejects_untagged_publication(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    with pytest.raises(ValueError, match="requires a tag"):
        verify_release([], require_tag=True, root=_project(tmp_path))


def test_release_gate_rejects_wrong_artifact_version(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        verify_release([Path("echr_py-0.1.0.tar.gz")], root=_project(tmp_path))
