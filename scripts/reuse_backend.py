#!/usr/bin/env python3
"""Recover a signed backend even after GitHub's short-lived cache is evicted."""

import json
import os
from pathlib import Path
import re
import shutil
import sys

from desktop import ROOT, download, run, sha256


def reuse(work):
    expected = json.loads((work / "manifest.json").read_text())
    tag = expected.get("reuse_release_tag", "")
    if not tag:
        print("No previous release has the same backend inputs")
        return False
    if not re.fullmatch(r"desktop-[0-9.]+-p[0-9]+", tag):
        raise ValueError("invalid reusable release tag")
    staging = work / "reuse"
    staging.mkdir(parents=True, exist_ok=True)
    base = f"https://github.com/w568w/codex-model-compaction/releases/download/{tag}"
    for name in ("manifest.json", "manifest.json.sig", "backend.tar.zst", "backend.tar.zst.sig"):
        download(f"{base}/{name}", staging / name)
    keyring = staging / "repository.gpg"
    run("gpg", "--batch", "--yes", "--dearmor", "--output", str(keyring), str(ROOT / "keys/repository.asc"))
    for name in ("manifest.json", "backend.tar.zst"):
        run("gpgv", "--keyring", str(keyring.resolve()), str(staging / (name + ".sig")), str(staging / name))
    previous = json.loads((staging / "manifest.json").read_text())
    for key in ("backend_key", "codex_version", "upstream_commit"):
        if previous[key] != expected[key]:
            raise ValueError(f"reusable backend differs on {key}")
    archive = staging / "backend.tar.zst"
    if sha256(archive) != previous["backend_sha256"]:
        raise ValueError("reusable backend checksum mismatch")
    shutil.copy2(archive, work / "backend.tar.zst")
    print(f"Restored signature-verified backend from {tag}")
    return True


if __name__ == "__main__":
    result = reuse(Path(sys.argv[1]).resolve())
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            print(f"reused={str(result).lower()}", file=stream)
