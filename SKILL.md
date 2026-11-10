---
name: defuddle
description: "Extract clean markdown content from web pages using Defuddle CLI. ALWAYS use this instead of WebFetch when the user provides a URL to read or analyze. TRIGGER when: user provides any URL to a webpage, documentation, article, blog post, PDF link, DOI, arXiv, SSRN, Academia.edu page, publisher article page (ScienceDirect, Wiley, Springer, etc.), PubMed/NBER/bioRxiv page, university repository page, or local PDF file path. DO NOT use WebFetch for these — use defuddle."
---

# Defuddle

Use Defuddle CLI to extract clean readable content from any web page — including YouTube videos (transcript + chapters), Apple Podcasts episodes (transcript + chapters), academic papers (PDF → markdown via DOI, arXiv, SSRN, Academia.edu, publisher article links like ScienceDirect, Wiley and Springer, paper indexes like PubMed and NBER, or university repository pages), plain PDF URLs, and local PDF file paths.

## Fetch and present

```bash
python3 ~/.claude/skills/defuddle/defuddle.py --url "<url>"
```

Use the output to answer the user's question or complete the task. The script automatically detects the source type (article, YouTube, podcast, paper) and handles fetching accordingly. For articles, it falls back to archive.org / archive.is if the page is JS-rendered or paywalled.

> [!warning] User Action Required — STOP
> If stderr contains "captcha" or "archive it" (from `--method archive-is` or `--method wayback`), **STOP immediately. Do not try other URLs or workarounds.** Show the user the URL printed in the message and ask them to visit it and let you know when done — then retry with the same command. Alternatively, ask the user if WebFetch should be used instead.

> [!warning] Apple Podcasts — Transcript Not Cached
> If stderr contains "Podcast transcript not found locally", **STOP immediately.** Show the user the `podcasts://` deep link printed in the message and ask them to open it, view the transcript in the Podcasts app, then let you know when done. Once confirmed, retry with the exact same command — the transcript will now be cached locally.

## Save to Vault

1. Ask: "Want to save this to the vault?"
2. If yes: determine a folder name from context (e.g. a project or topic name), or ask if unclear.
3. Run the script — it writes the file and prints the saved path:

```bash
python3 ~/.claude/skills/defuddle/defuddle.py \
  --url "<url>" --project "{folder}" --created "{YYYY-MM-DD}" \
  [--filename "kebab-case-name"] [--method native|native-md|native-page|googlebot|wayback|archive-is] [--keep-pdf | --no-keep-pdf]
```

- `--project`: subfolder name inside your vault (e.g. `my-project`, `research`)
- `--filename`: optional override; script auto-generates from the title via AI if omitted
- `--method`: force a specific fetch method instead of the auto-cascade (articles only)
- `--keep-pdf` / `--no-keep-pdf` (papers and PDFs): save the original PDF next to the note, with the same name (`{filename}.pdf`), compressed when `ENABLE_PDF_COMPRESSION` is on. The default comes from `KEEP_PDF` in `defuddle.py` (off unless changed); pass `--keep-pdf` when the user asks to keep the PDF.
- Several links: pass them all in one command (`defuddle.py <url1> <url2> ...` with the same options; not with `--filename`). They run `PARALLEL_JOBS` at a time (default 4), each link's progress lines are prefixed with its number, and each link's output follows under `=== [n] url ===`. Prefer this over separate commands; run it in the background when papers are involved.
- PDFs without a printed DOI are looked up in Crossref (file name IDs, Elsevier PIIs, then title) and saved as papers when the found title appears on the PDF's first pages; a local PDF is reused, not downloaded again. A `WARNING: conversion looks incomplete` line means the note has far fewer words than the PDF's text layer — tell the user.
- Papers behind a shadow library: the script opens the shadow library page in the user's browser and waits (up to `SHADOW_WAIT`, default 10 min) for a new PDF in their downloads folder, then continues. Run paper commands in the background and check their output after about 30 seconds; when it shows `Opening shadow library in your browser`, tell the user to save the PDF from that page (give the URL so they can click it if no browser opened). The PDF is left in their downloads folder.

Notes are saved to `{vault}/{folder}/defuddle/` regardless of source type.

The vault path is set via `VAULT_PATH` in `defuddle.py`. See README for setup.

4. Confirm the path printed by the script: "Saved to `{path}`"
