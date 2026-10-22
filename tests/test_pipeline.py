import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop
import publish


def database(version="26.1016.61520-1"):
    data = (f"%NAME%\nchatgpt-bin\n\n%VERSION%\n{version}\n\n"
            "%FILENAME%\nchatgpt-bin.pkg.tar.zst\n\n%SHA256SUM%\n1234\n\n").encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        member = tarfile.TarInfo("chatgpt-bin/desc")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class PipelineTests(unittest.TestCase):
    def test_workspace_lock_normalization_preserves_external_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[workspace.package]\nversion = "1.2.3"\n')
            (root / "local").mkdir()
            (root / "local/Cargo.toml").write_text('[package]\nname = "local"\nversion.workspace = true\n')
            (root / "Cargo.lock").write_text('''version = 4

[[package]]
name = "local"
version = "0.0.0"
dependencies = ["external"]

[[package]]
name = "external"
version = "7.8.9"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "unchanged"
''')
            subprocess.run([sys.executable, str(desktop.ROOT / "scripts/normalize-lock.py"), str(root)], check=True)
            import tomllib
            packages = tomllib.loads((root / "Cargo.lock").read_text())["package"]
            self.assertEqual(packages[0]["version"], "1.2.3")
            self.assertEqual(packages[1], {"name": "external", "version": "7.8.9",
                                          "source": "registry+https://github.com/rust-lang/crates.io-index",
                                          "checksum": "unchanged"})

    def test_repository_description_and_unchanged_fast_path(self):
        self.assertEqual(desktop.package_entry(database())["VERSION"], ["26.1016.61520-1"])
        with tempfile.TemporaryDirectory() as directory:
            def download(url, path):
                Path(path).write_bytes(database() if url.endswith(".db") else b"signature")
            previous = {"desktop_version": "26.1016.61520-1", "desktop_sha256": "1234",
                        "pipeline_sha256": desktop.pipeline_digest()}
            with patch.object(desktop, "download", side_effect=download) as fetch, patch.object(desktop, "run") as run:
                self.assertIsNone(desktop.resolve(Path(directory), previous))
                self.assertEqual(fetch.call_count, 2, "unchanged versions must not download the Desktop archive")
                self.assertIn("gpgv", run.call_args.args, "even the unchanged path verifies the signed database")

    def test_signed_database_failure_leaves_previous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            previous = work / "manifest.json"
            previous.write_text("last-good-release")
            with patch.object(desktop, "download"), patch.object(desktop, "run", side_effect=subprocess.CalledProcessError(1, "gpgv")):
                with self.assertRaises(subprocess.CalledProcessError):
                    desktop.resolve(work, {})
            self.assertEqual(previous.read_text(), "last-good-release")

    def test_missing_upstream_tag_is_an_error(self):
        with patch.object(desktop, "run", return_value=subprocess.CompletedProcess([], 0, stdout="")):
            with self.assertRaisesRegex(ValueError, "no public upstream source tag"):
                desktop.source_commit("rust-v0.0.0-missing")

    def test_annotated_tag_uses_commit_not_tag_object(self):
        tag_object, commit = "a" * 40, "b" * 40
        output = f"{tag_object}\trefs/tags/rust-v1\n{commit}\trefs/tags/rust-v1^{{}}\n"
        with patch.object(desktop, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)):
            self.assertEqual(desktop.source_commit("rust-v1"), commit)

    def test_bad_release_signature_stops_repository_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "release"
            release.mkdir()
            (release / "manifest.json").write_text(json.dumps({"package_filename": "test.pkg.tar.zst"}))
            with patch.object(publish, "run", side_effect=subprocess.CalledProcessError(1, "gpg")):
                with self.assertRaises(subprocess.CalledProcessError):
                    publish.prepare_repository([release], root / "site", "fingerprint", "passphrase", {})
            self.assertFalse((root / "site/repo/x86_64/codex-model-compaction.db").exists())

    def test_conflicting_patch_stops_before_build_or_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "upstream"
            source.mkdir()
            subprocess.run(["git", "init", "--quiet", str(source)], check=True)
            (source / "unrelated").write_text("upstream changed\n")
            subprocess.run(["git", "-C", str(source), "add", "unrelated"], check=True)
            subprocess.run(["git", "-C", str(source), "-c", "commit.gpgsign=false", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                            "commit", "--quiet", "-m", "incompatible upstream"], check=True)
            commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            (work / "manifest.json").write_text(json.dumps({"upstream_tag": "test", "upstream_commit": commit}))
            result = subprocess.run(["bash", str(desktop.ROOT / "scripts/build-backend.sh"), str(work)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No such file", result.stderr)
            self.assertFalse((work / "backend.tar.zst").exists())
            self.assertFalse((work / "release").exists())


if __name__ == "__main__":
    unittest.main()
