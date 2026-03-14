#!/usr/bin/env python3
"""
Formats defuddle JSON output into an Obsidian vault note.
Reads defuddle JSON from stdin, writes formatted markdown to stdout.

Usage:
    defuddle parse "<url>" --json --md | python3 defuddle.py \
        --url "<url>" --created "YYYY-MM-DD"
"""

import sys, json, re, argparse, subprocess, os, html, glob, io

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding='utf-8')


def _load_api_key(service):
    try:
        content = open(os.path.expanduser('~/.claude/CLAUDE.md')).read()
        m = re.search(rf'\*\*{service}\*\*:\s*`([^`]+)`', content, re.IGNORECASE)
        return m.group(1) if m else ''
    except Exception:
        return ''

GEMINI_API_KEY = _load_api_key('Gemini')


def yaml_str(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def frontmatter_field(key, value, quote=True):
    if value is None or value == '':
        return None
    val = yaml_str(value) if quote else str(value)
    return f'{key}: {val}'


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


def strip_timecode_lines(text):
    """Remove lines that start with a timecode (chapter index entries in descriptions)."""
    lines = [l for l in text.split('\n') if not re.match(r'^\s*\d+:\d{2}(?::\d{2})?', l)]
    return '\n'.join(lines).strip()


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
    """Insert ### chapter headings into transcript at the correct timecode positions."""
    if not chapters:
        return transcript_body

    # Parse and sort chapters by seconds
    chapter_list = sorted(
        [(timecode_to_seconds(ch['time']), ch['title']) for ch in chapters]
    )

    tc_pattern = re.compile(r'^\[?\*\*(\d+:\d{2}(?::\d{2})?)\*\*\]')
    lines = transcript_body.split('\n')
    result = []
    ch_idx = 0

    for line in lines:
        m = tc_pattern.match(line)
        if m:
            line_secs = timecode_to_seconds(m.group(1))
            while ch_idx < len(chapter_list) and line_secs >= chapter_list[ch_idx][0]:
                result.append(f'### {chapter_list[ch_idx][1]}')
                result.append('')
                ch_idx += 1
        result.append(line)

    # Append any remaining chapters (past the last timecode line)
    while ch_idx < len(chapter_list):
        result.append('')
        result.append(f'### {chapter_list[ch_idx][1]}')
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


def gemini_enrich(title, author, content, needs_chapters=False):
    """Call gemini for description, summary, tags, and optionally chapters."""
    chapters_field = ''
    chapters_rule = ''
    if needs_chapters:
        chapters_field = ',\n  "chapters": [{"time": "M:SS or H:MM:SS", "title": "..."}, ...]'
        chapters_rule = '- chapters: list of logical sections with their start timecode from the transcript (5-10 chapters)\n'

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

    try:
        env = {**os.environ, 'GEMINI_API_KEY': GEMINI_API_KEY}
        result = subprocess.run(
            ['gemini', '-m', 'gemini-3.1-flash-lite-preview', '-p', prompt],
            capture_output=True, text=True, timeout=120, env=env
        )
        raw = result.stdout.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        data = json.loads(raw)
        return (
            data.get('description', ''),
            data.get('summary', ''),
            data.get('tags', []),
            data.get('chapters', [])
        )
    except Exception:
        return '', '', [], []


def build_index(content):
    """Build a nested index from markdown headings (## to ####), indented relative to the shallowest level."""
    headings = re.findall(r'^(#{2,4})\s+(.+)$', content, re.MULTILINE)
    if not headings:
        return None
    min_level = min(len(h) for h, _ in headings)
    lines = []
    for hashes, title in headings:
        indent = '  ' * (len(hashes) - min_level)
        lines.append(f'{indent}- [[#{title}]]')
    return '\n'.join(lines)


def build_youtube(d, url, created):
    title     = d.get('title', '')
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
    ai_desc, ai_summary, ai_tags, ai_chapters = gemini_enrich(
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

    # Frontmatter
    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fields = [
        'tags:',
        '  - defuddle/video',
    ]
    for tag in all_tags:
        fields.append(f'  - {tag}')
    fields.append(f'created: {created}')
    for line in filter(None, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_field('url', short_url),
        frontmatter_field('author', author),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('duration', duration),
        frontmatter_field('words', words, quote=False) if words else None,
    ]):
        fields.append(line)

    fm = '---\n' + '\n'.join(fields) + '\n---'
    summary = ai_summary if ai_summary else '[Summary to be added]'

    return f"""{fm}

---

# {title}

## Summary

{summary}

## Description

![{title}]({short_url})

{desc}

## Contents

{contents}

---

## Transcript

{transcript_body}
"""


def build_article(d, url, created):
    title     = d.get('title', '')
    author    = d.get('author', '')
    published = (d.get('published') or '')[:10]
    site      = d.get('site', '')
    words     = d.get('wordCount')
    content   = d.get('content', '')

    # Gemini enrichment
    ai_desc, ai_summary, ai_tags, _ = gemini_enrich(title, author, content)
    ai_tags_clean = [t.lower() for t in ai_tags]

    # Build index from article headings
    index = build_index(content)

    # Frontmatter
    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fields = [
        'tags:',
        '  - defuddle/docs',
    ]
    for tag in ai_tags_clean:
        fields.append(f'  - {tag}')
    fields.append(f'created: {created}')
    for line in filter(None, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_field('url', url),
        frontmatter_field('author', author),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('words', words, quote=False) if words else None,
    ]):
        fields.append(line)

    fm = '---\n' + '\n'.join(fields) + '\n---'
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',     required=True)
    parser.add_argument('--created', required=True)
    args = parser.parse_args()

    d = json.load(sys.stdin)

    if is_youtube(args.url):
        print(build_youtube(d, args.url, args.created), end='')
    else:
        print(build_article(d, args.url, args.created), end='')


if __name__ == '__main__':
    main()
