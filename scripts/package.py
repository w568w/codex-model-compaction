#!/usr/bin/env python3
"""Stage the Arch package from a verified backend build."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from desktop import ROOT, sha256


def stage(work):
    manifest = json.loads((work / "manifest.json").read_text())
    destination = work / "package"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("PKGBUILD", "chatgpt-compaction.desktop"):
        shutil.copy2(ROOT / "arch" / name, destination / name)
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(work / "backend.tar.zst", destination / "backend.tar.zst")
    shutil.copy2(work / "manifest.json", destination / "manifest.json")
    values = {"pkgver": manifest["pkgver"], "pkgrel": str(manifest["pkgrel"]),
              "desktop_pkgver": manifest["desktop_version"], "codex_version": manifest["codex_version"]}
    for key, filename in (("backend", "backend.tar.zst"), ("manifest", "manifest.json"),
                          ("desktop", "chatgpt-compaction.desktop"), ("license", "LICENSE"), ("notice", "NOTICE")):
        values[f"{key}_sha256"] = sha256(destination / filename)
    (destination / "release.env").write_text("".join(f"{key}={value!r}\n" for key, value in values.items()))
    return destination


def collect(work):
    manifest = json.loads((work / "manifest.json").read_text())
    packages = list((work / "package").glob("*.pkg.tar.zst"))
    if len(packages) != 1:
        raise ValueError(f"expected one package, found {packages}")
    package = packages[0]
    # Inspect the actual package, including the exact Desktop dependency.
    info = subprocess.check_output(["bsdtar", "-xOf", str(package), ".PKGINFO"], text=True)
    assert f"pkgname = {manifest['package']}\n" in info
    assert f"pkgver = {manifest['pkgver']}-{manifest['pkgrel']}\n" in info
    assert f"depend = chatgpt-bin={manifest['desktop_version']}\n" in info
    members = subprocess.check_output(["bsdtar", "-tf", str(package)], text=True).splitlines()
    for path in ("usr/lib/codex-model-compaction/bin/codex", "usr/lib/codex-model-compaction/bin/codex-code-mode-host",
                 "usr/lib/codex-model-compaction/bin/codex-responses-api-proxy", "usr/share/applications/chatgpt-compaction.desktop"):
        assert path in members, path
    assert not any(path.startswith("usr/lib/chatgpt/") or path == "usr/bin/codex" for path in members)
    release = work / "release"
    release.mkdir(exist_ok=True)
    shutil.copy2(package, release / package.name)
    shutil.copy2(work / "backend.tar.zst", release / "backend.tar.zst")
    manifest.update(package_filename=package.name, package_sha256=sha256(package),
                    backend_sha256=sha256(work / "backend.tar.zst"))
    (release / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["stage", "collect"])
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    if args.operation == "stage":
        print(stage(args.work))
    else:
        collect(args.work)
