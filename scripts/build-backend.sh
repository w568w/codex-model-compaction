#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work_dir=${1:?usage: build-backend.sh WORK_DIR}
work_dir=$(realpath "$work_dir")
source_dir="$work_dir/upstream"
source_tag=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["upstream_tag"])' "$work_dir/manifest.json")
source_commit=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["upstream_commit"])' "$work_dir/manifest.json")

if [[ ! -d "$source_dir/.git" ]]; then
    git clone --depth 1 --branch "$source_tag" https://github.com/openai/codex.git "$source_dir"
fi
[[ $(git -C "$source_dir" rev-parse HEAD) == "$source_commit" ]]
[[ -z $(git -C "$source_dir" status --porcelain) ]]
for patch_file in "$project_root"/patches/*.patch; do
    git -C "$source_dir" apply --check "$patch_file"
    git -C "$source_dir" apply "$patch_file"
done
git -C "$source_dir" diff --check

v8_dir="$work_dir/v8"
mkdir -p "$v8_dir"
v8_version=$(python3 "$source_dir/.github/scripts/rusty_v8_bazel.py" resolved-v8-crate-version)
v8_profile=ptrcomp_sandbox_release_x86_64-unknown-linux-gnu
checksums="rusty_v8_${v8_profile}.sha256"
v8_base="https://github.com/openai/codex/releases/download/rusty-v8-v${v8_version}"
for filename in "$checksums" "librusty_v8_${v8_profile}.a.gz" "src_binding_${v8_profile}.rs"; do
    curl --fail --location --retry 3 "$v8_base/$filename" --output "$v8_dir/$filename"
done
expected=$(awk -v filename="$checksums" '$2 == filename { print $1 }' \
    "$source_dir/third_party/v8/rusty_v8_${v8_version//./_}_release_manifests.sha256")
[[ -n "$expected" && $(sha256sum "$v8_dir/$checksums" | cut -d ' ' -f 1) == "$expected" ]]
[[ $(wc -l < "$v8_dir/$checksums") == 2 ]]
(cd "$v8_dir" && tr -d '\r' < "$checksums" | sha256sum --check -)

export RUSTY_V8_ARCHIVE="$v8_dir/librusty_v8_${v8_profile}.a.gz"
export RUSTY_V8_SRC_BINDING_PATH="$v8_dir/src_binding_${v8_profile}.rs"
export LIBSQLITE3_SYS_USE_PKG_CONFIG=1
export RUSTONIG_DYNAMIC_LIBONIG=1
export ZSTD_SYS_USE_PKG_CONFIG=1
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-2}
export CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_PROFILE_RELEASE_LTO=off
export CARGO_PROFILE_TEST_DEBUG=0
export STABLE_GIT_COMMIT="$source_commit-compaction"

cd "$source_dir/codex-rs"
rustup show active-toolchain
cargo test --locked -p codex-model-provider compaction_policy --lib
cargo build --locked --release --bin codex --bin codex-code-mode-host --bin codex-responses-api-proxy
mkdir -p "$work_dir/backend/bin"
for binary in codex codex-code-mode-host codex-responses-api-proxy; do
    install -m755 "target/release/$binary" "$work_dir/backend/bin/$binary"
    strip "$work_dir/backend/bin/$binary"
done
tar --zstd -cf "$work_dir/backend.tar.zst" -C "$work_dir/backend" bin
