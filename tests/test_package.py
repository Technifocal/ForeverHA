"""Ensure distributable archives cannot include local Git or credential state."""
from pathlib import Path
import runpy
import zipfile


def test_source_archive_excludes_private_and_generated_state(tmp_path, monkeypatch):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/package.py"))
    zip_tree = module["zip_tree"]
    root = tmp_path / "source"
    monkeypatch.setitem(zip_tree.__globals__, "ROOT", root)
    public = ["README.md", "examples/config.json", "custom_components/forever_ha/manifest.json"]
    excluded = [".git/config", ".git/objects/object", ".DS_Store", "config.json",
                ".venv/token", "__pycache__/cache.pyc", "outbox.sqlite3-wal",
                "docs/api-audit.json", "docs/test-results.txt"]
    for name in public + excluded:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    target = tmp_path / "source.zip"
    zip_tree(target, root, "ForeverHA/")
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {"ForeverHA/" + name for name in public}
