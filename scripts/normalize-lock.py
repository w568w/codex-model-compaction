#!/usr/bin/env python3
"""Update release-tag workspace versions without resolving third-party dependencies."""

from pathlib import Path
import re
import subprocess
import sys
import tomllib


def normalize(root):
    root = Path(root)
    workspace = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]["package"]["version"]
    manifests = subprocess.check_output(["rg", "--files", "--hidden", "--one-file-system", "--glob", "Cargo.toml", str(root)], text=True)
    versions = {}
    for filename in manifests.splitlines():
        package = tomllib.loads(Path(filename).read_text()).get("package", {})
        if not package:
            continue
        version = package.get("version")
        if isinstance(version, dict) and version.get("workspace"):
            version = workspace
        if isinstance(version, str):
            versions[package["name"]] = version
    path = root / "Cargo.lock"
    original = path.read_text()
    before = tomllib.loads(original)["package"]
    replacements = {}

    def update(match):
        block = match[0]
        package = tomllib.loads(block)["package"][0]
        if "source" in package:
            return block
        name, old = package["name"], package["version"]
        expected = versions[name]
        if expected != old:
            replacements[f'{name} {old}'] = f'{name} {expected}'
            return re.sub(r'^version = "[^"]+"$', f'version = "{expected}"', block, count=1, flags=re.M)
        return block

    updated = re.sub(r'^\[\[package\]\]\n.*?(?=^\[\[package\]\]|\Z)', update, original, flags=re.M | re.S)
    for old, new in replacements.items():
        updated = updated.replace(f'"{old}"', f'"{new}"')
    after = tomllib.loads(updated)["package"]
    if [p for p in before if "source" in p] != [p for p in after if "source" in p]:
        raise ValueError("refusing a change to the third-party dependency lock")
    path.write_text(updated)
    print(f"Normalized {len(replacements)} local package versions; third-party locks unchanged")


if __name__ == "__main__":
    normalize(Path(sys.argv[1]))
