# kairo

English | [简体中文](README.zh-CN.md)

> A step-driven incremental knowledge-construction engine — drop in a reference, run a `step`, and knowledge grows one notch.

Turns the manual chain of "recording → transcription → minutes → understanding/judgment" into a `step`-driven incremental knowledge-construction engine. It upholds engineering discipline (traceable; derivatives are regenerable) and is an incremental construction system that **orchestrates LLMs**.

## Core mental model

One `kairo step` topples the dominoes all the way down: `add` a reference → ASR/doc2text → Digest (one model call emits both the full digest and a ≤2,000-character Evidence Card) → Compose (rebuild bounded `understanding.md` from all cards, then derive `assessment.md`; each target ≤20,000 characters). Like `make`, it **reconciles** toward the declared state until convergence. Full detail stays in digests; target documents are bounded current syntheses rather than ever-growing archives.

> **Readable full-text prose (optional, [#33](https://github.com/xforce-io/kairo/issues/33) / [#60](https://github.com/xforce-io/kairo/issues/60))**: raw ASR is noisy (no punctuation, colloquial, homophone errors) and hard to read through. A normalized readable full text `prose.md` can be produced as a **human-reading archive** — punctuation, paragraphing, fixes, less filler. It is **for human reading only and never enters the digest path** (digest always from raw `transcript`). Off by default; set `pipeline.normalize.enabled: true` for batch generation on `step`, or generate on demand via Web (“Generate readable prose”) / `kairo prose <ref_id>`. Only machine-derived transcriptions (`origin≠added`); human text and corpus are untouched.

## Installation

```bash
# Global CLI (requires uv, Python ≥ 3.11)
uv tool install git+https://github.com/xforce-io/kairo.git
# Web Console extra:
# uv tool install 'git+https://github.com/xforce-io/kairo.git[web]'

kairo doctor     # PATH / provider / ASR / skill
kairo connect    # hang the operator skill onto local Claude / Cursor / Codex / Pi
```

Developers working in a checkout: `uv tool install .` or `uv run kairo --help`.

Audio transcription depends on a local whisper — see "Local ASR configuration" below.

## Quick start

```bash
kairo init "My research topic"   # initialize the current directory as a topic-workspace + default constitution
kairo add recording.m4a          # register a path pointer (stream/observation by default)
kairo add recording.m4a --copy   # copy into workspace first, then register
kairo add ./meeting-folder       # directory → one multi-form reference (audio/docs/images inside)
kairo add report.docx            # binary sources (docx/pptx/xlsx/pdf) auto-convert to source_text
kairo add whitepaper.md --corpus # register as corpus/baseline (authoritative reference material)
kairo step                       # reconcile: ASR/doc2text → Digest(+evidence) → Compose
kairo status                     # see the fold status of each reference / document
```

Produces two layers of documents: `understanding.md` (neutral facts) and `assessment.md` (stance/judgment).

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Initialize the **current directory** as a topic-workspace + default constitution |
| `list` | List workspaces under a serve root (`--json`; root defaults to `KAIRO_SERVE_ROOT` or cwd) [#95](https://github.com/xforce-io/kairo/issues/95) |
| `new` | Create a workspace directory under the serve root and `init` it (Web create parity) |
| `rm-ws` | Delete a workspace under the serve root (`--yes` skips confirm; root glossary kept) |
| `add` | Register a reference (path pointer by default; `--copy` materializes; `--corpus` marks baseline; `--to <id>` attaches to an existing ref) |
| `title` | Rename a reference's display title (id / directory unchanged) |
| `step` | Run the reconciliation loop to convergence (configured endpoint → Claude CLI → stub; `KAIRO_STUB` forces stub) |
| `run` | Clear terminal blocked then step (same as Web primary button) |
| `re-step` | Force recompute (document-level = full re-synthesis, dropping manual edits) |
| `retry-ref` | Clear derived products for one reference and re-run |
| `rm-ref` | Permanently delete a reference |
| `prose` | Generate readable archive `prose.md` for one reference |
| `accept` | Accept manual edits, pin as the new baseline, clear `blocked: manual-edit` |
| `status` | List references / fold status of each document |
| `glossary` | Glossary `list` / `add` / `rm` (`--scope workspace\|shared`) |
| `index` | Regenerate the `references/MEETINGS.md` navigation index |
| `history` | List version snapshots |
| `rollback` | Roll a document back to a version |
| `diff` | Working-state vs versioned-document diff (built in, no git needed) |
| `serve` | Start the local Web Console |
| `doctor` | Machine check: provider / ASR / skill mount |
| `connect` | Install the operator skill into local coding agents |

## Core concepts

- **constitution.yaml**: this workspace's constitution — the mental model and protocol (two output layers, stream/corpus, fold, extension→role, conversion declarations) are all declared here; the engine hardcodes none of it.
- **stream (observation) / corpus (baseline)**: a stream first derives a bounded evidence card, then enters bounded synthesis; a corpus is a read-only reference layer for the fact agent — not digested, not carded, and not in the fold loop.
- **Two output layers**: `understanding.md` is rebuilt from all evidence cards; `assessment.md` depends only on that bounded fact layer. Neutral facts and stance judgments are not mixed.
- **Convergence**: `step` is like `make` — it reconciles toward the state declared in the constitution, judging staleness by content hash, running until no further progress is made.
- **Binary ingestion** ([#15](https://github.com/xforce-io/kairo/issues/15)): `add file.docx` (docx/pptx/xlsx/pdf) goes through `doc2text` (in-process conversion via [markitdown](https://github.com/microsoft/markitdown)) to produce `source_text`, isomorphic to ASR (`audio→transcript` ↔ `binary→source_text`), with zero downstream changes; xlsx converts to GFM tables, preserving header semantics. No machine configuration needed (markitdown is a project dependency). Stream-type processing only; corpus binaries are not converted (the baseline is read directly, read-only, not derived).
- **blocked states** include source/transform failures, `provider-failed`, invalid or over-budget evidence cards, compose process preambles, invalid provenance, over-budget compose output, `manual-edit`, and legacy `compose-degraded`. Invalid/over-budget outputs never overwrite the last successful artifact; terminal states require an explicit retry or `re-step`.

## Domain glossary

`constitution.yaml` can declare a `glossary` that pins down this domain's canonical proper nouns. It is injected into the agent prompt at every Digest / Evidence Card / Compose (and the optional Normalize) (Issue [#20](https://github.com/xforce-io/kairo/issues/20)), to correct homophone variants and aliases produced by speech/transcription — output always uses the canonical name, and ambiguous mentions are anchored accordingly. Each entry has three keys: `name` (canonical name, the anchor), `note` (grounding for the model, optional), `aka` (known variants/aliases, reference only, optional).

```yaml
glossary:
- name: 灵犀系统            # canonical name (example), used consistently everywhere
  note: 本项目所研究的系统    # grounding, optional
  aka: [灵西, 凌犀, 灵息]    # known mis-recognitions/homophone variants, optional
- name: 星图平台
  note: 平台名（与 corpus 基线一致）
```

Note: correction happens in the **normalize / digest / compose stages**; ASR transcription itself is unaffected (whisper still outputs by sound). An empty table (`glossary: []`, the default) means zero behavior change; after editing the glossary for an already-generated reference, run `kairo re-step <id>` to regenerate the digest before it is re-corrected.

## Local ASR configuration

The audio-transcription command is **machine-specific** and is not written into the shared `constitution.yaml` (which only declares `backend: whisper`). Configure it once on the local machine, after which any workspace's `kairo add audio && kairo step` transcribes automatically (Issue [#26](https://github.com/xforce-io/kairo/issues/26)).

`~/.config/kairo/config.toml`, sectioned by the transform's `backend` name (`[asr.<backend>]`):

```toml
[asr.whisper]
cmd = "mlx_whisper {input} --model mlx-community/whisper-large-v3-turbo --language zh -f txt -o {outdir} --output-name {stem}"
origin = "whisper:large-v3-turbo"
```

`kairo step` looks up the matching section by the transform's `backend` in `constitution.yaml` (default `whisper`) — so one machine can host multiple backends (`[asr.whisper]`, `[asr.xxx]`), routed by the workspace's declared backend. Placeholders: `{input}` audio path, `{outdir}` temp output dir, `{stem}` output name, `{output}`=`{outdir}/{stem}.txt`. If the template contains any output placeholder → kairo reads the transcription from the output file; otherwise it captures stdout. Environment variables `KAIRO_ASR_CMD` (and `KAIRO_ASR_ORIGIN`) override globally. Command failure → `blocked: asr-failed` (a fake transcription is never written); no matching config → `blocked: no-asr`.

## Local LLM endpoint configuration

Kairo can use a machine-local OpenAI-compatible Chat Completions endpoint as the default real provider. This stays outside `constitution.yaml`; credentials are read from the environment.

`~/.config/kairo/config.toml`:

```toml
[provider.openai]
base_url_env = "OPENAI_API_BASE"
model_env = "OPENAI_MODEL"
api_key_env = "OPENAI_API_KEY"
```

Provider selection order is: `KAIRO_STUB` → explicit `KAIRO_PROVIDER` → available `grok` CLI → configured `[provider.openai]` → available `claude` CLI → stub. With a local Grok login, plain `kairo step` uses `GrokProvider` by default. Set `KAIRO_PROVIDER=openai` / `claude-code` / `grok` to force a backend. Note: Grok has no `--add-dir`; corpus / image `read_dirs` paths still need `claude-code` (see [#61](https://github.com/xforce-io/kairo/issues/61)).

## Tech stack

Python + uv; an `AgentProvider` seam (`run(config)→artifacts`, backends: stub / grok / openai-compatible / claude-code / codex), no audit. See Issue [#4](https://github.com/xforce-io/kairo/issues/4), [#54](https://github.com/xforce-io/kairo/issues/54), and [#61](https://github.com/xforce-io/kairo/issues/61) for details.

## Web Console (optional)

    uv tool install 'git+https://github.com/xforce-io/kairo.git[web]'
    kairo serve <root directory containing multiple workspaces> [--port 8787]

In the browser (default `http://127.0.0.1:8787`, local only), manage the multiple workspaces under `root`. The UI is bilingual (English by default; switch to Chinese with the `EN | 中` toggle in the top bar, or via your browser's `Accept-Language`):

- **Dashboard**: lists each workspace (observation/baseline counts, to-step / blocked status); supports **single-field workspace creation** — type a topic to create a directory under `root` and `init` it.
- **Detail page**: the left column splits into `Targets / References (observations) / Corpus`; selecting an item → a persistent metadata column on the right (per-form optional preview, one-click path copy), with a preview canvas in the middle. Forms like transcript / digest preview on click (including `.txt` transcriptions outside the workspace — `.md` is rendered, plain text keeps line breaks); the top bar returns to the dashboard.
- **Run**: trigger `step` from the UI and watch the progress log live.

## Agent skill (optional)

The operator skill lives at [`skills/kairo/SKILL.md`](skills/kairo/SKILL.md) (same file the wheel ships). After installing the CLI:

```bash
kairo connect
# or, if you already use skills.sh:
npx skills add xforce-io/kairo --skill kairo -g
```

`kairo connect` writes `~/.agents/skills/kairo/` and links it into detected Claude / Cursor / Codex / Pi skill dirs. The skill is **read-first** — pure "what's the status / summarize conclusions" intents must not trigger `step` / `re-step` / `accept` / etc.; raw transcripts are never treated as final conclusions.

## Design & decision trail

The CLI tools are usable (`init`/`add`/`step`/… all ready, 105+ tests). Each feature's design doc is stored by issue number under [`docs/design/`](docs/design) and is the single source of truth for that decision: MVP [#1](https://github.com/xforce-io/kairo/issues/1), AgentProvider [#4](https://github.com/xforce-io/kairo/issues/4), source layering [#13](https://github.com/xforce-io/kairo/issues/13), Web Console i18n [#41](https://github.com/xforce-io/kairo/issues/41), Grok provider [#61](https://github.com/xforce-io/kairo/issues/61), etc.
