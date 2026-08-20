"""MCP study jobs fail closed around paths, providers, and budgets."""

import json

import pytest

from hudoc_py.studies import StudyJobManager


def _manager(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    return StudyJobManager(
        output_root=tmp_path / "jobs",
        input_roots=[allowed],
        allowed_providers={"fake"},
        allowed_studies={"allowed-study"},
        pricing={"fake:fake-model": {"input_per_m": 1, "output_per_m": 1}},
        max_job_budget_usd=0.5,
        max_workers=1,
    ), allowed


def _write_spec(path, *, source, budget=0.25, provider="fake", hook=None):
    import yaml

    value = {
        "schema_version": "hudoc-study/v1",
        "id": "allowed-study",
        "version": "1",
        "source": {"kind": "local", "path": str(source)},
        "unit": "document",
        "stages": [
            {
                "id": "label",
                "kind": "extract",
                "provider": provider,
                "model": "fake-model",
                "prompt": "{{text}}",
            }
        ],
        "budget": {"max_usd": budget},
    }
    if hook:
        value["hook"] = hook
    path.write_text(yaml.safe_dump(value), encoding="utf-8")


def test_job_manager_rejects_specs_outside_input_roots(tmp_path):
    manager, allowed = _manager(tmp_path)
    source = allowed / "source.jsonl"
    source.write_text(json.dumps({"itemid": "x", "text": "text"}) + "\n")
    outside = tmp_path / "outside.yaml"
    _write_spec(outside, source=source)

    with pytest.raises(PermissionError, match="outside permitted"):
        manager.create(outside)


def test_job_manager_rejects_unbounded_cost_and_python_path_hooks(tmp_path):
    manager, allowed = _manager(tmp_path)
    source = allowed / "source.jsonl"
    source.write_text(json.dumps({"itemid": "x", "text": "text"}) + "\n")
    spec = allowed / "study.yaml"
    _write_spec(spec, source=source, budget=0.75)

    with pytest.raises(PermissionError, match="max_usd"):
        manager.create(spec)

    _write_spec(spec, source=source, hook="arbitrary.py:hook")
    with pytest.raises(PermissionError, match="installed study hooks"):
        manager.create(spec)
