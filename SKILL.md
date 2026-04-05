---
name: defuddle
description: "Extract clean markdown content from web pages using Defuddle CLI. ALWAYS use this instead of WebFetch when the user provides a URL to read or analyze. TRIGGER when: user provides any URL to a webpage, documentation, article, blog post, or any standard web content. DO NOT use WebFetch for these — use defuddle."
---

# Defuddle

Use Defuddle CLI to extract clean readable content from any web page — including YouTube videos (transcript + chapters), Apple Podcasts episodes (transcript + chapters), and academic papers (PDF → markdown via DOI or arXiv URL).

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
  [--filename "kebab-case-name"] [--method native|native-md|native-page|googlebot|wayback|archive-is]
```

- `--project`: subfolder name inside your vault (e.g. `my-project`, `research`)
- `--filename`: optional override; script auto-generates from the title via AI if omitted
- `--method`: force a specific fetch method instead of the auto-cascade (articles only)

Notes are saved by source type under `{vault}/{folder}/`:
- Articles → `{vault}/{folder}/docs/`
- YouTube & Apple podcasts → `{vault}/{folder}/transcripts/`
- Papers → `{vault}/{folder}/papers/`

The vault path is set via `VAULT_PATH` in `defuddle.py`. See README for setup.

4. Confirm the path printed by the script: "Saved to `{path}`"
