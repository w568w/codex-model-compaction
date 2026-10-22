#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
work_dir=${1:?usage: build-backend.sh WORK_DIR [--prepared]}
work_dir=$(realpath "$work_dir")
if [[ ${2:-} != --prepared ]]; then
    bash "$project_root/scripts/prepare-backend.sh" "$work_dir"
fi
# shellcheck disable=SC1091
source "$work_dir/build.env"
source_dir="$work_dir/upstream"
export LIBSQLITE3_SYS_USE_PKG_CONFIG=1
export RUSTONIG_DYNAMIC_LIBONIG=1
export ZSTD_SYS_USE_PKG_CONFIG=1
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-2}
export CARGO_PROFILE_RELEASE_DEBUG=0
export CARGO_PROFILE_RELEASE_LTO=off
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
export CARGO_PROFILE_TEST_DEBUG=0

cd "$source_dir/codex-rs"
cargo test --locked --release -p codex-model-provider compaction_policy --lib
cargo build --locked --release --bin codex --bin codex-code-mode-host --bin codex-responses-api-proxy
mkdir -p "$work_dir/backend/bin"
for binary in codex codex-code-mode-host codex-responses-api-proxy; do
    install -m755 "target/release/$binary" "$work_dir/backend/bin/$binary"
    strip "$work_dir/backend/bin/$binary"
done
tar --zstd -cf "$work_dir/backend.tar.zst" -C "$work_dir/backend" bin
