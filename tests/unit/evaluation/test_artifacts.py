import os

import pytest

import evaluation.artifacts as artifacts_module
from evaluation.artifacts import publish_directory


def test_publish_directory_restores_existing_output_when_final_replace_fails(tmp_path, monkeypatch) -> None:
    output = tmp_path / "report"
    output.mkdir()
    marker = output / "marker.txt"
    marker.write_text("existing", encoding="utf-8")
    real_replace = os.replace
    calls = []

    def fail_publish(source, destination):
        calls.append((source, destination))
        if len(calls) == 2:
            raise OSError("synthetic publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(artifacts_module.os, "replace", fail_publish)
    with pytest.raises(OSError, match="synthetic"):
        publish_directory(
            output,
            lambda temporary: (temporary / "new.txt").write_text("new", encoding="utf-8"),
            overwrite=True,
        )

    assert marker.read_text(encoding="utf-8") == "existing"
    assert not (output / "new.txt").exists()
    assert not list(tmp_path.glob(".report.*.tmp"))
    assert not list(tmp_path.glob(".report.*.backup"))


def test_publish_directory_does_not_replace_target_created_during_render(tmp_path) -> None:
    output = tmp_path / "report"

    def concurrent_writer(temporary):
        (temporary / "new.txt").write_text("new", encoding="utf-8")
        output.mkdir()
        (output / "concurrent.txt").write_text("concurrent", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        publish_directory(output, concurrent_writer)
    assert (output / "concurrent.txt").read_text(encoding="utf-8") == "concurrent"
    assert not (output / "new.txt").exists()
    assert not list(tmp_path.glob(".report.*.tmp"))
