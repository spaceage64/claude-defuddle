#!/usr/bin/env python3
"""
Formats defuddle JSON output into an Obsidian vault note.
Reads defuddle JSON from stdin, writes formatted markdown to stdout.

Usage:
    defuddle parse "<url>" --json --md | python3 defuddle.py \
        --url "<url>" --project "<project>" --category "<Category>" --created "YYYY-MM-DD"
"""

import sys, json, re, argparse, subprocess, os, html, glob, shutil, tempfile, time
from urllib.parse import urlparse, urlunparse, quote, urljoin
from datetime import datetime, timezone

import io
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')

# Keys are read from ~/.claude/CLAUDE.md — add a line like: **ServiceName**: `your-key`
try:
    _claude_md = open(os.path.expanduser('~/.claude/CLAUDE.md')).read()
    _apikey = lambda s: (m.group(1) if (m := re.search(rf'\*\*{s}\*\*:\s*`([^`]+)`', _claude_md, re.IGNORECASE)) else '')
except Exception:
    _apikey = lambda s: ''

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                            USER SETTINGS                                     ║
# ╠══════════════════════════════════════════════════════════════════════════════╣
# ║  Vault path — where notes are saved. Leave '' to read from projects.yaml.    ║
# ║  Example: '/Users/you/Documents/Obsidian'                                    ║
VAULT_PATH = ''
# ║                                                                              ║
# ║  Source feature flags — set False to treat that type as a generic article    ║
ENABLE_YOUTUBE  = True
ENABLE_PODCAST  = True
ENABLE_PAPER    = True
# ║                                                                              ║
# ║  Shadow library — URL for fetching paywalled papers by DOI.                  ║
# ║  Set to '' to disable. Example: 'https://shadow-library-domain-here.box'     ║
SHADOW_BASE_URL = ''
# ║                                                                              ║
# ║  Images — download and localise images into a /img subfolder.                ║
# ║  If False, images are left as external URLs in the note.                     ║
ENABLE_IMAGES            = True
# ║  Compression — requires: brew install pngquant jpegoptim                     ║
# ║  pngquant: lossy palette reduction (~60–80% smaller, similar to TinyPNG)     ║
# ║  jpegoptim: re-encodes only if quality > 85; strips metadata.                ║
ENABLE_IMAGE_COMPRESSION = True
# ║                                                                              ║
# ║  Datalab — converts paper PDFs to markdown (fallback: local marker_single)   ║
# ║  Get key at https://datalab.to — free accounts get $10 in credits.           ║
# ║  Costs are about 10-15 cents per paper. Local fallback is free               ║
# ║  Add to ~/.claude/CLAUDE.md:  **Datalab**: `your-api-key`                    ║
DATALAB_API_KEY = _apikey('Datalab')
# ║                                                                              ║
# ║  AI enrichment — provider for summaries, tags, chapters, and filenames.      ║
# ║  Always falls back to Claude CLI if the provider fails or has no key.        ║
# ║                                                                              ║
# ║  AI_LLM options:                                                             ║
# ║    'gemini' — Google Gemini API. Add to ~/.claude/CLAUDE.md:                 ║
# ║               **Gemini**: `your-api-key`                                     ║
# ║               Free key: https://aistudio.google.com/apikey                   ║
# ║               Models: gemini-2.0-flash | gemini-2.5-flash | gemini-2.5-pro   ║
# ║    'openai' — Any OpenAI-compatible API. Add to ~/.claude/CLAUDE.md:         ║
# ║               **OpenAI**: `your-api-key`                                     ║
# ║               Set AI_BASE_URL for non-OpenAI endpoints, e.g.:                ║
# ║                 OpenCode Zen (free): https://zen.opencode.ai/v1              ║
# ║                 Groq:               https://api.groq.com/openai/v1           ║
# ║                 Together AI:        https://api.together.xyz/v1              ║
# ║               Leave AI_BASE_URL = '' for standard OpenAI.                    ║
# ║    'ollama' — Local Ollama server (no key needed). https://ollama.com        ║
# ║               Set AI_MODEL to any pulled model, e.g. llama3.2                ║
AI_LLM      = 'gemini'
AI_MODEL    = 'gemini-3.1-flash-lite-preview'
AI_BASE_URL = ''
# ║                                                                              ║
# ║  Claude fallback model (used when AI provider fails or has no key).          ║
# ║  Set to '' to use Claude Code's default model.                               ║
# ║    claude-haiku-4-5  — fast, cheap, good for structured output (default)     ║
# ║    claude-sonnet-4-6 — higher quality, slower and more expensive             ║
AI_FALLBACK_MODEL = 'claude-haiku-4-5'  
# ║                                                                              ║
# ║  Minimum word count to consider a fetch attempt successful. Few words        ║
# ║  indicate and error and may trigger alternative processing methods.          ║
MIN_WORDS = 25
# ╚══════════════════════════════════════════════════════════════════════════════╝

# Derived from AI_LLM — not user-configured.
AI_API_KEY = _apikey({'gemini': 'Gemini', 'openai': 'OpenAI'}.get(AI_LLM, ''))


def yaml_str(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def frontmatter_field(key, value, quote=True):
    if value is None or value == '':
        return None
    val = yaml_str(value) if quote else str(value)
    return f'{key}: {val}'


def build_frontmatter(defuddle_type, project, created, tags, extra_fields):
    """Build a YAML frontmatter block from common fields plus source-specific extras.

    extra_fields is a list of pre-formatted YAML lines (or None to skip).
    """
    fields = ['---', 'tags:', f'  - defuddle/{defuddle_type}']
    if project:
        fields.append(f'  - project/{project}')
    for tag in tags:
        fields.append(f'  - {tag}')
    if created:
        fields.append(f'created: {created}')
    for line in [x for x in extra_fields if x is not None]:
        fields.append(line)
    fields.append('---')
    return '\n'.join(fields)


def clean_url(url):
    """Strip fragment and query string from a URL."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))


def is_youtube(url):
    return bool(re.match(r'^https?://(.*\.)?(youtube\.com|youtu\.be)/', url))


def youtube_id(url):
    m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None


def fmt_tc(secs):
    """Format seconds as M:SS or H:MM:SS."""
    secs = int(secs)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f'{h}:{str(m).zfill(2)}:{str(s).zfill(2)}'
    return f'{m}:{str(s).zfill(2)}'


def timecode_to_seconds(tc):
    parts = [int(p) for p in tc.split(':')]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def ttml_time_to_seconds(s):
    """Parse a TTML time expression to seconds (float).
    Handles plain floats ('51.640') and MM:SS.mmm / H:MM:SS.mmm formats."""
    if not s:
        return 0.0
    if ':' in s:
        parts = s.split(':')
        try:
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def strip_timecode_lines(text):
    """Remove lines that start or end with a timecode (chapter index entries in descriptions).
    Handles both YouTube style (MM:SS Title) and podcast style (Title [MM:SS])."""
    tc = r'\[?\d+:\d{2}(?::\d{2})?\]?'
    lines = [l for l in text.split('\n')
             if not re.match(rf'^\s*{tc}', l) and not re.search(rf'{tc}\s*$', l)]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def linkify_timecodes(text, vid_id):
    def replace(m):
        tc = m.group(1)
        secs = timecode_to_seconds(tc)
        return f'[**{tc}**](https://youtu.be/{vid_id}?t={secs})'
    return re.sub(r'^\*\*(\d+:\d{2}(?::\d{2})?)\*\*', replace, text, flags=re.MULTILINE)


def extract_desc_tags(text):
    """Return list of lowercase hashtags found in text (without # prefix)."""
    return [t.lower().lstrip('#') for t in re.findall(r'#[a-zA-Z][a-zA-Z0-9_-]*', text)]


def remove_desc_tags(text):
    """Remove #hashtag tokens from text and clean up resulting blank lines."""
    text = re.sub(r'#[a-zA-Z][a-zA-Z0-9_-]*', '', text)
    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def insert_chapters(transcript_body, chapters):
    """Insert ### chapter headings into transcript at the correct timecode positions.
    Each chapter dict may include an optional 'img' URL, which is embedded below the heading."""
    if not chapters:
        return transcript_body

    # Parse and sort chapters by seconds; preserve full chapter dict
    chapter_list = sorted(
        [(timecode_to_seconds(ch['time']), ch) for ch in chapters]
    )

    tc_pattern = re.compile(r'^\[?\*\*(\d+:\d{2}(?::\d{2})?)\*\*\]')
    lines = transcript_body.split('\n')
    result = []
    ch_idx = 0

    def append_chapter(ch):
        result.append(f'### {ch["title"]}')
        if ch.get('img'):
            result.append('')
            result.append(f'![]({ch["img"]})')
        result.append('')

    for line in lines:
        m = tc_pattern.match(line)
        if m:
            line_secs = timecode_to_seconds(m.group(1))
            while ch_idx < len(chapter_list) and line_secs >= chapter_list[ch_idx][0]:
                append_chapter(chapter_list[ch_idx][1])
                ch_idx += 1
        result.append(line)

    # Append any remaining chapters (past the last timecode line)
    while ch_idx < len(chapter_list):
        result.append('')
        append_chapter(chapter_list[ch_idx][1])
        ch_idx += 1

    return '\n'.join(result)


def parse_vtt(content, vid_id, interval=30):
    """Parse VTT content into a transcript_body string with paragraph-level timestamps.

    Auto-detects rolling-window captions (take last line per cue) vs standalone
    captions (join all lines per cue). interval groups cues into ~N-second paragraphs.
    """
    raw_blocks = []
    for block in re.split(r'\n\n+', content):
        lines = block.strip().split('\n')
        time_line = None
        text_lines = []
        for line in lines:
            if re.match(r'[\d:.]+\s+-->\s+[\d:.]+', line):
                time_line = line.split('-->')[0].strip()
            elif line and not line.startswith('WEBVTT') and not line.startswith('Kind:') \
                    and not line.startswith('Language:'):
                clean = re.sub(r'<[^>]+>', '', line).strip()
                if clean:
                    text_lines.append(clean)
        if time_line and text_lines:
            parts = time_line.split(':')
            try:
                secs = (float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                        if len(parts) == 3 else float(parts[0])*60 + float(parts[1]))
            except (ValueError, IndexError):
                continue
            raw_blocks.append((secs, text_lines))

    if not raw_blocks:
        return ''

    # Auto-detect rolling window: if lines from one cue appear in the next, it's rolling
    sample = raw_blocks[:min(len(raw_blocks), 10)]
    overlaps = sum(
        1 for i in range(1, len(sample))
        if any(line in sample[i][1] for line in sample[i-1][1])
    )
    rolling = len(sample) >= 3 and overlaps >= 2

    # Convert raw blocks to (secs, text)
    if rolling:
        blocks = [(s, tl[-1]) for s, tl in raw_blocks]   # last line = new content
    else:
        blocks = [(s, ' '.join(tl)) for s, tl in raw_blocks]  # join all lines

    if not blocks:
        return ''

    # Deduplicate (handles auto-caption rolling window repeats; harmless for manual)
    deduped = [blocks[0]]
    for b in blocks[1:]:
        if b[1] != deduped[-1][1]:
            deduped.append(b)

    # Group cues into paragraphs of ~interval seconds
    paragraphs = []
    group_start = None
    group_texts = []
    for secs, text in deduped:
        if group_start is None:
            group_start = secs
            group_texts = [text]
        elif secs - group_start >= interval:
            paragraphs.append((group_start, ' '.join(group_texts)))
            group_start = secs
            group_texts = [text]
        else:
            group_texts.append(text)
    if group_texts:
        paragraphs.append((group_start, ' '.join(group_texts)))

    out = []
    for secs, text in paragraphs:
        text = html.unescape(text)
        text = re.sub(r'\s*>>\s*', '\n', text).strip()
        tc = fmt_tc(int(secs))
        link = f'[**{tc}**](https://youtu.be/{vid_id}?t={int(secs)})'
        out.append(f'{link} {text}')

    return '\n\n'.join(out)


def yt_extra(url):
    """Fetch description, duration, and chapters from yt-dlp. Returns (desc, duration, chapters)."""
    try:
        result = subprocess.run(
            ['yt-dlp', '--skip-download', '--dump-single-json', url],
            capture_output=True, text=True, timeout=30
        )
        data = json.loads(result.stdout)
        return (
            data.get('description', ''),
            data.get('duration_string', ''),
            data.get('chapters') or []
        )
    except Exception:
        return '', '', []


def fetch_vtt(url, vid_id):
    """Fetch VTT transcript via yt-dlp, preferring manual captions over auto-generated.
    Returns parsed transcript_body string or None."""
    vtt_path = f'/tmp/yt_{vid_id}.en.vtt'
    try:
        # Try manual captions first (better quality)
        subprocess.run(
            ['yt-dlp', '--write-sub', '--skip-download', '--sub-format', 'vtt',
             '--sub-langs', 'en', '-o', f'/tmp/yt_{vid_id}', url],
            capture_output=True, text=True, timeout=60
        )
        if os.path.exists(vtt_path):
            content = open(vtt_path).read()
            result = parse_vtt(content, vid_id)
            if result.strip():
                return result

        # Fall back to auto-generated captions
        subprocess.run(
            ['yt-dlp', '--write-auto-sub', '--skip-download', '--sub-format', 'vtt',
             '--sub-langs', 'en', '-o', f'/tmp/yt_{vid_id}', url],
            capture_output=True, text=True, timeout=60
        )
        if os.path.exists(vtt_path):
            content = open(vtt_path).read()
            result = parse_vtt(content, vid_id)
            if result.strip():
                return result

        return None
    except Exception:
        return None
    finally:
        for f in glob.glob(f'/tmp/yt_{vid_id}.*'):
            try:
                os.unlink(f)
            except Exception:
                pass


def ai_enrich(title, author, content, needs_chapters=False):
    """Call an AI model for description, summary, tags, and optionally chapters.

    Uses the provider configured by AI_LLM / AI_MODEL via direct API call.
    Falls back to the Claude CLI (always available in Claude Code) if the call fails.
    """
    import urllib.request as _urlreq

    if not content or len(content.split()) < MIN_WORDS:
        return '', '', [], []

    chapters_field = ''
    chapters_rule  = ''
    if needs_chapters:
        chapters_field = ',\n  "chapters": [{"time": "M:SS or H:MM:SS", "title": "..."}, ...]'
        chapters_rule  = '- chapters: list of logical sections with their start timecode from the transcript (5-10 chapters)\n'

    prompt = f"""Analyze this content and return ONLY a valid JSON object with exactly these fields:
{{
  "description": "a clear, neutral description in ~25 words",
  "summary": "a summary of the key points in ~110 words",
  "tags": ["lowercase-kebab-case-tag", ...]{chapters_field}
}}

Rules:
- tags: 5-10 items, lowercase kebab-case, about the content (e.g. "obsidian", "note-taking")
{chapters_rule}- Return ONLY the JSON object, no other text, no markdown code fences

Title: {title}
Author: {author}

Content:
{content}"""

    def _parse(raw):
        raw = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        return (
            data.get('description', ''),
            data.get('summary', ''),
            data.get('tags', []),
            data.get('chapters', [])
        )

    def _api_call():
        """Direct API call to the configured AI provider. Returns raw response text."""
        body: bytes
        req: _urlreq.Request
        if AI_LLM == 'gemini':
            url  = f'https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent?key={AI_API_KEY}'
            body = json.dumps({'contents': [{'parts': [{'text': prompt}]}]}).encode()
            req  = _urlreq.Request(url, data=body, headers={'Content-Type': 'application/json'})
            with _urlreq.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            return data['candidates'][0]['content']['parts'][0]['text']
        if AI_LLM == 'openai':
            url  = (AI_BASE_URL.rstrip('/') + '/chat/completions') if AI_BASE_URL else 'https://api.openai.com/v1/chat/completions'
            body = json.dumps({'model': AI_MODEL, 'messages': [{'role': 'user', 'content': prompt}]}).encode()
            req  = _urlreq.Request(url, data=body, headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {AI_API_KEY}',
            })
            with _urlreq.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            return data['choices'][0]['message']['content']
        if AI_LLM == 'ollama':
            url  = 'http://localhost:11434/api/generate'
            body = json.dumps({'model': AI_MODEL, 'prompt': prompt, 'stream': False}).encode()
            req  = _urlreq.Request(url, data=body, headers={'Content-Type': 'application/json'})
            with _urlreq.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            return data['response']
        raise ValueError(f'Unsupported AI_LLM: {AI_LLM!r}')

    # Try configured provider via direct API call
    if AI_API_KEY or AI_LLM == 'ollama':
        try:
            return _parse(_api_call())
        except Exception as e:
            print(f'{AI_LLM} enrichment failed ({e}) — falling back to Claude...', file=sys.stderr)

    # Fall back to Claude CLI (always available in Claude Code, no key needed)
    try:
        result = subprocess.run(
            ['claude', '-p', prompt, '--model', AI_FALLBACK_MODEL, '--output-format', 'text'] if AI_FALLBACK_MODEL else ['claude', '-p', prompt, '--output-format', 'text'],
            capture_output=True, text=True, timeout=120
        )
        return _parse(result.stdout)
    except Exception:
        return '', '', [], []


def _heading_display(title):
    """Return a plain-text display string for a heading that may contain LaTeX or markdown.

    Used to produce readable [[#anchor|display]] wikilinks for math headings.
    Strips paired bold/italic markers (**text** and *text*, no spaces inside),
    LaTeX commands, and $...$ math wrappers.
    """
    s = title
    # Strip paired **bold** markers (no space between ** and text)
    s = re.sub(r'\*\*(\S[^*]*\S|\S)\*\*', r'\1', s)
    # Strip paired *italic* markers (no space between * and text)
    s = re.sub(r'\*(\S[^*]*\S|\S)\*', r'\1', s)
    # \operatorname{name} → name (with trailing space to separate from next token)
    s = re.sub(r'\\operatorname\{([^}]+)\}', r'\1 ', s)
    # \mathcal{X}, \mathbb{X}, \mathrm{X} etc. → X
    s = re.sub(r'\\math\w+\{([^}]+)\}', r'\1', s)
    # \mathcal X (no braces, single letter) → X
    s = re.sub(r'\\math\w+\s+([A-Za-z])', r'\1', s)
    # Strip $...$ wrappers (keep inner content)
    s = re.sub(r'\$([^$]+)\$', r'\1', s)
    # Strip remaining \commands (leave their arguments in place)
    s = re.sub(r'\\[a-zA-Z]+', '', s)
    # Collapse multiple spaces
    s = re.sub(r'  +', ' ', s).strip()
    return s


def build_index(content, min_level=2):
    """Build a nested index from markdown headings, indented relative to the shallowest level."""
    headings = re.findall(r'^(#{' + str(min_level) + r',4})\s+(.+)$', content, re.MULTILINE)
    if not headings:
        return None
    base_level = min(len(h) for h, _ in headings)
    lines = []
    for hashes, title in headings:
        indent = '  ' * (len(hashes) - base_level)
        display = _heading_display(title)
        if display != title:
            lines.append(f'{indent}- [[#{title}|{display}]]')
        else:
            lines.append(f'{indent}- [[#{title}]]')
    return '\n'.join(lines)


def strip_title_suffix(title):
    """Strip trailing site-name suffix when title has 3+ parts (e.g. 'Page - Section - Site').
    Two-part titles like 'Commands - Developer Documentation' are kept as-is."""
    if not title:
        return title
    parts = re.split(r'\s+[-—|]\s+', title)
    if len(parts) >= 3:
        return re.sub(r'\s+[-—|]\s+[^-—|]+$', '', title).strip()
    return title


def _dedup_note_tags(note):
    """Remove duplicate tags from a note's YAML frontmatter (case-insensitive, order preserved)."""
    m = re.search(r'(tags:\n)((?:  - [^\n]+\n)+)', note)
    if not m:
        return note
    seen = set()
    deduped = []
    for line in m.group(2).splitlines():
        key = line.strip().removeprefix('- ').lower()
        if key not in seen:
            seen.add(key)
            deduped.append(line)
    return note[:m.start()] + m.group(1) + '\n'.join(deduped) + '\n' + note[m.end():]


def build_youtube(d, url, project, created, method=None):
    title     = strip_title_suffix(d.get('title', ''))
    author    = d.get('author', '')
    published = (d.get('published') or '')[:10]
    site      = d.get('site', 'YouTube')
    words     = d.get('wordCount')
    content   = d.get('content', '')

    vid_id    = youtube_id(url)
    short_url = f'https://youtu.be/{vid_id}' if vid_id else url

    yt_desc, duration, yt_chapters = yt_extra(url)
    desc = strip_timecode_lines(yt_desc or d.get('description', ''))

    # Try VTT for granular per-sentence timestamps
    vtt_transcript = fetch_vtt(url, vid_id) if vid_id else None

    if vtt_transcript:
        transcript_body = vtt_transcript
        if yt_chapters:
            # Use accurate chapter timestamps from yt-dlp
            chapter_list = [{'time': fmt_tc(int(ch['start_time'])),
                             'title': 'Introduction' if re.match(r'^<Untitled Chapter', ch['title']) else ch['title']}
                            for ch in yt_chapters]
            transcript_body = insert_chapters(transcript_body, chapter_list)
            has_chapters = True
        else:
            has_chapters = False  # Let Gemini generate chapters
    else:
        # Fall back to defuddle's transcript
        lines = content.split('\n')
        i = 0
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].startswith('!['):
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i < len(lines) and lines[i].strip() == '## Transcript':
            i += 1
        transcript_body = '\n'.join(lines[i:]).strip()

        if vid_id:
            transcript_body = linkify_timecodes(transcript_body, vid_id)

        has_chapters = bool(re.search(r'^### ', transcript_body, re.MULTILINE))

    # Gemini enrichment
    yt_content = f"Description:\n{desc}\n\nTranscript:\n{transcript_body}"
    ai_desc, ai_summary, ai_tags, ai_chapters = ai_enrich(
        title, author, yt_content, needs_chapters=not has_chapters
    )

    # Insert AI-generated chapters if needed
    if not has_chapters and ai_chapters:
        transcript_body = insert_chapters(transcript_body, ai_chapters)
        has_chapters = True

    # Collect and deduplicate tags (no # prefix — Obsidian adds it in display)
    desc_tags = extract_desc_tags(desc)
    ai_tags_clean = [t.lower() for t in ai_tags]
    all_tags = list(dict.fromkeys(desc_tags + ai_tags_clean))

    # Remove hashtags from description text
    desc = remove_desc_tags(desc)

    # Build Contents from chapter headings
    contents = build_index(transcript_body) or '[Chapters to be added]'

    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter('video', project, created, all_tags, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_field('url', short_url),
        frontmatter_field('channel', author),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('method', method),
        frontmatter_field('duration', duration),
        frontmatter_field('words', words, quote=False) if words else None,
    ])
    summary = ai_summary if ai_summary else '[Summary to be added]'

    return f"""{fm}

---

# {title}

## Summary

{summary}

## Description

![]({short_url})

{desc}

## Contents

{contents}

---

## Transcript

{transcript_body}
"""


# ── Apple Podcasts ────────────────────────────────────────────────────────────

PODCASTS_LIBRARY = os.path.expanduser(
    '~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts'
)


def is_apple_podcast(url):
    return 'podcasts.apple.com' in url


def apple_podcast_id(url):
    """Extract the episode ID from the ?i= query parameter."""
    m = re.search(r'[?&]i=(\d+)', url)
    return int(m.group(1)) if m else None


def apple_show_id(url):
    """Extract the show ID from the /id{N} path component."""
    m = re.search(r'/id(\d+)', url)
    return int(m.group(1)) if m else None


def find_podcast_ttml(episode_id):
    """Find the local TTML transcript file for an episode by its ID."""
    pattern = os.path.join(PODCASTS_LIBRARY, '**', f'*{episode_id}*.ttml')
    files = glob.glob(pattern, recursive=True)
    return files[0] if files else None


def parse_ttml(ttml_path):
    """Parse Apple Podcasts TTML transcript into [{speaker, text, time}] list.

    Parses at the sentence-span level (each <span podcasts:unit="sentence"> has its
    own begin timestamp) and groups ~5 consecutive sentences per chunk, splitting on
    speaker changes. This handles both multi-paragraph files (one <p> per utterance)
    and single-paragraph files (one <p> for the whole episode).
    """
    import xml.etree.ElementTree as ET
    NS_TT  = 'http://www.w3.org/ns/ttml'
    NS_TTM = 'http://www.w3.org/ns/ttml#metadata'
    NS_POD = 'http://podcasts.apple.com/transcript-ttml-internal'
    SENTENCES_PER_CHUNK = 5
    tree = ET.parse(ttml_path)
    root = tree.getroot()

    # Collect all sentences with speaker and begin time
    sentences = []
    for p in root.iter(f'{{{NS_TT}}}p'):
        speaker = p.get(f'{{{NS_TTM}}}agent', '')
        for span in p:
            if span.get(f'{{{NS_POD}}}unit') != 'sentence':
                continue
            begin_str = span.get('begin') or p.get('begin') or '0'
            begin = ttml_time_to_seconds(begin_str)
            words = [s.text for s in span
                     if s.get(f'{{{NS_POD}}}unit') == 'word' and s.text]
            text = ' '.join(words).strip()
            if text:
                sentences.append({'speaker': speaker, 'text': text, 'time': begin})

    # Group into chunks, splitting on speaker changes and at SENTENCES_PER_CHUNK
    chunks = []
    i = 0
    while i < len(sentences):
        current_speaker = sentences[i]['speaker']
        group = []
        while i < len(sentences) and len(group) < SENTENCES_PER_CHUNK and sentences[i]['speaker'] == current_speaker:
            group.append(sentences[i])
            i += 1
        if group:
            chunks.append({
                'speaker': group[0]['speaker'],
                'text': ' '.join(s['text'] for s in group),
                'time': group[0]['time'],
            })
    return chunks


def get_podcast_metadata(episode_id, show_id=None):
    """Query MTLibrary.sqlite for episode and show metadata.
    Falls back to the iTunes API (show lookup) when the episode is not in the local DB
    (e.g. previewed from a non-subscribed podcast)."""
    import sqlite3

    # 1. Try local SQLite
    db_path = os.path.join(PODCASTS_LIBRARY, 'Documents', 'MTLibrary.sqlite')
    if os.path.exists(db_path):
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        c = conn.cursor()
        c.execute('''
            SELECT e.ZCLEANEDTITLE, e.ZAUTHOR, e.ZITUNESSUBTITLE,
                   e.ZDURATION, e.ZPUBDATE, e.ZGUID,
                   p.ZTITLE, p.ZFEEDURL, p.ZSTORECLEANURL,
                   e.ZSEASONNUMBER, e.ZEPISODENUMBER
            FROM ZMTEPISODE e
            JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
            WHERE e.ZSTORETRACKID = ?
        ''', (int(episode_id),))
        row = c.fetchone()
        conn.close()
        if row:
            pub_unix = (row[4] + 978307200) if row[4] else None
            pub_date = datetime.fromtimestamp(pub_unix, tz=timezone.utc).strftime('%Y-%m-%d') if pub_unix else ''
            return {
                'title':       row[0] or '',
                'author':      row[1] or '',
                'description': row[2] or '',
                'duration':    row[3],
                'published':   pub_date,
                'guid':        row[5] or '',
                'show':        row[6] or '',
                'feed_url':    row[7] or '',
                'store_url':   row[8] or '',
                'season':      row[9],
                'episode':     row[10],
            }

    # 2. Fall back to iTunes show lookup (more reliable than direct episode lookup).
    # Paginates in batches of 200 until the episode is found or results are exhausted.
    if not show_id:
        return {}
    try:
        ep = None
        offset = 0
        while ep is None:
            api_url = (f'https://itunes.apple.com/lookup?id={show_id}'
                       f'&entity=podcastEpisode&limit=200&offset={offset}')
            result = subprocess.run(['curl', '-sL', '--max-time', '15', api_url],
                                    capture_output=True, timeout=20)
            data = json.loads(result.stdout.decode('utf-8', errors='replace'))
            batch = [r for r in data.get('results', []) if r.get('kind') == 'podcast-episode']
            if not batch:
                break
            ep = next((r for r in batch if r.get('trackId') == int(episode_id)), None)
            if len(batch) < 200:
                break  # last page
            offset += 200
        if not ep:
            return {}
        duration_ms = ep.get('trackTimeMillis')
        return {
            'title':       ep.get('trackName', ''),
            'author':      ep.get('collectionName', ''),
            'description': ep.get('description', ''),
            'duration':    int(duration_ms / 1000) if duration_ms else None,
            'published':   ep.get('releaseDate', '')[:10],
            'guid':        ep.get('episodeGuid', ''),
            'show':        ep.get('collectionName', ''),
            'feed_url':    ep.get('feedUrl', ''),
            'store_url':   f'https://podcasts.apple.com/podcast/id{show_id}',
            'artwork_url': ep.get('artworkUrl600', ''),
        }
    except Exception:
        return {}


def get_podcast_chapters(meta):
    """Fetch chapters and episode art for an episode.

    Returns (chapters, episode_img) where:
    - chapters is a list of {time, title, img?} dicts (img only present when available)
    - episode_img is the episode artwork URL string, or '' if not found

    Chapter sources (in priority order):
    1. ZMTCHAPTER in local SQLite (rare — usually empty)
    2. <podcast:chapters> JSON URL from RSS feed item
    """
    import sqlite3, struct

    # 1. Try ZMTCHAPTER in local DB
    db_path = os.path.join(PODCASTS_LIBRARY, 'Documents', 'MTLibrary.sqlite')
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            c = conn.cursor()
            c.execute('SELECT Z_PK FROM ZMTEPISODE WHERE ZGUID = ?', (meta.get('guid', ''),))
            row = c.fetchone()
            if row:
                c.execute('SELECT ZTITLE, ZTIMEFRAMESDATA FROM ZMTCHAPTER WHERE ZEPISODE = ? ORDER BY Z_PK', (row[0],))
                db_chapters = []
                for title, time_data in c.fetchall():
                    if not title or not time_data:
                        continue
                    secs = None
                    try:
                        if len(time_data) >= 8:
                            secs = struct.unpack_from('<d', time_data)[0]
                        elif len(time_data) >= 4:
                            secs = struct.unpack_from('<f', time_data)[0]
                    except Exception:
                        pass
                    if secs is not None and secs >= 0:
                        db_chapters.append({'time': fmt_tc(int(secs)), 'title': title})
                if db_chapters:
                    conn.close()
                    return db_chapters, ''
            conn.close()
        except Exception:
            pass

    # 2. Try <podcast:chapters> URL from RSS feed; also grab episode artwork
    feed_url = meta.get('feed_url', '')
    guid     = meta.get('guid', '')
    if not feed_url or not guid:
        return [], ''
    try:
        result = subprocess.run(['curl', '-sL', '--max-time', '15', feed_url],
                                capture_output=True, timeout=20)
        feed = result.stdout.decode('utf-8', errors='replace')
        items = re.split(r'<item[\s>]', feed, flags=re.IGNORECASE)
        chapters_url = ''
        episode_img  = ''
        for item in items:
            if guid in item:
                m = re.search(r'<podcast:chapters[^>]+url=["\']([^"\']+)["\']', item, re.IGNORECASE)
                if m:
                    chapters_url = m.group(1)
                img_m = re.search(r'<itunes:image[^>]+href=["\']([^"\']+)["\']', item, re.IGNORECASE)
                if img_m:
                    episode_img = img_m.group(1)
                break
        if not chapters_url:
            return [], episode_img
        result = subprocess.run(['curl', '-sL', '--max-time', '15', chapters_url],
                                capture_output=True, timeout=20)
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        chapters = [
            {
                'time':  fmt_tc(int(ch['startTime'])),
                'title': ch['title'],
                **({'img': ch['img']} if ch.get('img') else {}),
            }
            for ch in data.get('chapters', [])
            if ch.get('title') and ch.get('startTime') is not None
        ]
        return chapters, episode_img
    except Exception:
        return [], ''


def build_apple_podcast(url, project, created, ttml_path):
    """Build an Obsidian note for an Apple Podcasts episode from a local TTML transcript."""
    episode_id = apple_podcast_id(url)
    show_id    = apple_show_id(url)
    chunks     = parse_ttml(ttml_path)
    meta       = get_podcast_metadata(episode_id, show_id) if episode_id else {}

    title      = meta.get('title', '')
    show       = meta.get('show', '')
    author     = meta.get('author', '') or show
    desc       = strip_timecode_lines(meta.get('description', ''))
    duration   = fmt_tc(meta['duration']) if meta.get('duration') else ''
    published  = meta.get('published', '')
    store_url  = meta.get('store_url', '')
    season     = meta.get('season')
    episode    = meta.get('episode')

    words = sum(len(chunk['text'].split()) for chunk in chunks)

    # Build transcript with timed deep links: [**M:SS**](podcasts-url&t=N)
    transcript_lines = []
    for chunk in chunks:
        secs    = int(chunk['time'])
        tc      = fmt_tc(secs)
        speaker = chunk['speaker']
        if store_url and episode_id:
            link = f'[**{tc}**]({store_url}?i={episode_id}&t={secs})'
        else:
            link = f'[**{tc}**]'
        speaker_label = re.sub(r'SPEAKER_(\d+)', r'Speaker \1', speaker)
        transcript_lines.append(f'{link} **{speaker_label}:** {chunk["text"]}')
    transcript_body = '\n\n'.join(transcript_lines)

    # Chapters: native (RSS podcast:chapters) → Gemini fallback
    native_chapters, episode_img = get_podcast_chapters(meta)
    if not episode_img:
        episode_img = meta.get('artwork_url', '')
    has_chapters    = bool(native_chapters)
    if has_chapters:
        transcript_body = insert_chapters(transcript_body, native_chapters)

    # Gemini enrichment
    pod_content = f'Description:\n{desc}\n\nTranscript:\n{transcript_body}'
    ai_desc, ai_summary, ai_tags, ai_chapters = ai_enrich(
        title, author, pod_content, needs_chapters=not has_chapters
    )

    if not has_chapters and ai_chapters:
        transcript_body = insert_chapters(transcript_body, ai_chapters)
        has_chapters = True

    contents = build_index(transcript_body) or '[Chapters to be added]'

    # Frontmatter
    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter('podcast', project, created, [t.lower() for t in ai_tags], [
        frontmatter_field('title',     title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_field('url',       url),
        frontmatter_field('show',      show),
        frontmatter_field('season',    season,  quote=False) if season is not None else None,
        frontmatter_field('episode',   episode, quote=False) if episode is not None else None,
        frontmatter_field('published', published),
        frontmatter_field('site',      'Apple Podcasts'),
        frontmatter_field('duration',  duration),
        frontmatter_field('words',     words, quote=False) if words else None,
    ])
    summary = ai_summary if ai_summary else '[Summary to be added]'
    episode_art = f'\n![Episode art]({episode_img})\n' if episode_img else ''

    return f"""{fm}

---

# {title}

## Summary

{summary}

## Description
{episode_art}
{desc}

## Contents

{contents}

---

## Transcript

{transcript_body}
"""


# ---------------------------------------------------------------------------
# Academic paper support (DOI / arXiv)
# ---------------------------------------------------------------------------

def is_doi_url(url):
    return bool(re.match(r'https?://(dx\.)?doi\.org/', url)) or \
           bool(re.match(r'https?://(www\.)?arxiv\.org/abs/', url))

def extract_doi(url):
    m = re.match(r'https?://(?:dx\.)?doi\.org/(.+)', url)
    return m.group(1) if m else None

def extract_arxiv_id_from_doi(doi):
    m = re.search(r'arXiv\.(\d{4}\.\d{4,5})', doi, re.IGNORECASE)
    return m.group(1) if m else None

def extract_arxiv_id_from_url(url):
    m = re.search(r'arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)', url)
    return m.group(1) if m else None

def _get_paper_metadata_arxiv(arxiv_id):
    """Fetch paper metadata from the arXiv API (Atom feed)."""
    try:
        import xml.etree.ElementTree as ET
        result = subprocess.run(
            ['curl', '-sL', '--max-time', '10',
             f'https://export.arxiv.org/api/query?id_list={arxiv_id}'],
            capture_output=True, timeout=15
        )
        root = ET.fromstring(result.stdout.decode('utf-8', errors='replace'))
        ns = {'atom': 'http://www.w3.org/2005/Atom',
              'arxiv': 'http://arxiv.org/schemas/atom'}
        entry = root.find('atom:entry', ns)
        if entry is None:
            return {}
        title = (entry.findtext('atom:title', '', ns) or '').strip().replace('\n', ' ')
        authors = []
        for a in entry.findall('atom:author', ns):
            name = (a.findtext('atom:name', '', ns) or '').strip()
            if name:
                authors.append(name)
        published = (entry.findtext('atom:published', '', ns) or '')[:10]
        journal_ref = (entry.findtext('arxiv:journal_ref', '', ns) or '').strip()
        return {'title': title, 'authors': authors, 'published': published,
                'journal': journal_ref, 'doi': f'10.48550/arXiv.{arxiv_id}'}
    except Exception:
        return {}

def get_paper_metadata(doi):
    """Fetch paper metadata from CrossRef API, with arXiv API fallback."""
    # For arXiv DOIs (10.48550/arXiv.XXXX), use the arXiv API directly
    arxiv_id = extract_arxiv_id_from_doi(doi)
    if arxiv_id:
        return _get_paper_metadata_arxiv(arxiv_id)
    try:
        result = subprocess.run(
            ['curl', '-sL', '--max-time', '10',
             '-H', 'User-Agent: defuddle/1.0 (mailto:user@example.com)',
             f'https://api.crossref.org/works/{doi}'],
            capture_output=True, timeout=15
        )
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        work = data.get('message', {})
        title = ' '.join(work.get('title', [''])).strip()
        authors = []
        for a in work.get('author', []):
            name = f'{a.get("given", "")} {a.get("family", "")}'.strip()
            if name:
                authors.append(name)
        date_parts = (work.get('published') or work.get('issued') or {}).get('date-parts', [[]])[0]
        if len(date_parts) >= 3:
            pub_date = f'{date_parts[0]:04d}-{date_parts[1]:02d}-{date_parts[2]:02d}'
        elif len(date_parts) == 2:
            pub_date = f'{date_parts[0]:04d}-{date_parts[1]:02d}'
        elif len(date_parts) == 1:
            pub_date = str(date_parts[0])
        else:
            pub_date = ''
        container = work.get('container-title', [])
        journal = container[0] if container else work.get('publisher', '')
        return {'title': title, 'authors': authors, 'published': pub_date,
                'journal': journal, 'doi': doi}
    except Exception:
        return {}

def find_arxiv_id(doi):
    """Look up arXiv preprint for a DOI via Semantic Scholar."""
    try:
        result = subprocess.run(
            ['curl', '-sL', '--max-time', '10',
             f'https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=externalIds'],
            capture_output=True, timeout=15
        )
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        return data.get('externalIds', {}).get('ArXiv')
    except Exception:
        return None

def get_arxiv_latex(arxiv_id, tmp_dir):
    """Download arXiv LaTeX source and return path to main .tex file."""
    import tarfile as tarmod, gzip as gzmod
    src_path = os.path.join(tmp_dir, 'source.bin')
    subprocess.run(
        ['curl', '-sL', '--max-time', '30', '-o', src_path,
         f'https://arxiv.org/e-print/{arxiv_id}'],
        timeout=35
    )
    if not os.path.exists(src_path) or os.path.getsize(src_path) < 100:
        return None
    extract_dir = os.path.join(tmp_dir, 'source')
    os.makedirs(extract_dir, exist_ok=True)
    try:
        with tarmod.open(src_path) as tf:
            tf.extractall(extract_dir)
    except Exception:
        try:
            with gzmod.open(src_path) as gz:
                tex_path = os.path.join(extract_dir, 'main.tex')
                with open(tex_path, 'wb') as f:
                    f.write(gz.read())
                return tex_path
        except Exception:
            return None
    tex_files = []
    for root, _, files in os.walk(extract_dir):
        for fname in files:
            if not fname.endswith('.tex'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', errors='ignore') as f:
                    if '\\documentclass' in f.read(1000):
                        tex_files.append(fpath)
            except Exception:
                pass
    if not tex_files:
        return None
    for preferred in ('main.tex', 'paper.tex', 'article.tex', 'ms.tex'):
        for p in tex_files:
            if os.path.basename(p) == preferred:
                return p
    return tex_files[0]

_PANDOC_KNOWN_ENVS = {
    'document', 'abstract', 'itemize', 'enumerate', 'description',
    'verbatim', 'lstlisting', 'minted', 'quote', 'quotation', 'verse',
    'flushleft', 'flushright', 'center', 'minipage',
    'figure', 'figure*', 'table', 'table*', 'tabular', 'tabular*', 'array',
    'equation', 'equation*', 'align', 'align*', 'gather', 'gather*',
    'multline', 'multline*', 'split', 'cases',
    'pmatrix', 'bmatrix', 'vmatrix', 'Vmatrix', 'matrix', 'smallmatrix',
    'theorem', 'lemma', 'corollary', 'proposition', 'conjecture',
    'definition', 'example', 'remark', 'note', 'exercise', 'problem',
    'proof', 'solution',
    'thebibliography', 'tikzpicture', 'frame',
}

# Common shorthand theorem environments → pandoc-known equivalents
_THEOREM_ENV_MAP = {
    'thm': 'theorem', 'lem': 'lemma', 'cor': 'corollary', 'prop': 'proposition',
    'rem': 'remark', 'defi': 'definition', 'defn': 'definition', 'ex': 'example',
    'conj': 'conjecture', 'obs': 'remark', 'claim': 'theorem', 'fact': 'remark',
    'hyp': 'conjecture', 'ass': 'remark', 'prp': 'proposition',
}

def _expand_simple_macros(content):
    """Expand zero- and one-argument \\newcommand macros (single pass, non-recursive).

    Only expands macros declared in the same file.  Handles the common pattern
    of equation shorthand macros, e.g.:
        \\newcommand{\\eq}[1]{\\begin{equation}\\label{#1}\\quad}
        \\newcommand{\\en}{\\end{equation}}
    so that \\eq{BC} → \\begin{equation}\\label{BC}\\quad and \\en → \\end{equation},
    allowing _collect_labels to correctly track equation counters and labels.

    The \\newcommand definition lines are removed before expansion so that macros
    do not expand inside their own definitions.
    """
    def _brace_end(s, pos):
        """Return the index after the closing '}' of a brace group starting at pos."""
        if pos >= len(s) or s[pos] != '{':
            return pos
        depth = 0
        for i in range(pos, len(s)):
            if s[i] == '{':
                depth += 1
            elif s[i] == '}':
                depth -= 1
                if depth == 0:
                    return i + 1
        return len(s)

    macros_0, macros_1 = {}, {}
    def_spans = []  # (start, end) of each \newcommand block to strip

    for m in re.finditer(r'\\newcommand\s*\{\\(\w+)\}', content):
        name, pos = m.group(1), m.end()
        n_args = 0
        an = re.match(r'\s*\[(\d)\]', content[pos:])
        if an:
            n_args, pos = int(an.group(1)), pos + an.end()
        while pos < len(content) and content[pos] in ' \t\n':
            pos += 1
        if pos >= len(content) or content[pos] != '{':
            continue
        defn_start = pos + 1
        defn_end = _brace_end(content, pos)
        defn = content[defn_start:defn_end - 1]
        # Skip past optional trailing newline so blank lines aren't left behind
        span_end = defn_end
        if span_end < len(content) and content[span_end] == '\n':
            span_end += 1
        def_spans.append((m.start(), span_end))
        if n_args > 1:
            continue
        (macros_1 if n_args == 1 else macros_0)[name] = defn

    # Remove \newcommand definitions so they don't self-expand
    if def_spans:
        parts = []
        prev = 0
        for start, end in sorted(def_spans):
            parts.append(content[prev:start])
            prev = end
        parts.append(content[prev:])
        content = ''.join(parts)

    # Expand 1-arg macros (longest name first to avoid partial matches)
    for name in sorted(macros_1, key=len, reverse=True):
        defn = macros_1[name]
        # LaTeX tokenises \command{arg} with implicit spacing, but string
        # substitution doesn't.  Add a space before #1 when preceded by a
        # letter (end of a command name) so e.g. \langle#1 → \langle #1,
        # preventing \langleAx from being read as a single command name.
        safe_defn = re.sub(r'(?<=[a-zA-Z])#1', ' #1', defn)
        content = re.sub(
            r'\\' + re.escape(name) + r'\{([^{}]*)\}',
            lambda m, d=safe_defn: d.replace('#1', m.group(1)), content)

    # Expand 0-arg macros
    for name in sorted(macros_0, key=len, reverse=True):
        defn = macros_0[name]
        content = re.sub(
            r'\\' + re.escape(name) + r'(?![a-zA-Z])',
            lambda m, d=defn: d, content)

    return content


def _preprocess_latex(content):
    """Preprocess LaTeX so pandoc can parse it without choking."""
    # Expand simple \newcommand macros so structural commands like \begin/\end
    # are visible to _collect_labels even when wrapped in paper-specific shorthands.
    content = _expand_simple_macros(content)
    # Strip optional spacing args from \\ line breaks in math: \\[6pt] → \\
    content = re.sub(r'\\\\(\[\s*[\d.]+\s*(?:pt|em|ex|cm|mm|in|bp|pc|sp)\s*\])', r'\\\\', content)
    # Strip \iffalse...\fi blocks (commented-out code)
    content = re.sub(r'\\iffalse\b.*?\\fi\b', '', content, flags=re.DOTALL)
    # Strip \newtheorem declarations so pandoc does not add its own sequential
    # numbering before our injected \textbf{N} markers.  Counter information is
    # parsed from the *raw* source (before this step) in latex_to_markdown.
    content = re.sub(
        r'\\newtheorem\{[^}]+\}(?:\[[^\]]*\])?(?:\{[^}]*\})?(?:\[[^\]]*\])?[^\n]*\n?',
        '', content)
    # Map shorthand theorem environments to pandoc-known equivalents
    for short, full in _THEOREM_ENV_MAP.items():
        content = re.sub(
            r'\\begin\{' + re.escape(short) + r'\}(\[[^\]]*\])?(\{[^}]*\})*',
            r'\\begin{' + full + r'}', content)
        content = re.sub(r'\\end\{' + re.escape(short) + r'\}',
                         r'\\end{' + full + r'}', content)
    # Replace remaining unknown environments with \begin{quote}/\end{quote}
    used = set(re.findall(r'\\begin\{(\w+\*?)\}', content))
    for env in used - _PANDOC_KNOWN_ENVS:
        content = re.sub(
            r'\\begin\{' + re.escape(env) + r'\}(\[[^\]]*\])?(\{[^}]*\})*',
            r'\\begin{quote}', content)
        content = re.sub(r'\\end\{' + re.escape(env) + r'\}', r'\\end{quote}', content)
    return content

def _run_pandoc(pre_path, out_path, cwd, fmt='latex'):
    """Run pandoc -f {fmt} -t markdown. Returns True on success."""
    result = subprocess.run(
        ['pandoc', '-f', fmt, '-t', 'markdown', '--wrap=none', '-o', out_path, pre_path],
        capture_output=True, cwd=cwd, timeout=60
    )
    return result.returncode == 0 and os.path.exists(out_path)

_NUMBERED_THEOREM_ENVS = {
    'theorem', 'lemma', 'corollary', 'proposition', 'conjecture',
    'definition', 'remark', 'example',
}
_NUMBERED_EQ_ENVS = {
    'equation', 'align', 'gather', 'multline', 'eqnarray', 'flalign', 'alignat',
}

def _parse_newtheorem(content):
    """Parse \\newtheorem declarations; return (env_counter, counter_within) dicts."""
    env_counter    = {}   # canonical_env -> counter_name
    counter_within = {}   # counter_name  -> 'section' | None
    for m in re.finditer(
            r'\\newtheorem\{(\w+)\}(?:\[(\w+)\])?\{[^}]*\}(?:\[(\w+)\])?', content):
        env, shared, within = m.group(1), m.group(2), m.group(3)
        canonical = _THEOREM_ENV_MAP.get(env, env)
        if shared:
            env_counter[canonical] = _THEOREM_ENV_MAP.get(shared, shared)
        else:
            env_counter[canonical] = canonical
            counter_within[canonical] = within
    for env in _NUMBERED_THEOREM_ENVS:
        if env not in env_counter:
            env_counter[env] = env
            counter_within.setdefault(env, 'section')
    return env_counter, counter_within


def _collect_labels(content, nt_data=None):
    """Scan preprocessed LaTeX and return {label: number_string}.

    Processes \\newtheorem declarations, section/theorem/equation counters.
    Labels are collected unconditionally after each line's counter updates so
    they are correctly associated even when \\label appears inside nested envs
    (e.g. \\label inside \\begin{bmatrix} inside \\begin{equation}).

    nt_data: optional (env_counter, counter_within) tuple from _parse_newtheorem
    called on the raw (pre-preprocessed) source.  If omitted, parsed from content.
    """
    env_counter, counter_within = nt_data if nt_data else _parse_newtheorem(content)
    sec       = [0, 0, 0]
    thm_cnt   = {}
    eq_cnt    = [0]
    labels    = {}
    env_stack = []  # list of (canonical_env_name, number_string)

    for line in content.split('\n'):
        # ── Section commands ─────────────────────────────────────────────────
        sec_level = None
        for level, cmd in enumerate(['section', 'subsection', 'subsubsection']):
            m_sec = re.search(r'\\' + cmd + r'(?!\w)(\*?)\s*\{', line)
            if m_sec:
                if not m_sec.group(1):
                    sec[level] += 1
                    for j in range(level + 1, 3):
                        sec[j] = 0
                    for c, w in counter_within.items():
                        if w == 'section':
                            thm_cnt[c] = 0
                    eq_cnt[0] = 0
                    sec_level = level
                break

        # ── \begin{env}: push numbered envs onto stack ───────────────────────
        bm = re.search(r'\\begin\{(\w+\*?)\}', line)
        if bm:
            raw_env   = bm.group(1)
            starred   = raw_env.endswith('*')
            base_env  = raw_env.rstrip('*')
            canonical = _THEOREM_ENV_MAP.get(base_env, base_env)
            if not starred and canonical in env_counter:
                ctr = env_counter[canonical]
                thm_cnt[ctr] = thm_cnt.get(ctr, 0) + 1
                n = (f'{sec[0]}.{thm_cnt[ctr]}'
                     if counter_within.get(ctr) == 'section' and sec[0]
                     else str(thm_cnt.get(ctr, 1)))
                env_stack.append((canonical, n))
            elif not starred and base_env in _NUMBERED_EQ_ENVS:
                eq_cnt[0] += 1
                n = f'{sec[0]}.{eq_cnt[0]}' if sec[0] else str(eq_cnt[0])
                env_stack.append(('__eq__', n))

        # ── Collect labels — unconditional, after all counter updates ────────
        # This correctly handles \label inside nested envs (e.g. bmatrix inside
        # equation) because the enclosing numbered env is already on the stack.
        if '\\label' in line:
            if env_stack:
                ctx_num = env_stack[-1][1]
            elif sec_level is not None:
                ctx_num = '.'.join(str(sec[i]) for i in range(sec_level + 1))
            else:
                ctx_num = None
            if ctx_num:
                for lm in re.finditer(r'\\label\{([^}]+)\}', line):
                    labels.setdefault(lm.group(1), ctx_num)

        # ── \end{env}: pop stack ─────────────────────────────────────────────
        em = re.search(r'\\end\{(\w+\*?)\}', line)
        if em and env_stack:
            canonical = _THEOREM_ENV_MAP.get(em.group(1).rstrip('*'),
                                              em.group(1).rstrip('*'))
            if env_stack[-1][0] in (canonical, '__eq__'):
                env_stack.pop()

    return labels


def _inject_ref_numbers(content, labels, nt_data=None):
    """Replace \\ref{}/\\eqref{} with resolved numbers, inject theorem/section
    numbers into the LaTeX source so pandoc preserves them in its output.

    Theorem numbers: injects \\textbf{N} on the line after \\begin{env} —
    pandoc emits this as **N** inside the ::: fenced div, which
    _fenced_div_to_bold then extracts as the theorem label number.
    (No brackets: pandoc escapes [ and ] → \\[ \\] which breaks matching.)

    Section numbers: prefixes \\section{title} → \\section{N. title}.

    nt_data: optional (env_counter, counter_within) from _parse_newtheorem on
    raw source (before \\newtheorem stripping).  Needed for counter-sharing info.
    """
    if not labels:
        return content

    def _sub_ref(m):
        n = labels.get(m.group(1))
        return n if n else m.group(0)

    def _sub_eqref(m):
        n = labels.get(m.group(1))
        return f'({n})' if n else m.group(0)

    content = re.sub(r'\\eqref\{([^}]+)\}', _sub_eqref, content)
    content = re.sub(r'\\ref\{([^}]+)\}',   _sub_ref,   content)

    # Second pass: inject theorem numbers and section numbers
    env_counter, counter_within = nt_data if nt_data else _parse_newtheorem(content)
    sec     = [0, 0, 0]
    thm_cnt = {}
    eq_cnt  = [0]

    out_lines = []
    for line in content.split('\n'):
        # ── Section: inject number into title ────────────────────────────────
        sec_injected = False
        for level, cmd in enumerate(['section', 'subsection', 'subsubsection']):
            m_sec = re.search(r'\\' + cmd + r'(?!\w)(\*?)\s*\{', line)
            if m_sec:
                if not m_sec.group(1):
                    sec[level] += 1
                    for j in range(level + 1, 3):
                        sec[j] = 0
                    for c, w in counter_within.items():
                        if w == 'section':
                            thm_cnt[c] = 0
                    eq_cnt[0] = 0
                    n = '.'.join(str(sec[i]) for i in range(level + 1))
                    # Insert number at start of the section title argument
                    insert_pos = m_sec.end()  # position right after '{'
                    line = line[:insert_pos] + n + '. ' + line[insert_pos:]
                    sec_injected = True
                break

        # ── \begin{env}: inject \textbf{[N]} marker ──────────────────────────
        bm = re.search(r'\\begin\{(\w+\*?)\}', line)
        if bm and not sec_injected:
            raw_env   = bm.group(1)
            starred   = raw_env.endswith('*')
            base_env  = raw_env.rstrip('*')
            canonical = _THEOREM_ENV_MAP.get(base_env, base_env)
            if not starred and canonical in env_counter:
                ctr = env_counter[canonical]
                thm_cnt[ctr] = thm_cnt.get(ctr, 0) + 1
                n = (f'{sec[0]}.{thm_cnt[ctr]}'
                     if counter_within.get(ctr) == 'section' and sec[0]
                     else str(thm_cnt.get(ctr, 1)))
                out_lines.append(line)
                # \textbf{N} survives pandoc as **N** — detected by _fenced_div_to_bold
                # (no brackets: pandoc escapes [ and ] → \[ \] which breaks matching)
                out_lines.append(r'\textbf{' + n + r'}')
                continue

        out_lines.append(line)

    return '\n'.join(out_lines)


def latex_to_markdown(tex_path, tmp_dir):
    """Convert LaTeX to Markdown using pandoc (gfm output, markxiv approach)."""
    with open(tex_path, encoding='utf-8', errors='replace') as f:
        raw = f.read()
    # Parse \newtheorem BEFORE preprocessing strips them, to preserve counter-
    # sharing info (e.g. \newtheorem{cor}[thm]{Corollary}).
    nt_data = _parse_newtheorem(raw)
    preprocessed = _preprocess_latex(raw)
    # Resolve cross-references: replace \ref{}/\eqref{} with numbers and
    # inject \textbf{N} markers into theorem environments.
    labels = _collect_labels(preprocessed, nt_data=nt_data)
    preprocessed = _inject_ref_numbers(preprocessed, labels, nt_data=nt_data)
    # Inline .bbl bibliography: try \bibliography{name}.bbl → main.bbl →
    # master.bbl → any .bbl in the source directory.
    source_dir = os.path.dirname(tex_path)
    def _find_bbl(names):
        for name in names:
            p = os.path.join(source_dir, name + '.bbl')
            if os.path.exists(p):
                return p
        for fname in ('main.bbl', 'master.bbl'):
            p = os.path.join(source_dir, fname)
            if os.path.exists(p):
                return p
        for fname in os.listdir(source_dir):
            if fname.endswith('.bbl'):
                return os.path.join(source_dir, fname)
        return None
    def _sub_bbl(m):
        names = [n.strip() for n in m.group(1).split(',')]
        bbl_path = _find_bbl(names)
        if bbl_path:
            with open(bbl_path, encoding='utf-8', errors='replace') as bf:
                return bf.read()
        return m.group(0)
    preprocessed = re.sub(r'\\bibliography\{([^}]+)\}', _sub_bbl, preprocessed)
    pre_path = os.path.join(tmp_dir, 'preprocessed.tex')
    with open(pre_path, 'w', encoding='utf-8') as f:
        f.write(preprocessed)
    out_path = os.path.join(tmp_dir, 'out.md')
    cwd = os.path.dirname(tex_path)
    # Try standard latex first, fall back to latex-latex_macros (no macro expansion)
    if not _run_pandoc(pre_path, out_path, cwd, 'latex'):
        if os.path.exists(out_path):
            os.remove(out_path)
        if not _run_pandoc(pre_path, out_path, cwd, 'latex-latex_macros'):
            return ''
    with open(out_path, encoding='utf-8', errors='replace') as f:
        md = f.read()
    # Strip YAML frontmatter pandoc may emit
    md = re.sub(r'^---\n.*?\n---\n\n?', '', md, count=1, flags=re.DOTALL)
    return md.strip()

def fetch_doi_pdf(doi, tmp_dir):
    """Try to download PDF by following a DOI redirect."""
    pdf_path = os.path.join(tmp_dir, 'paper.pdf')
    result = subprocess.run(
        ['curl', '-sL', '--max-time', '20', '-o', pdf_path,
         '-w', '%{content_type}', f'https://doi.org/{doi}'],
        capture_output=True, text=True, timeout=25
    )
    if 'pdf' in result.stdout.lower() and \
            os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
        return pdf_path
    return None

def fetch_shadow_pdf(doi, tmp_dir):
    """Try to fetch PDF from the configured shadow library."""
    base = SHADOW_BASE_URL.rstrip('/')
    result = subprocess.run(
        ['curl', '-sL', '--max-time', '20', f'{base}/{doi}'],
        capture_output=True, timeout=25
    )
    html_text = result.stdout.decode('utf-8', errors='replace')
    for pattern in [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+\.pdf[^"\']*)["\'][^>]+name=["\']citation_pdf_url["\']',
        r'<object[^>]+data=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'<embed[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'<iframe[^>]+src=["\']([^"\']+\.pdf[^"\']*)["\']',
        r'"pdf_url"\s*:\s*"([^"]+)"',
        r"'pdf_url'\s*:\s*'([^']+)'",
        r'href=["\']([^"\']+\.pdf(?:[^"\'#]*)?)["\']',
        r'src=["\']([^"\']+\.pdf(?:[^"\'#]*)?)["\']',
    ]:
        m = re.search(pattern, html_text, re.IGNORECASE)
        if m:
            pdf_url = m.group(1)
            if pdf_url.startswith('//'):
                pdf_url = 'https:' + pdf_url
            elif not pdf_url.startswith('http'):
                pdf_url = base + pdf_url
            pdf_path = os.path.join(tmp_dir, 'paper.pdf')
            subprocess.run(
                ['curl', '-sL', '--max-time', '30', '-o', pdf_path, pdf_url],
                timeout=35
            )
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
                return pdf_path
    return None

def pdf_to_markdown(pdf_path):
    """Convert a PDF to markdown + images dict.

    Tries Datalab Marker API first, falls back to local marker_single.
    Returns (markdown_str, images_dict).
    """
    # ── Datalab API ───────────────────────────────────────────────────────────
    if DATALAB_API_KEY:
        print('Converting PDF via Datalab Marker API...', file=sys.stderr)
        result = subprocess.run(
            ['curl', '-sL', '-X', 'POST',
             'https://www.datalab.to/api/v1/marker',
             '-H', f'X-Api-Key: {DATALAB_API_KEY}',
             '-F', f'file=@{pdf_path}',
             '-F', 'output_format=markdown',
             '-F', 'mode=balanced'],
            capture_output=True, timeout=60
        )
        try:
            data = json.loads(result.stdout.decode('utf-8', errors='replace'))
            check_url = data.get('request_check_url')
            if check_url:
                print('Waiting for Datalab conversion...', file=sys.stderr)
                for _ in range(60):
                    time.sleep(5)
                    poll = subprocess.run(
                        ['curl', '-sL', check_url, '-H', f'X-Api-Key: {DATALAB_API_KEY}'],
                        capture_output=True, timeout=15
                    )
                    status = json.loads(poll.stdout.decode('utf-8', errors='replace'))
                    if status.get('status') == 'complete':
                        return status.get('markdown', ''), status.get('images', {})
                    if status.get('status') == 'failed':
                        print(f'Datalab failed: {status.get("error", "")} — falling back to local marker...', file=sys.stderr)
                        break
                else:
                    print('Datalab timed out — falling back to local marker...', file=sys.stderr)
            else:
                print(f'Datalab error: {data.get("error", "no request URL")} — falling back to local marker...', file=sys.stderr)
        except Exception as e:
            print(f'Datalab error: {e} — falling back to local marker...', file=sys.stderr)

    # ── Local marker_single ───────────────────────────────────────────────────
    if not shutil.which('marker_single'):
        print(
            'PDF conversion requires either a Datalab API key or local marker.\n'
            'Install with: pip install marker-pdf',
            file=sys.stderr
        )
        return '', {}

    # Map AI_LLM to marker's --llm_service and key flag
    _MARKER_LLM = {
        'gemini': ('marker.services.gemini.GoogleGeminiService', '--gemini_api_key'),
        'openai': ('marker.services.openai.OpenAIService',       '--openai_api_key'),
        'claude': ('marker.services.claude.ClaudeService',       '--claude_api_key'),
        'ollama': ('marker.services.ollama.OllamaService',       None),
    }
    marker_service, key_flag = _MARKER_LLM.get(AI_LLM, (None, None))
    use_llm = bool(marker_service and (AI_API_KEY or AI_LLM == 'ollama'))

    with tempfile.TemporaryDirectory() as out_dir:
        cmd = ['marker_single', pdf_path, '--output_dir', out_dir, '--output_format', 'markdown']
        if use_llm:
            cmd += ['--use_llm', '--llm_service', marker_service]
            if key_flag and AI_API_KEY:
                cmd += [key_flag, AI_API_KEY]
            print(f'Running local marker (with {AI_LLM} LLM)...', file=sys.stderr)
        else:
            print('Running local marker...', file=sys.stderr)

        subprocess.run(cmd, capture_output=True, timeout=600)

        md_files = glob.glob(os.path.join(out_dir, '**', '*.md'), recursive=True)
        if not md_files:
            return '', {}

        with open(md_files[0], encoding='utf-8') as f:
            markdown = f.read()

        import base64 as _b64
        _IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
        images_dict = {}
        for img_file in glob.glob(os.path.join(out_dir, '**', '*'), recursive=True):
            if os.path.splitext(img_file)[1].lower() in _IMG_EXT:
                try:
                    with open(img_file, 'rb') as f:
                        images_dict[os.path.basename(img_file)] = _b64.b64encode(f.read()).decode()
                except Exception:
                    pass

        return markdown, images_dict

def _latex_braced_arg(content, cmd):
    """Extract the content of \\cmd{...} with proper nested-brace handling."""
    m = re.search(r'\\' + re.escape(cmd) + r'\s*\{', content)
    if not m:
        return ''
    start = m.end()
    depth = 1
    i = start
    while i < len(content) and depth:
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
        i += 1
    return content[start:i - 1]

def _latex_strip_markup(s):
    """Remove LaTeX commands/braces, leaving plain text."""
    s = re.sub(r'\$[^$]*\$', '', s)           # strip inline math ($^1$, $^{1,2}$, etc.)
    # Remove \cmd{...} recursively (won't handle deep nesting but good enough)
    for _ in range(5):
        s = re.sub(r'\\[a-zA-Z]+\{([^{}]*)\}', r'\1', s)
    s = re.sub(r'\\[a-zA-Z]+\s*', '', s)
    s = re.sub(r'[{}]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def _metadata_from_latex(tex_path):
    """Extract title and authors from a LaTeX source file."""
    try:
        with open(tex_path, encoding='utf-8', errors='replace') as f:
            content = f.read()

        raw_title = _latex_braced_arg(content, 'title')
        # Strip reference/footnote-marker commands entirely (don't preserve their content)
        raw_title = re.sub(r'\\(?:tnoteref|fnref|corref|thanks)\{[^{}]*\}', '', raw_title)
        title = _latex_strip_markup(raw_title)

        # Handle both \author{name\and name} and \author[label]{name} (Elsevier style)
        authors = []
        # Elsevier: multiple \author[...]{Name} lines
        elsevier = re.findall(r'\\author\[[^\]]*\]\{([^}]+)\}', content)
        if elsevier:
            for name in elsevier:
                name = _latex_strip_markup(name)
                if name:
                    authors.append(name)
        else:
            raw_author = _latex_braced_arg(content, 'author')
            raw_author = re.sub(r'\\(?:thanks|tnoteref|fnref|corref)\{[^{}]*\}', '', raw_author)
            for name in re.split(r'\\and\b', raw_author):
                name = _latex_strip_markup(name)
                if name:
                    authors.append(name)

        abstract_m = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', content, re.DOTALL)
        abstract = abstract_m.group(1).strip() if abstract_m else _latex_braced_arg(content, 'abstract')
        abstract = _latex_strip_markup(abstract)

        return {'title': title, 'authors': authors, 'published': '', 'journal': '',
                'doi': '', 'abstract': abstract}
    except Exception:
        return {}

def _convert_simple_tables(md):
    """Convert pandoc simple tables (space-aligned) to pipe tables for Obsidian."""
    lines = md.split('\n')
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Separator line: only dashes and spaces, at least two dash groups
        if re.match(r'^\s*-{2,}(?:\s+-{2,})+\s*$', line) and i > 0:
            col_starts = [m.start() for m in re.finditer(r'-+', line)]
            if len(col_starts) >= 2:
                def get_cols(s, col_starts=col_starts):
                    cols = []
                    for j, start in enumerate(col_starts):
                        end = col_starts[j + 1] if j + 1 < len(col_starts) else len(s)
                        cols.append(s[start:end].strip() if start < len(s) else '')
                    return cols
                header_line = out.pop()
                header_cols = get_cols(header_line)
                out.append('| ' + ' | '.join(c or ' ' for c in header_cols) + ' |')
                out.append('| ' + ' | '.join('---' for _ in col_starts) + ' |')
                i += 1
                while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(':'):
                    out.append('| ' + ' | '.join(c or ' ' for c in get_cols(lines[i])) + ' |')
                    i += 1
                # Skip blank lines before caption
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines) and lines[i].strip().startswith(':'):
                    out.append('')
                    out.append(lines[i].strip())
                    i += 1
                continue
        out.append(line)
        i += 1
    return '\n'.join(out)

def fix_paper_markdown(md):
    """Sanitise pandoc markdown output for Obsidian compatibility."""
    # 0. Convert standalone $\eqref{label}$ / $\ref{label}$ to plain (label) —
    #    Obsidian MathJax does not support \eqref and renders (???)
    md = re.sub(r'\$\\(?:eqref|ref)\{([^}]+)\}\$', r'(\1)', md)
    # 0b. Replace unsupported MathJax commands with supported equivalents
    _MATH_CMD_REMAP = {
        r'\bm{':         r'\boldsymbol{',    # bm package bold → boldsymbol
        r'\mathbbm{':    r'\mathbb{',        # bbm package → standard blackboard bold
        r'\underbracket': r'\underbrace',    # mathtools → standard
        r'\overbracket':  r'\overbrace',     # mathtools → standard
    }
    for bad, good in _MATH_CMD_REMAP.items():
        md = md.replace(bad, good)
    # \bm\cmd (no braces) → \boldsymbol{\cmd}
    md = re.sub(r'\\bm(\\[a-zA-Z]+)', r'\\boldsymbol{\1}', md)

    # 1. Normalise LaTeX math delimiters: \[...\] → $$...$$ and \(...\) → $...$
    md = re.sub(r'\\\[\s*\n(.*?)\n\s*\\\]', lambda m: f'$$\n{m.group(1)}\n$$', md, flags=re.DOTALL)
    md = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', md, flags=re.DOTALL)
    md = re.sub(r'\\\((.*?)\\\)', r'$\1$', md)

    # 2a. Clean equation-ref links before display math isolation: [$$label$$](#ref) → (label)
    md = re.sub(r'\[\$\$([^$]+)\$\$\]\(#[^)]*\)', r'(\1)', md)
    # 2b. Strip internal cross-ref links: [text](#anchor) → text
    md = re.sub(r'\[([^\]]*)\]\(#[^)]*\)', r'\1', md)
    # 2c. Clean pandoc citation blocks: strip @ signs, replace ; with ,
    # [@Key] → [Key], [@K1; @K2; @K3] → [K1, K2, K3]
    def _clean_citation(m):
        inner = m.group(1)
        inner = inner.replace('@', '').replace(';', ',')
        inner = re.sub(r',\s*,', ',', inner)  # collapse double commas
        return '[' + re.sub(r'\s+', ' ', inner).strip(', ') + ']'
    md = re.sub(r'\[(@[^\]]+)\]', _clean_citation, md)
    # 2d. Citation comma: [12 Theorem 4.1] → [12, Theorem 4.1]
    md = re.sub(r'\[(\d+)\s+([A-Za-z])', r'[\1, \2', md)

    # 2. Strip pandoc label/anchor constructs not supported by Obsidian
    # []{#label}, []{#label label="..."}, <span ...></span>
    md = re.sub(r'\[\]\{[^}]*\}', '', md)
    md = re.sub(r'<span[^>]*></span>', '', md)
    # Strip {#id} attributes from headings: ## Heading {#s:intro} → ## Heading
    md = re.sub(r'^(#{1,6}.*?)\s*\{#[^}]*\}\s*$', r'\1', md, flags=re.MULTILINE)

    # 3. Strip cross-reference HTML links, keeping inner text: <a href="..." ...>text</a>
    md = re.sub(r'<a\s[^>]*>(.*?)</a>', r'\1', md, flags=re.DOTALL)
    # Strip pandoc link attribute blocks: [text](url){reference-type="..." ...} → [text](url)
    md = re.sub(r'(\[[^\]]*\]\([^)]*\))\{[^}]*reference-type[^}]*\}', r'\1', md)
    # Strip bare {reference-type="..."} attribute blocks on plain text
    md = re.sub(r'\{[^}]*reference-type[^}]*\}', '', md)

    # 4. Convert <figure> blocks to markdown images with captions
    def _figure_to_md(m):
        block = m.group(0)
        src_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block)
        cap_m = re.search(r'<figcaption>(.*?)</figcaption>', block, re.DOTALL)
        if not src_m:
            return ''
        src = src_m.group(1)
        caption = re.sub(r'<[^>]+>', '', cap_m.group(1)).strip() if cap_m else ''
        return f'\n![{caption}]({src})\n'
    md = re.sub(r'<figure[^>]*>.*?</figure>', _figure_to_md, md, flags=re.DOTALL)

    # 5. Clean and isolate $$...$$ display math blocks
    # Multi-line environments need an aligned/gathered wrapper to render in Obsidian MathJax.
    # equation: strip (single-line, no alignment markers). Others: remap to aligned equivalent.
    _MATH_ENV_REMAP = {
        'align': 'aligned', 'eqnarray': 'aligned', 'flalign': 'aligned', 'alignat': 'aligned',
        'gather': 'gathered', 'multline': 'aligned',
    }
    def _isolate_display_math(m):
        inner = m.group(1)
        inner = re.sub(r'\\begin\{equation\}', '', inner)
        inner = re.sub(r'\\end\{equation\}', '', inner)
        for env, repl in _MATH_ENV_REMAP.items():
            inner = re.sub(r'\\begin\{' + env + r'\}', r'\\begin{' + repl + '}', inner)
            inner = re.sub(r'\\end\{' + env + r'\}', r'\\end{' + repl + '}', inner)
        inner = re.sub(r'\\nonumber\b\s*', '', inner)  # not needed without numbering
        inner = re.sub(r'\\label\{[^}]*\}', '', inner)
        inner = re.sub(r'^\s*\\quad\s*', '', inner, flags=re.MULTILINE)
        return f'\n$$\n{inner.strip()}\n$$\n'
    md = re.sub(r'\$\$(.+?)\$\$', _isolate_display_math, md, flags=re.DOTALL)

    # 5b. Unwrap ::: fenced divs that have only an ID (e.g. table/figure wrappers).
    # These have no class so _fenced_div_to_bold won't match them; just strip the delimiters.
    md = re.sub(r'^:{3,}\s*\{#[^}]+\}\s*\n(.*?)^:{3,}\s*$',
                lambda m: m.group(1).rstrip('\n'), md, flags=re.MULTILINE | re.DOTALL)

    # 5c. Convert pandoc ::: fenced divs (theorem environments) to bold-label format.
    # Done after display math isolation so $$-blocks inside theorem environments render correctly.
    # For proof: content already starts with "*Proof.*" — just unwrap.
    # For all others: prepend bold label.
    _DIV_LABELS = {
        'theorem': 'Theorem', 'lemma': 'Lemma', 'corollary': 'Corollary',
        'proposition': 'Proposition', 'definition': 'Definition', 'remark': 'Remark',
        'example': 'Example', 'conjecture': 'Conjecture', 'proof': None,
    }
    def _fenced_div_to_bold(m):
        class_name = (m.group(1) or m.group(2) or '').strip().lower()
        content_inner = m.group(3).strip()
        # Special case: bibliography — emit as # References with [N] numbered items
        if class_name == 'thebibliography':
            body = re.sub(r'^\d+\s*\n+', '', content_inner)  # strip leading arg (e.g. "10")
            paras = [p.strip() for p in re.split(r'\n\n+', body) if p.strip()]
            numbered = '\n\n'.join(f'[{i + 1}] {p}' for i, p in enumerate(paras))
            return '# References\n\n' + numbered
        label = _DIV_LABELS.get(class_name, class_name.title())
        if label is None:  # proof — content already begins with "*Proof.*"
            return content_inner
        # Extract injected \textbf{N} theorem number if present (no brackets)
        num_m = re.match(r'\*\*(\d+(?:\.\d+)*)\*\*\s*', content_inner)
        if num_m:
            num = num_m.group(1)
            content_inner = content_inner[num_m.end():]
            return f'**{label} {num}.** {content_inner}'
        return f'**{label}.** {content_inner}'
    md = re.sub(
        r'^:{3,}\s*(?:\{[^}]*\.(\w+)[^}]*\}|(\w+))\s*\n(.*?)^:{3,}\s*$',
        _fenced_div_to_bold, md, flags=re.MULTILINE | re.DOTALL)

    # 6. Fix specific KaTeX-incompatible commands (from markxiv)
    md = re.sub(r'(\\mathcal\{[^}]*\})\{', r'\1_{', md)
    md = re.sub(r'\\textsc\{([^}]*)\}', r'\\textbf{\1}', md)
    md = re.sub(r'\\mathbbm\{([^}]*)\}', r'\\mathbb{\1}', md)

    # 7. Strip remaining HTML tags (preserve math blocks verbatim)
    md = _strip_html_preserve_math(md)

    # 8. Convert pandoc simple tables to pipe tables for reliable Obsidian rendering
    md = _convert_simple_tables(md)

    # 8.5. Re-number table captions (pandoc drops LaTeX counter; `: text` follows tables)
    def _renumber_captions(md):
        table_n = 0
        lines = md.split('\n')
        result = []
        for line in lines:
            m = re.match(r'^:\s+(.+)', line)
            if m:
                j = len(result) - 1
                while j >= 0 and not result[j].strip():
                    j -= 1
                if j >= 0 and result[j].lstrip().startswith('|'):
                    table_n += 1
                    result.append(f'Table {table_n}: {m.group(1)}')
                    continue
            result.append(line)
        return '\n'.join(result)

    md = _renumber_captions(md)

    # 9. Collapse runs of 3+ blank lines to 2
    md = re.sub(r'\n{3,}', '\n\n', md)

    return md

def _extract_paper_keywords(content):
    """Extract and remove a keywords section from paper content.
    Returns (cleaned_content, keywords_list).

    Handles:
    - Heading style:  ## Keywords\\n\\nword1\\n\\nword2  (keywords one per paragraph)
    - Inline label:   Keywords: word1, word2            (plain, bold **, or italic *)
    The label may or may not have a trailing colon, and may or may not be wrapped in
    * or ** markers. Everything up to end-of-line is taken as the keyword list.
    """
    keywords = []

    # 1. Heading style: ## Keywords / ### Key Words (optional colon after label)
    kw_m = re.search(
        r'^#{1,4}\s+[Kk]ey\s*[Ww]ords?:?\s*\n+(.*?)(?=\n#{1,4}\s|\Z)',
        content, re.DOTALL | re.MULTILINE
    )
    if kw_m:
        kw_text = kw_m.group(1).strip()
        # Keywords may be comma-separated on one line, or one per paragraph
        for kw in re.split(r'[,;·•\n]+', kw_text):
            kw = re.sub(r'\*+', '', kw).strip()
            if kw and len(kw) < 60:
                keywords.append(kw)
    else:
        # 2. Inline label: plain / *italic* / **bold** — colon inside or after markers optional
        #    e.g. "Keywords: ..."  "*Key words:* ..."  "**Keywords** ..."
        kw_m = re.search(
            r'^\*{0,2}[Kk]ey\s*[Ww]ords?:?\*{0,2}:?\s+([^\n]+)',
            content, re.MULTILINE
        )
        if kw_m:
            kw_text = kw_m.group(1).strip()
            for kw in re.split(r'[,;·•]', kw_text):
                kw = re.sub(r'\*+', '', kw).strip()
                if kw and len(kw) < 60:
                    keywords.append(kw)

    if kw_m:
        content = content[:kw_m.start()] + content[kw_m.end():]
        content = re.sub(r'\n{3,}', '\n\n', content).strip()
    return content, keywords


def _fix_paper_structure(content, title=''):
    """Fix common structural issues in publisher-formatted (PDF-converted) papers:
    - Remove duplicate title headings
    - Remove publisher boilerplate lines (journal headers, copyright, ISSN, etc.)
    - Normalise abstract heading to ## Abstract and move it to the top
    Returns cleaned content."""
    # 1. Remove duplicate title headings (any level, case-insensitive)
    if title:
        title_norm = re.sub(r'\s+', ' ', title.strip().lower())
        def _drop_dup_title(m):
            heading_text = re.sub(r'\s+', ' ', m.group(1).strip().lower())
            return '' if heading_text == title_norm else m.group(0)
        content = re.sub(r'^#{1,4}\s+(.+?)\s*$', _drop_dup_title, content, flags=re.MULTILINE)

    # 2. Strip publisher boilerplate lines
    BOILERPLATE = re.compile(
        r'(?i)('
        r'journal\s+homepage|Available\s+online\s+at'
        r'|ISSN\s*[\d -]{4,}|©\s*\d{4}'
        r'|www\.\w[\w.]+/locate/\w'
        r'|Elsevier\s+B\.?V\.?|ScienceDirect'
        r')'
    )
    lines = content.split('\n')
    content = '\n'.join(l for l in lines if not BOILERPLATE.search(l))

    # 3. Normalise abstract heading and move it to the top of the content
    abs_m = re.search(
        r'^(#{1,4})\s+Abstract\s*\n+(.*?)(?=\n#{1,4}\s|\Z)',
        content, re.DOTALL | re.MULTILINE | re.IGNORECASE
    )
    if abs_m:
        abstract_body = abs_m.group(2).strip()
        abstract_section = f'## Abstract\n\n{abstract_body}'
        content = content[:abs_m.start()] + content[abs_m.end():]
        content = abstract_section + '\n\n' + content.lstrip('\n')

    return re.sub(r'\n{3,}', '\n\n', content).strip()


def _strip_html_preserve_math(text):
    """Strip HTML tags while leaving $...$ and $$...$$ content untouched."""
    result = []
    i = 0
    while i < len(text):
        # Preserve $$...$$
        if text[i:i+2] == '$$':
            end = text.find('$$', i + 2)
            if end == -1:
                result.append(text[i:])
                break
            result.append(text[i:end + 2])
            i = end + 2
        # Preserve $...$
        elif text[i] == '$':
            end = text.find('$', i + 1)
            if end == -1:
                result.append(text[i:])
                break
            result.append(text[i:end + 1])
            i = end + 1
        # Strip HTML tags
        elif text[i] == '<':
            end = text.find('>', i)
            if end == -1:
                result.append(text[i:])
                break
            i = end + 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)

def build_paper(url, project, created):
    """Fetch an academic paper and build an Obsidian note."""
    doi      = extract_doi(url)
    arxiv_id = extract_arxiv_id_from_url(url)
    if doi and not arxiv_id:
        arxiv_id = extract_arxiv_id_from_doi(doi)

    meta = get_paper_metadata(doi) if doi else {}

    if not arxiv_id and doi:
        print('Checking for arXiv preprint...', file=sys.stderr)
        arxiv_id = find_arxiv_id(doi)
        if arxiv_id:
            print(f'Found arXiv preprint: {arxiv_id}', file=sys.stderr)

    content     = ''
    images_dict = {}
    method      = ''
    bbl_key_map = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        if arxiv_id:
            if not shutil.which('pandoc'):
                print('pandoc not found — install with: brew install pandoc', file=sys.stderr)
                print('Falling back to PDF path...', file=sys.stderr)
                arxiv_id = None
            else:
                print(f'Downloading arXiv LaTeX source ({arxiv_id})...', file=sys.stderr)
                tex_path = get_arxiv_latex(arxiv_id, tmp_dir)
                if tex_path:
                    latex_meta = _metadata_from_latex(tex_path)
                    for k, v in latex_meta.items():
                        if v and not meta.get(k):
                            meta[k] = v
                    if doi:
                        meta['doi'] = doi
                    print('Converting LaTeX → Markdown via pandoc...', file=sys.stderr)
                    content = latex_to_markdown(tex_path, tmp_dir)
                    if content:
                        content = fix_paper_markdown(content)
                        method  = 'arxiv'
                        # Collect image files from the source dir before tmp_dir is cleaned up
                        import base64 as _b64mod
                        _IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
                        src_dir = os.path.dirname(tex_path)
                        for _fname in os.listdir(src_dir):
                            if os.path.splitext(_fname)[1].lower() in _IMG_EXT:
                                try:
                                    with open(os.path.join(src_dir, _fname), 'rb') as _f:
                                        images_dict[_fname] = _b64mod.b64encode(_f.read()).decode()
                                except Exception:
                                    pass
                        # Build citation key → number map from .bbl if present
                        bbl_key_map = {}
                        for _fname in os.listdir(src_dir):
                            if _fname.endswith('.bbl'):
                                try:
                                    with open(os.path.join(src_dir, _fname), encoding='utf-8', errors='replace') as _f:
                                        _bbl = _f.read()
                                    _keys = re.findall(r'\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}', _bbl)
                                    bbl_key_map = {k: i + 1 for i, k in enumerate(_keys)}
                                except Exception:
                                    pass
                                break

        if not content:
            pdf_path = None
            if arxiv_id:
                print(f'Trying arXiv PDF ({arxiv_id})...', file=sys.stderr)
                arxiv_pdf_url = f'https://arxiv.org/pdf/{arxiv_id}'
                arxiv_pdf_path = os.path.join(tmp_dir, 'arxiv.pdf')
                result = subprocess.run(
                    ['curl', '-sL', '--max-time', '30', '-o', arxiv_pdf_path,
                     '-w', '%{content_type}', arxiv_pdf_url],
                    capture_output=True, text=True, timeout=35
                )
                if 'pdf' in result.stdout.lower() and \
                        os.path.exists(arxiv_pdf_path) and os.path.getsize(arxiv_pdf_path) > 1000:
                    pdf_path = arxiv_pdf_path
            if not pdf_path and doi:
                print('Trying direct PDF download...', file=sys.stderr)
                pdf_path = fetch_doi_pdf(doi, tmp_dir)
            if not pdf_path and doi:
                if SHADOW_BASE_URL:
                    print(f'Trying shadow library ({SHADOW_BASE_URL.rstrip("/")})...', file=sys.stderr)
                    pdf_path = fetch_shadow_pdf(doi, tmp_dir)
                else:
                    print('Paper is not publicly accessible and no shadow library is configured (set SHADOW_BASE_URL).', file=sys.stderr)
            if not pdf_path:
                print(
                    'Could not retrieve PDF. Please provide a direct link to the PDF.',
                    file=sys.stderr
                )
                sys.exit(1)
            content, images_dict = pdf_to_markdown(pdf_path)
            if content:
                content = fix_paper_markdown(content)
                method  = 'datalab'

    if not content:
        print('Failed to extract paper content.', file=sys.stderr)
        sys.exit(1)

    # Strip leading blank lines and empty blockquotes.
    # For the LaTeX/arXiv path the title lives in pandoc's YAML frontmatter (already
    # stripped); the first H1 is the real first section — do NOT remove it.
    # For the PDF/Datalab path the title appears as a duplicate H1 — strip it.
    lines = content.split('\n')
    while lines and (not lines[0].strip() or re.match(r'^>\s*$', lines[0])):
        lines.pop(0)
    if method != 'arxiv' and lines and re.match(r'^#\s+', lines[0]):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    content = '\n'.join(lines).strip()

    # If the LaTeX source provided an abstract (Elsevier frontmatter etc.), prepend it
    abstract_text = meta.get('abstract', '')
    if abstract_text and not re.search(r'#{1,4}\s+Abstract\b', content, re.IGNORECASE):
        content = f'## Abstract\n\n{abstract_text}\n\n' + content
    # Normalise abstract heading: "--- Abstract" → "Abstract"
    content = re.sub(r'^(#{1,4})\s*---\s*Abstract', r'\1 Abstract', content,
                     flags=re.MULTILINE | re.IGNORECASE)
    # Fix reference list items: "- [N] " → "[N] " (prevents Obsidian checkbox rendering)
    content = re.sub(r'^- (\[\d+\] )', r'\1', content, flags=re.MULTILINE)

    # Fix paper structure: remove duplicate titles, publisher boilerplate, move abstract to top
    content = _fix_paper_structure(content, meta.get('title', ''))
    # Extract keywords from content (removed from body, added as tags below)
    content, paper_keywords = _extract_paper_keywords(content)

    # Extract abstract text for Gemini context
    abstract_m = re.search(
        r'#{1,4}\s+Abstract\s*\n+(.*?)(?=\n#{1,4}\s|\Z)', content,
        re.DOTALL | re.IGNORECASE
    )
    gemini_context = abstract_m.group(1).strip() if abstract_m else ' '.join(content.split()[:800])

    title   = meta.get('title', '')
    authors = meta.get('authors', [])
    ai_desc, _, ai_tags, _ = ai_enrich(title, ', '.join(authors[:3]), gemini_context)

    # Replace citation keys with numbers from .bbl (e.g. [GWW] → [4], [Wu11 Thm 1] → [16, Thm 1])
    if bbl_key_map:
        _keys_pat = '|'.join(re.escape(k) for k in sorted(bbl_key_map, key=len, reverse=True))
        def _replace_cite(m):
            key, rest = m.group(1), m.group(2) or ''
            num = bbl_key_map.get(key)
            return f'[{num}{rest}]' if num else m.group(0)
        content = re.sub(r'\[(' + _keys_pat + r')([\s,][^\]]+)?\]', _replace_cite, content)

    # Build TOC from headings after abstract only, insert before first post-abstract section
    abstract_end_m = re.search(r'#{1,4}\s+Abstract\b[^\n]*\n', content, re.IGNORECASE)
    toc_source = content[abstract_end_m.end():] if abstract_end_m else content
    toc = build_index(toc_source, min_level=1)
    if toc:
        next_sec = re.search(r'\n(#{1,4}\s+(?!Abstract\b))', content, re.IGNORECASE)
        if next_sec:
            ins = next_sec.start(1)
            content = content[:ins] + f'## Contents\n\n{toc}\n\n' + content[ins:]

    published = meta.get('published', '')
    journal   = meta.get('journal', '')
    doi_str   = meta.get('doi', doi or '')
    words     = len(re.findall(r'\S+', content))

    kw_tags = []
    for kw in paper_keywords:
        kw_slug = re.sub(r'[^a-z0-9]+', '-', kw.lower()).strip('-')
        if kw_slug:
            kw_tags.append(kw_slug)

    author_lines = ([f'author:'] + [f'  - {yaml_str(a)}' for a in authors]) if authors else []
    fm = build_frontmatter('paper', project, created, list(ai_tags) + kw_tags, [
        frontmatter_field('title', title),
        f'description: {yaml_str(ai_desc)}' if ai_desc else None,
        f'url: {url}',
        f'doi: {doi_str}' if doi_str else None,
        *author_lines,
        frontmatter_field('published', published),
        frontmatter_field('publication', journal),
        frontmatter_field('method', method),
        f'words: {words}',
    ])

    heading = f'# {title}' if title else ''
    parts   = [fm] + ([heading] if heading else []) + [content]
    return '\n\n'.join(parts), images_dict, meta


def title_from_content(content):
    """Extract title from first # or ## heading, or return empty string."""
    for prefix in (r'^# ', r'^## '):
        m = re.search(prefix + r'(.+)$', content, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ''


def build_article(d, url, project, created, method=None):
    title     = strip_title_suffix(d.get('title', '') or title_from_content(d.get('content', '')))
    author    = d.get('author', '')
    published = (d.get('published') or '')[:10]
    site      = d.get('site', '')
    words     = d.get('wordCount')
    content   = d.get('content', '')
    # Strip leading heading from content if it duplicates the title (or its first component)
    if title:
        first_component = re.split(r'\s+[-—|]\s+', title)[0].strip()
        candidates = [title] + ([first_component] if first_component != title else [])
        for candidate in candidates:
            new_content = re.sub(r'^\s*#{1,2}\s+' + re.escape(candidate) + r'\s*\n?', '', content).lstrip()
            if new_content != content:
                content = new_content
                break

    # Gemini enrichment
    ai_desc, ai_summary, ai_tags, _ = ai_enrich(title, author, content)
    ai_tags_clean = [t.lower() for t in ai_tags]

    # Build index from article headings
    index = build_index(content)

    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter('doc', project, created, ai_tags_clean, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_field('url', url),
        *([f'author:\n  - {yaml_str(author)}'] if author else []),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('method', method),
        frontmatter_field('words', words, quote=False) if words else None,
    ])
    summary = ai_summary if ai_summary else '[Summary to be added]'

    index_section = f'\n## Index\n\n{index}\n\n---\n' if index else '\n---\n'

    return f"""{fm}

---

# {title}

## Summary

{summary}
{index_section}
{content}
"""


def get_vault_path():
    """Return vault path: VAULT_PATH setting > projects.yaml > ~/Documents/Obsidian."""
    if VAULT_PATH:
        return os.path.expanduser(VAULT_PATH)
    try:
        content = open(os.path.expanduser('~/.claude/projects.yaml')).read()
        m = re.search(r'path:\s*(.+)', content)
        return m.group(1).strip() if m else os.path.expanduser('~/Documents/Obsidian')
    except Exception:
        return os.path.expanduser('~/Documents/Obsidian')


def get_save_dir(vault, category, project, subdir):
    """Build save directory path. category is optional."""
    parts = [vault] + ([category] if category else []) + [project, subdir]
    return os.path.join(*parts)


def format_saved_path(category, project, subdir, filename):
    """Format the 'Saved to ...' confirmation string."""
    parts = ([category] if category else []) + [project, subdir, filename]
    return '/'.join(parts)


def generate_filename(title, word_count='2-3'):
    """Generate a kebab-case filename from a title using the configured AI provider.
    Falls back to filtering title words by length if the AI call fails."""
    import urllib.request as _urlreq

    ai_prompt = f'Give me a {word_count} word kebab-case filename (no extension) for an article titled "{title}". Return ONLY the filename, nothing else.'

    def _clean(raw):
        name = raw.strip().lower().splitlines()[0]
        return re.sub(r'[^a-z0-9]+', '-', name).strip('-')

    if title and (AI_API_KEY or AI_LLM == 'ollama'):
        try:
            if AI_LLM == 'gemini':
                url  = f'https://generativelanguage.googleapis.com/v1beta/models/{AI_MODEL}:generateContent?key={AI_API_KEY}'
                body = json.dumps({'contents': [{'parts': [{'text': ai_prompt}]}]}).encode()
                req  = _urlreq.Request(url, data=body, headers={'Content-Type': 'application/json'})
                with _urlreq.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                raw = data['candidates'][0]['content']['parts'][0]['text']
            elif AI_LLM == 'openai':
                url  = (AI_BASE_URL.rstrip('/') + '/chat/completions') if AI_BASE_URL else 'https://api.openai.com/v1/chat/completions'
                body = json.dumps({'model': AI_MODEL, 'messages': [{'role': 'user', 'content': ai_prompt}]}).encode()
                req  = _urlreq.Request(url, data=body, headers={
                    'Content-Type': 'application/json', 'Authorization': f'Bearer {AI_API_KEY}'})
                with _urlreq.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                raw = data['choices'][0]['message']['content']
            elif AI_LLM == 'ollama':
                url  = 'http://localhost:11434/api/generate'
                body = json.dumps({'model': AI_MODEL, 'prompt': ai_prompt, 'stream': False}).encode()
                req  = _urlreq.Request(url, data=body, headers={'Content-Type': 'application/json'})
                with _urlreq.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                raw = data['response']
            else:
                raw = ''
            name = _clean(raw)
            if name:
                return name
        except Exception:
            pass

    # Fallback: try Claude CLI
    if title and shutil.which('claude'):
        try:
            result = subprocess.run(
                ['claude', '-p', ai_prompt, '--output-format', 'text'],
                capture_output=True, text=True, timeout=30
            )
            name = _clean(result.stdout)
            if name:
                return name
        except Exception:
            pass

    # Fallback: filter words by decreasing minimum length until we get 3
    words = re.findall(r'[a-zA-Z0-9]+', title)
    for min_len in (4, 3, 2, 0):
        filtered = [w.lower() for w in words if len(w) > min_len]
        if len(filtered) >= 3:
            return '-'.join(filtered[:3])

    return '-'.join(w.lower() for w in words[:3]) if words else 'untitled'


def fetch_defuddle(url):
    """Fetch a URL via defuddle CLI and return parsed JSON dict, or {} on failure."""
    result = subprocess.run(
        ['defuddle', 'parse', url, '--json', '--md'],
        capture_output=True, text=True, timeout=60
    )
    try:
        return json.loads(result.stdout)
    except Exception:
        return {}


def resolve_img_tags(html_str, base_url):
    """Convert <img> HTML tags to markdown image syntax with resolved absolute URLs.
    Call this before html_to_markdown so images survive the tag-stripping pass."""
    def _replace(m):
        src_m = re.search(r'src=["\']([^"\']+)["\']', m.group(0), re.IGNORECASE)
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', m.group(0), re.IGNORECASE)
        if not src_m:
            return ''
        src = src_m.group(1)
        if src.startswith('data:'):
            return ''
        if not src.startswith(('http://', 'https://')):
            src = urljoin(base_url, src)
        alt = alt_m.group(1).strip() if alt_m else ''
        return f'\n![{alt}]({src})\n'
    return re.sub(r'<img[^>]*/?>',  _replace, html_str, flags=re.IGNORECASE)


def _is_valid_image(path):
    """Check magic bytes to confirm the file is actually an image, not an HTML error page."""
    MAGIC = [
        b'\xff\xd8\xff',       # JPEG
        b'\x89PNG\r\n\x1a\n',  # PNG
        b'GIF8',               # GIF
        b'RIFF',               # WebP
    ]
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        ext = os.path.splitext(path)[1].lower()
        if ext == '.svg':
            return header.lstrip()[:1] in (b'<',)
        return any(header.startswith(m) for m in MAGIC)
    except Exception:
        return False


def _compress_image(path):
    """Compress a saved image in-place using pngquant (PNG) or jpegoptim (JPEG).
    Skips silently if the required tool is not installed."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.png':
        if shutil.which('pngquant'):
            subprocess.run(
                ['pngquant', '--force', '--ext', '.png', '--quality=65-80', '--skip-if-larger', '--strip', path],
                capture_output=True
            )
    elif ext == '.jpg':
        if shutil.which('jpegoptim'):
            subprocess.run(
                ['jpegoptim', '--max=85', '--strip-all', path],
                capture_output=True
            )


def download_images(content, docs_dir, filename_base, images_dict=None, source_url=None):
    """Save all images referenced in content to img/[filename_base]-NNN.ext.

    Handles two source types:
    - https:// URLs  → downloaded via curl
    - local filenames → decoded from base64 in images_dict (Datalab output)

    source_url: the original article URL, used as Referer on a retry when the
    first download fails image validation (e.g. CDN hotlink protection).

    Returns updated content with rewritten img/ paths.
    """
    import base64 as b64mod
    VALID_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.avif'}
    def _norm_ext(e): return '.jpg' if e == '.jpeg' else e
    pattern = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    if not pattern.search(content):
        return content

    img_dir = os.path.join(docs_dir, 'img')
    os.makedirs(img_dir, exist_ok=True)
    seen = {}   # original src → local_name (or None if failed)
    counter = [0]

    def replace_img(m):
        alt, src = m.group(1), m.group(2)
        if src.startswith('data:') or src.startswith('img/'):
            return m.group(0)
        if src in seen:
            return f'![{alt}](img/{seen[src]})' if seen[src] else m.group(0)

        counter[0] += 1
        local_name = None

        if src.startswith('http'):
            # Download from URL
            ext = _norm_ext(os.path.splitext(urlparse(src).path)[1].lower())
            if ext not in VALID_EXT:
                ext = '.png'
            local_name = f'{filename_base}-{counter[0]:03d}{ext}'
            local_path = os.path.join(img_dir, local_name)
            def _curl(extra_args=()):
                cmd = ['curl', '-sL', src, '-o', local_path] + list(extra_args)
                r = subprocess.run(cmd, capture_output=True, timeout=30)
                return (r.returncode == 0 and os.path.exists(local_path)
                        and os.path.getsize(local_path) > 0
                        and _is_valid_image(local_path))

            try:
                ok = _curl()
                if not ok and source_url:
                    if os.path.exists(local_path):
                        os.unlink(local_path)
                    ok = _curl([
                        '-H', f'Referer: {source_url}',
                        '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                    ])
                if not ok:
                    if os.path.exists(local_path):
                        os.unlink(local_path)
                    local_name = None
            except Exception:
                local_name = None
            if ENABLE_IMAGE_COMPRESSION and local_name:
                _compress_image(local_path)
        elif images_dict:
            # Decode from base64 dict (Datalab or arXiv)
            # Try exact key first, then basename (handles path-prefix mismatches)
            raw = images_dict.get(src) or images_dict.get(os.path.basename(src))
            if raw is not None:
                ext = _norm_ext(os.path.splitext(src)[1].lower())
                if ext not in VALID_EXT:
                    ext = '.png'
                local_name = f'{filename_base}-{counter[0]:03d}{ext}'
                try:
                    data = raw.split(',', 1)[1] if ',' in raw else raw
                    with open(os.path.join(img_dir, local_name), 'wb') as f:
                        f.write(b64mod.b64decode(data))
                except Exception:
                    local_name = None
                if ENABLE_IMAGE_COMPRESSION and local_name:
                    _compress_image(os.path.join(img_dir, local_name))

        seen[src] = local_name
        return f'![{alt}](img/{local_name})' if local_name else m.group(0)

    return pattern.sub(replace_img, content)


def html_to_markdown(html_str):
    """Convert HTML to rough markdown, preserving headings, code blocks, and paragraph breaks.

    If the HTML already contains raw markdown (detected via backtick fences), all HTML→markdown
    conversions are skipped — headings, lists, code blocks are already correct. Only HTML tags
    are stripped and entities unescaped. This handles sites that embed pre-rendered markdown
    for bots (e.g. VitePress preload divs) and avoids duplicate/conflicting content.
    """
    # Always remove noise elements first (head strips <title> text, nav/header/footer strips chrome)
    html_str = re.sub(r'<(head|style|script|nav|header|footer)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.IGNORECASE)

    if '```' in html_str:
        # Content is already markdown — strip HTML tags only, preserving the markdown structure
        html_str = re.sub(r'<pre[^>]*>.*?</pre>', '', html_str, flags=re.DOTALL | re.IGNORECASE)
        html_str = re.sub(r'<[^>]+>', '', html_str)
    else:
        # Pure HTML — convert structure to markdown
        for n in range(1, 7):
            html_str = re.sub(
                rf'<h{n}[^>]*>(.*?)</h{n}>',
                lambda m, n=n: '\n\n' + '#' * n + ' ' + re.sub(r'<[^>]+>', '', m.group(1)).strip() + '\n',
                html_str, flags=re.IGNORECASE | re.DOTALL
            )
        html_str = re.sub(
            r'<pre[^>]*>(?:<code[^>]*>)?(.*?)(?:</code>)?</pre>',
            lambda m: '\n\n```\n' + html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip() + '\n```\n',
            html_str, flags=re.DOTALL | re.IGNORECASE
        )
        html_str = re.sub(
            r'<code[^>]*>(.*?)</code>',
            lambda m: '`' + re.sub(r'<[^>]+>', '', m.group(1)) + '`',
            html_str, flags=re.DOTALL | re.IGNORECASE
        )
        html_str = re.sub(r'</?(?:p|br|li|div|tr|blockquote)[^>]*/?>',
                          '\n', html_str, flags=re.IGNORECASE)
        html_str = re.sub(r'<[^>]+>', '', html_str)

    html_str = html.unescape(html_str)
    # Collapse whitespace outside code fences only (preserve indentation inside)
    lines = html_str.split('\n')
    in_fence = False
    cleaned = []
    for line in lines:
        if line.startswith('```'):
            in_fence = not in_fence
            cleaned.append(line)
        elif in_fence:
            cleaned.append(line)
        else:
            cleaned.append(re.sub(r'[ \t]+', ' ', line))
    html_str = '\n'.join(cleaned)
    html_str = re.sub(r'\n{3,}', '\n\n', html_str)
    return html_str.strip()


def fetch_html_as_markdown(url, original_url=None, user_agent=None):
    """Fetch URL HTML, convert to markdown. Returns defuddle-style dict or None.
    original_url is used for site/metadata if fetching an archive URL."""
    try:
        cmd = ['curl', '-sL', url]
        if user_agent:
            cmd += ['-A', user_agent]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if not result.stdout:
            return None
        raw = result.stdout.decode('utf-8', errors='replace')
        raw = resolve_img_tags(raw, original_url or url)
        # Prefer <title> (browser tab, more descriptive) over <h1> (often too generic)
        title_m = re.search(r'<title[^>]*>(.*?)</title>', raw, re.IGNORECASE | re.DOTALL)
        title = html.unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip() if title_m else ''
        if not title:
            h1_m = re.search(r'<h1[^>]*>(.*?)</h1>', raw, re.IGNORECASE | re.DOTALL)
            if h1_m:
                title = html.unescape(re.sub(r'<[^>]+>', '', h1_m.group(1))).strip()
        content = html_to_markdown(raw)
        netloc = urlparse(original_url or url).netloc
        return {
            'content': content,
            'wordCount': len(content.split()),
            'title': title,
            'site': re.sub(r'^www\.', '', netloc),
            'author': '',
            'published': '',
        }
    except Exception:
        return None


def resolve_wikilink_images(content, url):
    """Replace Obsidian ![[image.ext]] wikilinks with real URLs.
    Fetches the page with a browser UA to extract <img> src attributes and map them by filename."""
    wikilink_pattern = re.compile(r'!\[\[([^\]]+\.(?:png|jpg|jpeg|gif|webp|svg|avif))\]\]', re.IGNORECASE)
    if not wikilink_pattern.search(content):
        return content
    try:
        result = subprocess.run(
            ['curl', '-sL', url, '-A',
             'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'],
            capture_output=True, timeout=30
        )
        html_str = result.stdout.decode('utf-8', errors='replace')
        img_map = {}
        for img_m in re.finditer(r'<img[^>]*/?>',  html_str, re.IGNORECASE):
            src_m = re.search(r'src=["\']([^"\']+)["\']', img_m.group(0), re.IGNORECASE)
            if src_m:
                src = src_m.group(1)
                if src.startswith('data:'):
                    continue
                if not src.startswith(('http://', 'https://')):
                    src = urljoin(url, src)
                filename = os.path.basename(urlparse(src).path).lower()
                if filename:
                    img_map[filename] = src
        def _replace(m):
            filename = m.group(1)
            resolved = img_map.get(filename.lower())
            return f'![{filename}]({resolved})' if resolved else m.group(0)
        return wikilink_pattern.sub(_replace, content)
    except Exception:
        return content


def fetch_googlebot(url):
    """Fetch page as Googlebot, strip scripts, convert to markdown."""
    d = fetch_html_as_markdown(url, user_agent='Googlebot/2.1 (+http://www.google.com/bot.html)')
    if d:
        d = dict(d)
        d['content'] = resolve_wikilink_images(d['content'], url)
        d['wordCount'] = len(d['content'].split())
    return d


def fetch_direct_md(url):
    """Look for a direct link to the page's raw .md source in the HTML.
    If found, fetches it and resolves wikilink images using the same base path.
    Returns a defuddle-style dict or None."""
    try:
        result = subprocess.run(['curl', '-sL', url], capture_output=True, timeout=30)
        raw = result.stdout.decode('utf-8', errors='replace')

        # Find a .md file URL in the HTML — must be on the same domain as the original URL
        # (prevents picking up random GitHub/CDN markdown files from paywall redirect pages)
        original_netloc = urlparse(url).netloc
        md_m = re.search(r'https?://[^\s"\'<>&]+/[^\s"\'<>&/?#]+\.md(?=[?#"\'<>\s]|$)', raw)
        if not md_m or urlparse(md_m.group(0)).netloc != original_netloc:
            return None
        md_url = md_m.group(0)

        # Fetch the raw .md file
        md_result = subprocess.run(['curl', '-sL', md_url], capture_output=True, timeout=30)
        content = md_result.stdout.decode('utf-8', errors='replace').strip()
        if not content or len(content.split()) < MIN_WORDS:
            return None

        # Resolve ![[image.ext]] wikilinks using base path + Assets/
        base_m = re.match(r'(https?://[^/]+/[^/]+/[a-f0-9]+/)', md_url)
        if base_m:
            assets_base = base_m.group(1) + 'Assets/'
            content = re.sub(
                r'!\[\[([^\]]+\.(?:png|jpg|jpeg|gif|webp|svg|avif))\]\]',
                lambda m: f'![{m.group(1)}]({assets_base}{quote(m.group(1), safe="")})',
                content, flags=re.IGNORECASE
            )

        # Extract title from first # heading
        title_m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else ''
        # Strip the leading # heading from content (build_article re-adds it)
        if title_m:
            content = content[title_m.end():].lstrip()

        netloc = urlparse(url).netloc
        return {
            'content': content,
            'wordCount': len(content.split()),
            'title': title,
            'site': re.sub(r'^www\.', '', netloc),
            'author': '',
            'published': '',
        }
    except Exception:
        return None


def wayback_snapshot(url):
    """Return the most recent Wayback Machine snapshot URL, or None."""
    try:
        api = f'https://archive.org/wayback/available?url={quote(url, safe="")}'
        result = subprocess.run(['curl', '-s', api], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        snapshot = data.get('archived_snapshots', {}).get('closest', {})
        if snapshot.get('available'):
            return snapshot['url']
    except Exception:
        pass
    return None


def clean_archive_content(d, original_url):
    """Remove archive.is artifacts and restore original metadata."""
    content = d.get('content', '')
    # Remove leading archive.is header table
    content = re.sub(r'^<table>.*?</table>\s*', '', content, flags=re.DOTALL)
    # Strip archive.is URL prefix: https://archive.is/o/ID/https://... → https://...
    content = re.sub(r'https://archive\.is/o/[A-Za-z0-9]+/', '', content)
    # Remove archive.is scroll-position footer: [0%](url) [10%](url) ... [100%](url)
    content = re.sub(r'\n\[0%\].*', '', content, flags=re.DOTALL)
    d = dict(d)
    d['content'] = content.strip()
    # Restore site from original URL domain
    netloc = urlparse(original_url).netloc
    d['site'] = re.sub(r'^www\.', '', netloc)
    # Clear author/published — archive.is values are unreliable
    if 'archive' in (d.get('author') or '').lower():
        d['author'] = ''
    d['published'] = ''
    return d


def archive_is_snapshot(url):
    """Return the most recent archive.is snapshot URL if one exists, or None."""
    try:
        result = subprocess.run(
            ['curl', '-sI', f'https://archive.is/newest/{url}'],
            capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.split('\n'):
            if line.lower().startswith('location:'):
                loc = line.split(':', 1)[1].strip()
                # Matches both https://archive.is/AbCdE and https://archive.is/20260101120000/https://...
                if re.match(r'https://archive\.is/[A-Za-z0-9]', loc):
                    return loc
    except Exception:
        pass
    return None


def archive_is_save(url):
    """Submit URL to archive.is and return snapshot URL, or None."""
    try:
        result = subprocess.run(
            ['curl', '-s', '-D', '-', '-o', '/dev/null', '-X', 'POST',
             '-d', f'url={quote(url, safe="")}&anyway=1',
             'https://archive.is/submit/'],
            capture_output=True, text=True, timeout=90
        )
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.lower().startswith('location:'):
                loc = line.split(':', 1)[1].strip()
                if 'archive.is/' in loc:
                    return loc
            if line.lower().startswith('refresh:') and 'url=' in line.lower():
                return line.split('url=', 1)[1].strip()
    except Exception:
        pass
    return None


def fetch_for_method(url, method):
    """Fetch using a specific method. Returns (d, used_method)."""
    if method == 'native':
        # Prefer native-md (raw .md source) over native-page (defuddle parse)
        candidate = fetch_direct_md(url)
        if candidate and candidate.get('wordCount', 0) > MIN_WORDS:
            return candidate, 'native-md'
        return fetch_defuddle(url), 'native-page'
    elif method == 'native-md':
        return fetch_direct_md(url) or {}, 'native-md'
    elif method == 'native-page':
        return fetch_defuddle(url), 'native-page'
    elif method == 'googlebot':
        candidate = fetch_googlebot(url)
        if candidate:
            candidate = dict(candidate)
            candidate['site'] = re.sub(r'^www\.', '', urlparse(url).netloc)
        return candidate or {}, 'googlebot'
    elif method == 'wayback':
        archive_url = wayback_snapshot(url)
        if not archive_url:
            print(
                'No Wayback Machine snapshot found for this URL.\n'
                'Note: Wayback Machine does not support JS-rendered pages, so this method '
                'may not work even if a snapshot exists.',
                file=sys.stderr
            )
            return {}, 'wayback'
        return fetch_html_as_markdown(archive_url, original_url=url) or {}, 'wayback'
    elif method == 'archive-is':
        archive_url = archive_is_snapshot(url)
        if not archive_url:
            archive_url = archive_is_save(url)
        if archive_url:
            d = fetch_defuddle(archive_url)
            if d:
                d = clean_archive_content(d, url)
            return d or {}, 'archive-is'
        print(
            f'archive.is requires a captcha for this URL.\n'
            f'Please visit this URL, save the page, and solve the captcha:\n'
            f'https://archive.is/?url={quote(url, safe=":/?")}',
            file=sys.stderr
        )
        return {}, 'archive-is'
    else:
        return {}, method


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',      required=True)
    parser.add_argument('--project',  default=None)
    parser.add_argument('--category', default=None)
    parser.add_argument('--created',  default=None)
    parser.add_argument('--filename', default=None)
    parser.add_argument('--method',   default=None, choices=['native', 'native-md', 'native-page', 'googlebot', 'wayback', 'archive-is'])
    args = parser.parse_args()

    _is_special = (
        (ENABLE_PAPER   and is_doi_url(args.url)) or
        (ENABLE_PODCAST and is_apple_podcast(args.url)) or
        (ENABLE_YOUTUBE and is_youtube(args.url))
    )
    url = args.url if _is_special else clean_url(args.url)

    # Normalize DOI URLs to canonical https://doi.org/{doi} form (strip dx., www., etc.)
    if ENABLE_PAPER and is_doi_url(url) and 'arxiv.org' not in url:
        doi_part = extract_doi(url)
        if doi_part:
            url = f'https://doi.org/{doi_part}'

    # Academic papers: DOI / arXiv
    if ENABLE_PAPER and is_doi_url(url):
        if args.project is None:
            note, _, _meta = build_paper(url, None, args.created)
            print(re.sub(r'^---\n.*?\n---\n\n?', '', note, count=1, flags=re.DOTALL).strip())
            return
        note, images_dict, meta = build_paper(url, args.project, args.created)
        title    = meta.get('title', '')
        filename = args.filename or generate_filename(title, word_count='3-4')
        vault    = get_vault_path()
        papers_dir = get_save_dir(vault, args.category, args.project, 'papers')
        os.makedirs(papers_dir, exist_ok=True)
        if ENABLE_IMAGES:
            note = download_images(note, papers_dir, filename, images_dict=images_dict, source_url=url)
        note = _dedup_note_tags(note)
        out_path = os.path.join(papers_dir, f'{filename}.md')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(note)
        print(f'Saved to {format_saved_path(args.category, args.project, "papers", filename)}')
        return

    # Apple Podcasts: local TTML transcript, no HTTP cascade needed
    if ENABLE_PODCAST and is_apple_podcast(url):
        episode_id = apple_podcast_id(url)
        ttml_path  = find_podcast_ttml(episode_id) if episode_id else None
        if not ttml_path:
            deep_link = re.sub(r'^https://', 'podcasts://', url)
            print(
                f'Podcast transcript not found locally.\n'
                f'Open the episode in the Podcasts app and view its transcript:\n\n'
                f'  {deep_link}\n\n'
                f'Once the transcript has loaded, re-run this command.',
                file=sys.stderr
            )
            return
        if args.project is None:
            # Present mode: print transcript text
            for chunk in parse_ttml(ttml_path):
                speaker_label = re.sub(r'SPEAKER_(\d+)', r'Speaker \1', chunk['speaker'])
                print(f'[{fmt_tc(chunk["time"])}] **{speaker_label}:** {chunk["text"]}')
            return
        # Save mode
        # Canonical URL: keep only ?i= param
        clean_pod_url = re.sub(r'[?&]i=(\d+).*', r'?i=\1', url)
        note = build_apple_podcast(clean_pod_url, args.project, args.created, ttml_path)
        meta = get_podcast_metadata(episode_id, apple_show_id(url))
        show  = meta.get('show', '')
        title = meta.get('title', '')
        # Strip words containing digits (e.g. "S10E5", "256") from episode title
        cleaned_title = ' '.join(w for w in title.split() if not re.search(r'\d', w)).strip()
        combined = f'{show}: {cleaned_title}' if show and cleaned_title else show or cleaned_title or title
        filename = args.filename or generate_filename(combined, word_count='3-4')
        vault    = get_vault_path()
        transcripts_dir = get_save_dir(vault, args.category, args.project, 'transcripts')
        os.makedirs(transcripts_dir, exist_ok=True)
        if ENABLE_IMAGES:
            note = download_images(note, transcripts_dir, filename, source_url=url)
        note = _dedup_note_tags(note)
        out_path = os.path.join(transcripts_dir, f'{filename}.md')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(note)
        print(f'Saved to {format_saved_path(args.category, args.project, "transcripts", filename)}')
        return

    if args.method:
        d, used_method = fetch_for_method(url, args.method)
    else:
        # Auto-cascade: native-md → native-page → googlebot → wayback → archive-is
        d, used_method = {}, None

        # 1. native-md: direct .md source (e.g. Obsidian Publish)
        candidate = fetch_direct_md(url)
        if candidate and candidate.get('wordCount', 0) > MIN_WORDS:
            d, used_method = candidate, 'native-md'

        # 2. native-page: defuddle parse
        if not used_method:
            candidate = fetch_defuddle(url)
            if candidate.get('wordCount', 0) > MIN_WORDS:
                d, used_method = candidate, 'native-page'

        # 3. googlebot: Googlebot UA + script-strip
        if not used_method:
            candidate = fetch_googlebot(url)
            if candidate and candidate.get('wordCount', 0) > MIN_WORDS:
                candidate = dict(candidate)
                candidate['site'] = re.sub(r'^www\.', '', urlparse(url).netloc)
                d, used_method = candidate, 'googlebot'

        # 4. Archive fallbacks
        if not used_method:
            for get_archive_url, method_name in [
                (wayback_snapshot,    'wayback'),
                (archive_is_snapshot, 'archive-is'),
                (archive_is_save,     'archive-is'),
            ]:
                archive_url = get_archive_url(url)
                if archive_url:
                    if method_name == 'wayback':
                        candidate = fetch_html_as_markdown(archive_url, original_url=url) or {}
                    else:
                        candidate = fetch_defuddle(archive_url)
                        if candidate.get('wordCount', 0) > MIN_WORDS and 'archive.is/' in archive_url:
                            candidate = clean_archive_content(candidate, url)
                    if candidate.get('wordCount', 0) > MIN_WORDS:
                        d, used_method = candidate, method_name
                        break

            if not used_method:
                print(
                    f'Page appears JS-rendered and no archive snapshot could be retrieved.\n'
                    f'Please visit this URL, save the page, and solve the captcha:\n'
                    f'https://archive.is/?url={quote(url, safe=":/?")}',
                    file=sys.stderr
                )

    # Present mode (no --project): output raw content
    if args.project is None:
        print(d.get('content', ''), end='')
        return

    # Don't save an empty note
    if d.get('wordCount', 0) <= MIN_WORDS:
        print('No content retrieved — skipping save.', file=sys.stderr)
        return

    # Save mode: build note, write to vault
    if ENABLE_YOUTUBE and is_youtube(url):
        note = build_youtube(d, url, args.project, args.created, method=used_method)
    else:
        note = build_article(d, url, args.project, args.created, method=used_method)

    title = d.get('title', '') or title_from_content(d.get('content', ''))
    filename = args.filename or generate_filename(title)

    vault   = get_vault_path()
    subdir  = 'transcripts' if is_youtube(url) else 'docs'
    save_dir = get_save_dir(vault, args.category, args.project, subdir)
    os.makedirs(save_dir, exist_ok=True)
    if ENABLE_IMAGES:
        note = download_images(note, save_dir, filename, source_url=url)
    note = _dedup_note_tags(note)
    out_path = os.path.join(save_dir, f'{filename}.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(note)
    print(f'Saved to {format_saved_path(args.category, args.project, subdir, filename)}')


if __name__ == '__main__':
    main()
