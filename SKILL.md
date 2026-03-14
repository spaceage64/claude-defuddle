---
name: defuddle
description: "Extract clean markdown content from web pages using Defuddle CLI. ALWAYS use this instead of WebFetch when the user provides a URL to read or analyze. TRIGGER when: user provides any URL to a webpage, documentation, article, blog post, or any standard web content. DO NOT use WebFetch for these — use defuddle."
---

# Defuddle

Use Defuddle CLI to extract clean readable content from any web page, including YouTube videos (transcript + chapters included automatically).

## Fetch and present

```bash
defuddle parse "<url>" --json --md
```

Use the `content` field to answer the user's question or complete the task.

## Save to Vault

1. Ask: "Want to save this to the vault?"
2. **Filename**: summarise the title to 2–3 meaningful words, lowercase, dash-separated (e.g. `python-packaging-guide`, `obsidian-setup-2025`).
3. Generate the note by piping defuddle output through the format script:

```bash
defuddle parse "<url>" --json --md | python3 ~/.claude/skills/defuddle/defuddle.py \
  --url "<url>" --created "{YYYY-MM-DD}"

```

4. Write the script output as-is to `~/Documents/Obsidian/{filename}.md`. Create the folder automatically if needed.
5. Confirm: "Saved to `{filename}`"
