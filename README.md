# kairo

English | [简体中文](README.zh-CN.md)

> A step-driven incremental knowledge-construction engine — drop in a reference, run a `step`, and knowledge grows one notch.

Turns the manual chain of "recording → transcription → minutes → understanding/judgment" into a `step`-driven incremental knowledge-construction engine. It upholds engineering discipline (traceable; derivatives are regenerable) and is an incremental construction system that **orchestrates LLMs**.

## Core mental model

One `kairo step` topples the dominoes all the way down: `add` a reference → ASR/doc2text → Digest (dense memory minutes = this reference's memory) → Compose (incrementally synthesize into the fact layer `understanding.md` / the judgment layer `assessment.md`). Like `make`: it doesn't run commands, it **reconciles** toward the state declared in the constitution, running until convergence.

> **Readable full-text prose (optional, [#33](https://github.com/xforce-io/kairo/issues/33) / [#60](https://github.com/xforce-io/kairo/issues/60))**: raw ASR is noisy (no punctuation, colloquial, homophone errors) and hard to read through. A normalized readable full text `prose.md` can be produced as a **human-reading archive** — punctuation, paragraphing, fixes, less filler. It is **for human reading only and never enters the digest path** (digest always from raw `transcript`). Off by default; set `pipeline.normalize.enabled: true` for batch generation on `step`, or generate on demand via Web (“Generate readable prose”) / `kairo prose <ref_id>`. Only machine-derived transcriptions (`origin≠added`); human text and corpus are untouched.

## Installation

```bash
# Install the kairo console command globally (requires uv)
uv tool install .

# Or run in-repo in development mode
uv run kairo --help
```

Requires Python ≥ 3.11. Audio transcription depends on a local whisper — see "Local ASR configuration" below.

## Quick start

```bash
kairo init "My research topic"   # initialize the current directory as a topic-workspace + default constitution
kairo add recording.m4a          # register a reference (stream/observation by default)
kairo add report.docx            # binary sources (docx/pptx/xlsx/pdf) auto-convert to source_text
kairo add whitepaper.md --corpus # register as corpus/baseline (authoritative reference material)
kairo step                       # reconcile to convergence: ASR/doc2text → Digest → Compose (prose alongside when normalize is on)
kairo status                     # see the fold status of each reference / document
```

Produces two layers of documents: `understanding.md` (neutral facts) and `assessment.md` (stance/judgment).

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Initialize a topic-workspace + default constitution |
| `add` | Register all forms of one reference (`--corpus` marks baseline; stream observation by default) |
| `step` | Run the reconciliation loop to convergence (configured endpoint → Claude CLI → stub; `KAIRO_STUB` forces stub) |
| `re-step` | Force recompute (document-level = full re-synthesis, dropping manual edits) |
| `accept` | Accept manual edits, pin as the new baseline, clear `blocked: manual-edit` |
| `status` | List references / fold status of each document |
| `index` | Regenerate the `references/MEETINGS.md` navigation index |
| `history` | List version snapshots |
| `rollback` | Roll a document back to a version |
| `diff` | Working-state vs versioned-document diff (built in, no git needed) |

## Core concepts

- **constitution.yaml**: this workspace's constitution — the mental model and protocol (two output layers, stream/corpus, fold, extension→role, conversion declarations) are all declared here; the engine hardcodes none of it.
- **stream (observation) / corpus (baseline)**: the epistemic classification of a reference. A stream is folded into documents one by one, judgments evolve with it and can overturn earlier ones; a corpus is a read-only reference layer for the agent — not digested, not in the fold loop — and corrects proper nouns/terminology against the baseline when it conflicts with observations.
- **Two output layers**: `understanding.md` (fact layer) and the `assessment.md` (judgment layer) that depends on it; neutral facts and stance judgments are not mixed.
- **Convergence**: `step` is like `make` — it reconciles toward the state declared in the constitution, judging staleness by content hash, running until no further progress is made.
- **Binary ingestion** ([#15](https://github.com/xforce-io/kairo/issues/15)): `add file.docx` (docx/pptx/xlsx/pdf) goes through `doc2text` (in-process conversion via [markitdown](https://github.com/microsoft/markitdown)) to produce `source_text`, isomorphic to ASR (`audio→transcript` ↔ `binary→source_text`), with zero downstream changes; xlsx converts to GFM tables, preserving header semantics. No machine configuration needed (markitdown is a project dependency). Stream-type processing only; corpus binaries are not converted (the baseline is read directly, read-only, not derived).
- **blocked states**: `no-asr` (no local ASR backend configured) / `asr-failed` (transcription command failed) / `convert-failed` (binary conversion failed/empty output) / `missing-source` (source unreachable) / `manual-edit` (manual edit awaiting `accept`) / `compose-degraded` (synthesis output shrank sharply versus the previous version, suspected degraded output — the overwrite was rejected to protect the old document). After preconditions change, the next `step` retries automatically (e.g. once ASR is configured, old audio is re-transcribed); `asr-failed` / `convert-failed` / `compose-degraded` are treated as terminal and need a manual `re-step` to recompute.

## Domain glossary

`constitution.yaml` can declare a `glossary` that pins down this domain's canonical proper nouns. It is injected into the agent prompt at every Digest / Compose (and the optional Normalize) (Issue [#20](https://github.com/xforce-io/kairo/issues/20)), to correct homophone variants and aliases produced by speech/transcription — output always uses the canonical name, and ambiguous mentions are anchored accordingly. Each entry has three keys: `name` (canonical name, the anchor), `note` (grounding for the model, optional), `aka` (known variants/aliases, reference only, optional).

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

    pip install 'kairo[web]'
    kairo serve <root directory containing multiple workspaces> [--port 8000]

In the browser (default `http://127.0.0.1:8000`, local only), manage the multiple workspaces under `root`. The UI is bilingual (English by default; switch to Chinese with the `EN | 中` toggle in the top bar, or via your browser's `Accept-Language`):

- **Dashboard**: lists each workspace (observation/baseline counts, to-step / blocked status); supports **single-field workspace creation** — type a topic to create a directory under `root` and `init` it.
- **Detail page**: the left column splits into `Targets / References (observations) / Corpus`; selecting an item → a persistent metadata column on the right (per-form optional preview, one-click path copy), with a preview canvas in the middle. Forms like transcript / digest preview on click (including `.txt` transcriptions outside the workspace — `.md` is rendered, plain text keeps line breaks); the top bar returns to the dashboard.
- **Run**: trigger `step` from the UI and watch the progress log live.

## Design & decision trail

The CLI tools are usable (`init`/`add`/`step`/… all ready, 105+ tests). Each feature's design doc is stored by issue number under [`docs/design/`](docs/design) and is the single source of truth for that decision: MVP [#1](https://github.com/xforce-io/kairo/issues/1), AgentProvider [#4](https://github.com/xforce-io/kairo/issues/4), source layering [#13](https://github.com/xforce-io/kairo/issues/13), Web Console i18n [#41](https://github.com/xforce-io/kairo/issues/41), Grok provider [#61](https://github.com/xforce-io/kairo/issues/61), etc.
