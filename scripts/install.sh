#!/usr/bin/env bash
set -euo pipefail

repo_url=https://www.w568w.eu.org/codex-model-compaction
fingerprint=93ABE6A96FE686C876BCCA16FF628EB8B9BA6C2C
temporary=$(mktemp -d)
trap 'rm -rf -- "$temporary"' EXIT
curl --proto '=https' --proto-redir '=https' --fail --location --retry 3 --retry-all-errors "$repo_url/repository.asc" --output "$temporary/repository.asc"
actual=$(gpg --batch --show-keys --with-colons "$temporary/repository.asc" | awk -F: '$1 == "fpr" { print $10; exit }')
[[ "$actual" == "$fingerprint" ]]
sudo pacman-key --add "$temporary/repository.asc"
sudo pacman-key --lsign-key "$fingerprint"
printf '[codex-model-compaction]\nSigLevel = Required\nServer = %s/repo/%s\n' "$repo_url" "\$arch" \
    | sudo tee /etc/pacman.d/codex-model-compaction.conf >/dev/null
if ! rg --fixed-strings --line-regexp --quiet 'Include = /etc/pacman.d/codex-model-compaction.conf' /etc/pacman.conf; then
    printf '\nInclude = /etc/pacman.d/codex-model-compaction.conf\n' | sudo tee -a /etc/pacman.conf >/dev/null
fi
sudo pacman -Syu --needed codex-model-compaction
