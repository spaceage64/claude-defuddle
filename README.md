# Defuddle — Claude Code Skill

A [Claude Code](https://claude.ai/code) WebFetch replacement that turns web pages, YouTube videos, Apple podcasts, and academic papers into clean Markdown notes and saves them to your [Obsidian](https://obsidian.md) vault. Token-efficient — Claude only ever sees the finished note.

Just paste any URL into your conversation. That's it.

![Example](img/example.png)

---

## What it does

**Articles & docs** — strips ads and clutter, generates an AI summary, pulls a heading index, and saves a clean note with frontmatter. Falls back to Wayback Machine / archive.is for JS-rendered or paywalled pages.

**YouTube videos** — fetches the full transcript with timestamps, inserts chapter markers, generates a summary and description, and saves a structured note.

**Apple podcasts** — same as YouTube, but for podcast episodes. Reads the TTML transcript cached by the macOS Podcasts app. (No other podcast platforms supported for now.)

**Academic papers** — give it a DOI URL (`https://doi.org/...`) or arXiv abstract URL (`https://arxiv.org/abs/...`). It fetches the PDF, converts it to markdown, and saves a structured note with abstract, keywords as tags, and bibliography.

All content saves to `{vault}/{project}/defuddle/`.

**Images** — when enabled, images in saved notes are downloaded and stored in an `img/` subfolder next to the note, with optional compression via `pngquant` and `jpegoptim`.

---

### Why this is token-efficient

Claude Code's built-in `WebFetch` tool dumps raw page content — ads, navigation, footers and all — straight into Claude's context window. Claude then has to wade through it, burning tokens on noise.

This skill offloads that work to dedicated tools:

- **Defuddle** strips pages down to just the article before Claude sees anything
- **yt-dlp** handles YouTube transcripts entirely in a subprocess — no raw VTT in context
- **Datalab / marker_single** converts PDFs to markdown outside Claude's context
- **The Python script** handles all formatting — frontmatter, tags, timestamps, indexes, image downloading — without Claude reasoning about any of it
- **A separate AI provider** (Gemini, OpenAI, Ollama) handles summarisation as its own API call, so a full article never needs to fit in Claude's context window. Default is Gemini free tier, so no cost. If not configured, it falls back to a Claude Haiku subprocess.

Claude's job is just: trigger the skill, get the finished note, write it to disk.

---

### Example article note

```
---
tags:
  - project/myproject
  - defuddle/doc
  - python
  - packaging
created: "2026-03-14"
title: "How to publish a Python package"
description: "A step-by-step guide to publishing a Python package to PyPI using modern tooling."
url: "https://example.com/python-packaging"
author: "Jane Smith"
published: "2024-11-01"
site: "example.com"
---

---

# How to publish a Python package

## Summary

A concise AI-generated summary of the article's key points...

## Index

- [[#Setting up your project]]
- [[#Writing pyproject.toml]]
- [[#Publishing to PyPI]]

---

[full article content]
```

## Requirements

| Tool | Purpose | Install |
|---|---|---|
| [Claude Code](https://claude.ai/code) | Runs the skill | See Claude docs |
| [defuddle](https://github.com/kepano/defuddle) | Extracts clean content from web pages | `npm install -g defuddle` |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Downloads YouTube transcripts | `brew install yt-dlp` |
| Python 3.9+ | Runs the formatting script | Pre-installed on macOS Ventura+ |
| [Obsidian](https://obsidian.md) | Your note vault (optional) | See Obsidian site |

**Optional — AI enrichment** (summaries, tags, descriptions):

| Tool | Purpose | Install |
|---|---|---|
| Gemini API key | AI enrichment via Google (default) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free tier available |
| [Ollama](https://ollama.com) | Local AI, no key needed | `brew install ollama` |

> **Note:** AI enrichment always runs. If you don't configure a provider, it falls back to a Claude CLI subprocess (Haiku by default). Setting up Gemini free tier is recommended to avoid spending Claude tokens on summaries.

**Optional — PDF papers:**

| Tool | Purpose | Install |
|---|---|---|
| Datalab API key | Cloud PDF→markdown conversion | [datalab.to](https://datalab.to) — free accounts get $10 in credits (roughly 10–15 cents per paper) |
| [marker-pdf](https://github.com/VikParuchuri/marker) | Local PDF→markdown fallback | `pip install marker-pdf` |

**Optional — image compression:**

| Tool | Purpose | Install |
|---|---|---|
| pngquant | Lossy PNG compression (~60–80% smaller) | `brew install pngquant` |
| jpegoptim | JPEG compression (quality ceiling 85) | `brew install jpegoptim` |

---

## Installation

### 1. Copy the skill files

Place this folder at:

```
~/.claude/skills/defuddle/
```

So you end up with:

```
~/.claude/skills/defuddle/
├── SKILL.md
├── defuddle.py
└── README.md
```

### 2. Set your vault path

Open `defuddle.py` and set the `VAULT_PATH` setting near the top of the file:

```python
VAULT_PATH = '/your/path/to/obsidian/vault'
```

This is where all notes get saved. It can be your Obsidian vault, or just any folder you want.

### 3. Tell Claude the project folder when saving

When you want to save a note, just tell Claude which folder to use. Notes land at `{vault}/{project}/defuddle/`. The folder is created automatically if it doesn't exist.

### 4. Allow required tools in Claude Code settings

Add these to your `~/.claude/settings.json` under `permissions.allow`:

```json
"Bash(defuddle **)",
"Bash(yt-dlp *)",
"Bash(python3 ~/.claude/**)"
```

> **Note:** `defuddle **` uses a double wildcard because URLs contain `/` characters which a single `*` won't match.

Your full `settings.json` might look like:

```json
{
  "permissions": {
    "allow": [
      "Bash(defuddle **)",
      "Bash(yt-dlp *)",
      "Bash(python3 ~/.claude/**)",
      "Skill(*)"
    ]
  }
}
```

---

## AI enrichment (optional)

Configure your preferred AI provider in `defuddle.py` under `AI_LLM`.

### Gemini (default)

1. Grab a free API key at [Google AI Studio](https://aistudio.google.com/apikey)
2. Open `~/.claude/CLAUDE.md` (create it if it doesn't exist) and add:

```markdown
## API Keys

- **Gemini**: `YOUR_GEMINI_API_KEY_HERE`
```

Default model is `gemini-3.1-flash-lite-preview`, which is on the free tier. You can swap it for any model you like via `AI_MODEL`. See the [API pricing overview](https://ai.google.dev/gemini-api/docs/pricing) for free tier eligibility.

> **Note:** To go back to the free tier later, just remove your billing info — it downgrades automatically.

### OpenAI or compatible APIs

Set `AI_LLM = 'openai'` in `defuddle.py` and add your key to `~/.claude/CLAUDE.md`:

```markdown
- **OpenAI**: `YOUR_API_KEY_HERE`
```

Set `AI_BASE_URL` to use any OpenAI-compatible endpoint:

```python
AI_BASE_URL = 'https://api.groq.com/openai/v1'   # Groq
AI_BASE_URL = 'https://zen.opencode.ai/v1'        # OpenCode Zen (free)
AI_BASE_URL = 'https://api.together.xyz/v1'       # Together AI
```

### Ollama (local, no key needed)

Set `AI_LLM = 'ollama'` and `AI_MODEL` to any model you have pulled:

```python
AI_LLM   = 'ollama'
AI_MODEL = 'llama3.2'
```

### Claude CLI fallback

If no API key is found or the provider fails, the skill falls back to the Claude CLI — running as a separate process so it doesn't eat your current session's tokens. You can configure the fallback model with `AI_FALLBACK_MODEL` in `defuddle.py` (defaults to Haiku).

---

## Academic papers

Given a DOI URL (e.g. `https://doi.org/10.48550/arXiv.1706.03762`), the skill:

1. Tries to fetch the arXiv LaTeX source directly (best quality)
2. Falls back to the Datalab API for cloud PDF→markdown
3. Falls back to local `marker_single` if Datalab isn't available
4. Optionally tries a shadow library for restricted papers

To enable Datalab, add your key to `~/.claude/CLAUDE.md`:

```markdown
- **Datalab**: `YOUR_DATALAB_API_KEY_HERE`
```

To configure a shadow library, set `SHADOW_BASE_URL` in `defuddle.py`:

```python
SHADOW_BASE_URL = 'https://your-shadow-library.example'
```

Set it to `''` to disable. The shadow library needs to support this URL format: `https://example.domain/10.1103/PhysRevB.57.6107`

---

## Usage

Just share a URL in your Claude Code conversation:

> "Can you read this article for me? https://example.com/some-article"

> "https://www.youtube.com/watch?v=dsA2sQ-rThU"

> "Summarise this page: https://github.com/spaceage64/claude-defuddle"

> "https://doi.org/10.1145/3706598.3713709"

> "Check out this podcast: https://podcasts.apple.com/us/podcast/restitutio/id1053137114?i=1000741998143"

Claude will automatically use the defuddle skill. After fetching, it'll ask if you want to save the note to your vault.

---

## How the skill is triggered

The skill's `description` field in `SKILL.md` tells Claude when to use it:

```
TRIGGER when: user provides any URL to a webpage, documentation, article, blog post,
or any standard web content.
DO NOT use WebFetch for these — use defuddle.
```

You can reinforce this in your own `~/.claude/CLAUDE.md`:

```markdown
## Tools

- **Never use WebFetch for standard web pages** — always use the `defuddle` skill instead.
```

---

## Notes

- **Platform** — designed and tested on macOS. Linux should work for articles, YouTube, and papers — all dependencies are available, but it's untested. Windows may work with Claude Code for Windows and the relevant tools installed, also untested. Apple Podcasts is macOS-only regardless (TTML transcripts are stored by the macOS Podcasts app).
- **Transcripts** — the skill tries manual captions first (better quality), then falls back to auto-generated. Rolling-window caption artefacts (duplicated lines) are detected and removed automatically.
- **YouTube chapters** — if the video has chapter markers, they're used directly. If not, the AI generates logical chapter breaks.
- **Tags** — hashtags in YouTube descriptions and keywords in paper abstracts are extracted and added to frontmatter. The AI also adds content-based tags.
- **Images** — when `ENABLE_IMAGES = True`, images are downloaded into an `img/` subfolder next to the note. Invalid responses (auth errors, HTML pages served as images) are detected and the original URL is kept instead.
- **Obsidian compatibility** — formatting is tuned for Obsidian. May look slightly different in other Markdown readers.
