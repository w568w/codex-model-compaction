#!/usr/bin/env python3
"""Sign verified releases and prepare an atomic Pages repository deployment."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from desktop import PUBLIC_SITE, ROOT, sha256


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def capture(*args):
    return run(*args, capture_output=True, text=True).stdout


def verify_release(directory, env):
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for path in (manifest_path, directory / manifest["package_filename"]):
        if path.parent != directory:
            raise ValueError("invalid release filename")
        run("gpg", "--batch", "--verify", str(path) + ".sig", str(path), env=env)
    if sha256(directory / manifest["package_filename"]) != manifest["package_sha256"]:
        raise ValueError("released package checksum mismatch")
    return manifest


def sign(path, fingerprint, passphrase, env):
    run("gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase-fd", "0",
        "--local-user", fingerprint, "--output", str(path) + ".sig", "--detach-sign", str(path),
        input=passphrase.encode(), env=env)


def release_exists(repo, tag):
    result = subprocess.run(["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
                            capture_output=True, text=True)
    if result.returncode == 0:
        return True
    if "release not found" in result.stderr.lower() or "not found" in result.stderr.lower():
        return False
    raise RuntimeError(result.stderr)


def download_release(repo, tag, destination):
    destination.mkdir(parents=True, exist_ok=True)
    run("gh", "release", "download", tag, "--repo", repo, "--dir", str(destination),
        "--pattern", "*.pkg.tar.zst*", "--pattern", "manifest.json*")


def prepare_repository(releases, site, fingerprint, passphrase, env):
    package_dir = site / "repo/x86_64"
    package_dir.mkdir(parents=True, exist_ok=True)
    packages = []
    for directory in releases:
        manifest = verify_release(directory, env)
        filename = manifest["package_filename"]
        for suffix in ("", ".sig"):
            shutil.copy2(directory / (filename + suffix), package_dir / (filename + suffix))
        packages.append(package_dir / filename)
    database = package_dir / "codex-model-compaction.db.tar.zst"
    run("repo-add", str(database), *(str(path) for path in packages), env=env)
    for kind in ("db", "files"):
        archive = package_dir / f"codex-model-compaction.{kind}.tar.zst"
        alias = package_dir / f"codex-model-compaction.{kind}"
        sign(archive, fingerprint, passphrase, env)
        alias.unlink()
        shutil.copy2(archive, alias)
        shutil.copy2(Path(str(archive) + ".sig"), Path(str(alias) + ".sig"))
    for name in ("manifest.json", "manifest.json.sig"):
        shutil.copy2(releases[0] / name, site / name)
    shutil.copy2(ROOT / "keys/repository.asc", site / "repository.asc")
    shutil.copy2(ROOT / "scripts/install.sh", site / "install.sh")
    (site / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Codex Model Compaction</title>'
        '<h1>Codex Model Compaction</h1><p>Signed Arch repository for the Desktop-matched backend.</p>'
        '<p><a href="https://github.com/w568w/codex-model-compaction#installation">Installation</a> · '
        '<a href="manifest.json">Current build</a> · <a href="repository.asc">Signing key</a></p>'
        f'<pre>{fingerprint}</pre>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--repo", default="w568w/codex-model-compaction")
    args = parser.parse_args()
    fingerprint = (ROOT / "keys/repository.fingerprint").read_text().strip()
    private_key = os.environ.pop("ARCH_SIGNING_KEY")
    passphrase = os.environ.pop("ARCH_SIGNING_PASSPHRASE")
    with tempfile.TemporaryDirectory(prefix="codex-sign-") as temporary:
        private_home = Path(temporary) / "gnupg"
        private_home.mkdir(mode=0o700)
        env = {**os.environ, "GNUPGHOME": str(private_home)}
        run("gpg", "--batch", "--import", input=private_key.encode(), env=env)
        keys = run("gpg", "--batch", "--with-colons", "--list-secret-keys", env=env,
                   capture_output=True, text=True).stdout
        if fingerprint not in [line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:")]:
            raise ValueError("signing key does not match the pinned public fingerprint")
        current = args.release_dir.resolve()
        manifest = json.loads((current / "manifest.json").read_text())
        tag = manifest["release_tag"]
        if release_exists(args.repo, tag):
            # A successful Release followed by a failed Pages deployment is safe to retry.
            existing = Path(temporary) / "existing"
            download_release(args.repo, tag, existing)
            published = verify_release(existing, env)
            for key in ("desktop_version", "pipeline_sha256", "upstream_commit"):
                if published[key] != manifest[key]:
                    raise ValueError(f"existing immutable release disagrees on {key}")
            current = existing
        else:
            for filename in (manifest["package_filename"], "manifest.json", "backend.tar.zst"):
                sign(current / filename, fingerprint, passphrase, env)
            shutil.copy2(ROOT / "keys/repository.asc", current / "repository.asc")
            notes = Path(temporary) / "release-notes.md"
            notes.write_text(
                f"Desktop `{manifest['desktop_version']}` · Codex `{manifest['codex_version']}`\n\n"
                f"Upstream commit: `{manifest['upstream_commit']}`\n\n"
                "GPT models retain remote compaction. Other models use ordinary summarization.\n\n"
                f"Install through the [signed Arch repository]({PUBLIC_SITE}).\n")
            run("gh", "release", "create", tag, "--repo", args.repo, "--target", os.environ["GITHUB_SHA"],
                "--title", f"Desktop {manifest['pkgver']} · patch {manifest['pkgrel']}",
                "--notes-file", str(notes), *(str(path) for path in sorted(current.iterdir()) if path.is_file()))
        releases = [current]
        release_list = json.loads(capture("gh", "release", "list", "--repo", args.repo, "--limit", "100",
                                          "--json", "tagName,isDraft"))
        for release in release_list:
            previous_tag = release["tagName"]
            if previous_tag == tag or not previous_tag.startswith("desktop-") or release["isDraft"]:
                continue
            destination = Path(temporary) / previous_tag
            download_release(args.repo, previous_tag, destination)
            releases.append(destination)
            if len(releases) == 3:
                break
        prepare_repository(releases, args.site.resolve(), fingerprint, passphrase, env)


if __name__ == "__main__":
    main()
