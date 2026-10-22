#!/usr/bin/env python3
"""Resolve a signed Desktop package to its exact public Codex source tag."""

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_REPO = "https://persistent.oaistatic.com/codex-app-prod/linux/arch/x86_64"
PACKAGE_NAME = "codex-model-compaction"
PUBLIC_SITE = "https://w568w.github.io/codex-model-compaction"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def download(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    print(f"Downloading {destination.name}", flush=True)
    run("curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3",
        "--connect-timeout", "15", "--max-time", "600", url, "--output", str(temporary))
    temporary.replace(destination)


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def pipeline_digest():
    digest = hashlib.sha256()
    for directory in ("scripts", "arch", "patches", "tests", ".github/workflows"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
                digest.update(path.read_bytes())
    return digest.hexdigest()


def read_description(data):
    blocks = data.decode().strip().split("\n\n")
    return {lines[0].strip("%"): lines[1:] for block in blocks if (lines := block.splitlines())}


def package_entry(database):
    with tarfile.open(fileobj=io.BytesIO(database), mode="r:*") as archive:
        for member in archive:
            if member.name.endswith("/desc"):
                entry = read_description(archive.extractfile(member).read())
                if entry.get("NAME") == ["chatgpt-bin"]:
                    return entry
    raise ValueError("signed repository does not contain chatgpt-bin")


def source_commit(tag):
    result = run("git", "ls-remote", "https://github.com/openai/codex.git",
                 f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}", capture_output=True, text=True)
    refs = dict(line.split()[::-1] for line in result.stdout.splitlines())
    commit = refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}"))
    if not commit or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"no public upstream source tag: {tag}")
    return commit


def previous_manifest(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise


def resolve(work, previous):
    work.mkdir(parents=True, exist_ok=True)
    database = work / "openai-chatgpt.db"
    download(f"{DESKTOP_REPO}/openai-chatgpt.db", database)
    download(f"{DESKTOP_REPO}/openai-chatgpt.db.sig", database.with_suffix(".db.sig"))
    keyring = work / "openai-linux.gpg"
    run("gpg", "--batch", "--yes", "--dearmor", "--output", str(keyring), str(ROOT / "keys/openai-linux.asc"))
    run("gpgv", "--keyring", str(keyring.resolve()), str(database.with_suffix(".db.sig")), str(database))
    entry = package_entry(database.read_bytes())
    desktop_version = entry["VERSION"][0]
    if not re.fullmatch(r"[0-9][0-9.]*-[0-9]+", desktop_version):
        raise ValueError(f"unexpected Desktop package version: {desktop_version}")
    fingerprint = pipeline_digest()
    if (previous.get("desktop_version") == desktop_version
            and previous.get("pipeline_sha256") == fingerprint
            and previous.get("desktop_sha256") == entry["SHA256SUM"][0]):
        return None
    filename = entry["FILENAME"][0]
    if Path(filename).name != filename:
        raise ValueError("invalid filename in Desktop repository")
    archive = work / filename
    if not archive.exists() or sha256(archive) != entry["SHA256SUM"][0]:
        download(f"{DESKTOP_REPO}/{filename}", archive)
    if sha256(archive) != entry["SHA256SUM"][0]:
        raise ValueError("Desktop package checksum mismatch")
    signature = archive.with_suffix(archive.suffix + ".sig")
    signature.write_bytes(base64.b64decode(entry["PGPSIG"][0], validate=True))
    run("gpgv", "--keyring", str(keyring.resolve()), str(signature), str(archive))
    extracted = work / "desktop"
    extracted.mkdir(exist_ok=True)
    binary_path = "usr/lib/chatgpt/resources/codex"
    run("bsdtar", "-xf", str(archive), "-C", str(extracted), binary_path)
    binary = extracted / binary_path
    version_output = run(str(binary.resolve()), "--version", capture_output=True, text=True).stdout.strip()
    match = re.fullmatch(r"codex-cli ([0-9][0-9A-Za-z.+-]*)", version_output)
    if not match:
        raise ValueError(f"unexpected bundled Codex version: {version_output}")
    codex_version = match[1]
    tag = f"rust-v{codex_version}"
    pkgver = desktop_version.rsplit("-", 1)[0]
    pkgrel = int(previous.get("pkgrel", 0)) + 1 if previous.get("pkgver") == pkgver else 1
    build_files = [ROOT / "scripts/build-backend.sh", *sorted((ROOT / "patches").glob("*.patch"))]
    build_digest = hashlib.sha256(b"".join(path.read_bytes() for path in build_files)).hexdigest()
    commit = source_commit(tag)
    return {
        "package": PACKAGE_NAME, "pkgver": pkgver, "pkgrel": pkgrel,
        "desktop_version": desktop_version, "desktop_filename": filename,
        "desktop_sha256": entry["SHA256SUM"][0], "codex_version": codex_version,
        "upstream_tag": tag, "upstream_commit": commit,
        "pipeline_sha256": fingerprint,
        "backend_key": hashlib.sha256(f"{commit}\n{build_digest}".encode()).hexdigest(),
        "patches": {path.name: sha256(path) for path in sorted((ROOT / "patches").glob("*.patch"))},
        "architecture": "x86_64",
        "release_tag": f"desktop-{pkgver}-p{pkgrel}",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=ROOT / ".work")
    parser.add_argument("--previous-url", default=f"{PUBLIC_SITE}/manifest.json")
    args = parser.parse_args()
    manifest = resolve(args.work, previous_manifest(args.previous_url))
    changed = manifest is not None
    if changed:
        (args.work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
    else:
        print("Desktop package and pipeline are unchanged")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as stream:
            print(f"changed={str(changed).lower()}", file=stream)
            if changed:
                for key in ("pkgver", "pkgrel", "backend_key", "release_tag", "upstream_tag", "upstream_commit"):
                    print(f"{key}={manifest[key]}", file=stream)


if __name__ == "__main__":
    main()
