# Codex Model Compaction

Use GPT, Kimi, and other models together in ChatGPT/Codex Desktop on Arch Linux,
with context compaction suited to each model.

| Model | Context compaction |
| --- | --- |
| GPT (`gpt-*`) | Remote compaction, when supported by the provider |
| Kimi and other models | Ordinary text summaries |

This applies to both manual and automatic compaction. Each conversation uses its
own model's policy, including when models share the same gateway.

## Installation

Install the official `chatgpt-bin` package first and keep the OpenAI Arch
repository enabled. Then run:

```sh
curl -fL https://www.w568w.eu.org/codex-model-compaction/install.sh -o /tmp/install-codex-model-compaction.sh &&
  bash /tmp/install-codex-model-compaction.sh
```

The installer adds the signed package repository and installs
`codex-model-compaction` through pacman.

## Usage

Fully quit ChatGPT, then open **ChatGPT (Model Compaction)** from the application
menu (**ChatGPT（模型压缩）** in Chinese). Select your models and use ChatGPT as usual.

To switch back, fully quit the app and open the original **ChatGPT** launcher.

## Updates

Update with your regular system upgrade, then restart ChatGPT:

```sh
sudo pacman -Syu
```

New Desktop versions are checked every six hours. The package keeps Desktop and
the patched backend on matching versions. If pacman reports a `chatgpt-bin`
version conflict while an update is being prepared, update the rest of your
system with `sudo pacman -Syu --ignore=chatgpt-bin` and retry the full upgrade later.

## Uninstall

```sh
sudo pacman -R codex-model-compaction
```

Use the original **ChatGPT** launcher afterward.

## Links

- [Releases](https://github.com/w568w/codex-model-compaction/releases)
- [Report an issue](https://github.com/w568w/codex-model-compaction/issues)
- [Upstream Codex](https://github.com/openai/codex)
- [Apache 2.0 license](LICENSE)
