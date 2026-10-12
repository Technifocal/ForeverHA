"""Build install ZIPs from this source tree, without copying caches or test state."""
from pathlib import Path
import zipfile
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent


def zip_tree(target, tree, prefix=""):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(tree.rglob("*")):
            if not file.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", ".venv", "build", "dist"} or part.endswith(".egg-info") for part in file.parts):
                continue
            if file.name == ".DS_Store" or (file.name == "config.json" and file.parent != ROOT / "examples") or file.name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
                continue
            if file.relative_to(tree).as_posix() in {"docs/api-audit.json", "docs/test-results.txt"}:
                continue
            archive.write(file, prefix + file.relative_to(tree).as_posix())


def main():
    version = re.search(r"## Version:\s*(\S+)", (ROOT / "addon/ForeverHA/ForeverHA.toc").read_text())[1]
    ha_version = json.loads((ROOT / "custom_components/forever_ha/manifest.json").read_text())["version"]
    addon = OUT / f"ForeverHA-addon-{version}.zip"
    integration = OUT / f"ForeverHA-home-assistant-{ha_version}.zip"
    source = OUT / f"ForeverHA-source-{version}.zip"
    zip_tree(addon, ROOT / "addon", "")
    zip_tree(integration, ROOT / "custom_components", "custom_components/")
    zip_tree(source, ROOT, "ForeverHA/")
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (addon, integration, source)}
    for p in OUT.glob("forever_ha_bridge-0.1.0-*.whl"):
        checksums[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    (OUT / "SHA256SUMS.txt").write_text("\n".join(f"{digest}  {name}" for name, digest in checksums.items()) + "\n")
    bundle = OUT / f"ForeverHA-{version}.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in checksums:
            archive.write(OUT / filename, filename)
        archive.write(OUT / "SHA256SUMS.txt", "SHA256SUMS.txt")
        archive.writestr("START-HERE.md", f"# ForeverHA {version}\n\nExtract {source.name} and open ForeverHA/README.md for setup.\n\nThe addon ZIP goes into the Forever client's Interface/AddOns directory.\nThe Home Assistant ZIP goes into its configuration directory, followed by a restart and adding the WoW Forever integration.\nInstall the companion wheel with Python 3.11 or newer, or install from the companion source directory.\n\nFor the 0.1.1 crash hotfix, replace only the addon folder; preserve SavedVariables. The companion remains at 0.1.0; the Home Assistant integration is {ha_version}.\n\nThis release passed offline regression tests. Confirmation of the hotfix inside the live Forever client remains pending.\n")
    checksums[bundle.name] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    (OUT / "SHA256SUMS.txt").write_text("\n".join(f"{digest}  {name}" for name, digest in checksums.items()) + "\n")
    print(json.dumps(checksums, indent=2))


if __name__ == "__main__":
    main()
