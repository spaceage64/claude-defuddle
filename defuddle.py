#!/usr/bin/env python3
"""
Formats defuddle JSON output into an Obsidian vault note.
Reads defuddle JSON from stdin, writes formatted markdown to stdout.

Usage:
    defuddle parse "<url>" --json --md | python3 defuddle.py \
        --url "<url>" --project "<project>" --created "YYYY-MM-DD"
"""

import sys, json, re, argparse, subprocess, os, html, glob, shutil, shlex, tempfile, time, unicodedata, base64
from urllib.parse import urlparse, urlunparse, quote, unquote, urljoin
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

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
# ║  Save folder — notes are saved in {vault}/{project}/{SAVE_SUBFOLDER}/.       ║
# ║  '' sorts them by type instead: docs/, papers/ and transcripts/.             ║
SAVE_SUBFOLDER = 'defuddle'
# ║                                                                              ║
# ║  Source feature flags — set False to treat that type as a generic article    ║
ENABLE_YOUTUBE  = True
ENABLE_PODCAST  = True
ENABLE_PAPER    = True
ENABLE_PDF      = True   # Enables plain PDF URL support
# ║                                                                              ║
# ║  Shadow library — Optional shadow library base URL for paywalled papers      ║
# ║  (e.g. https://sci-hub.example). Set to '' to disable.                       ║
SHADOW_BASE_URL = ''
# ║  Downloads folder — watched for the PDF you save from the shadow library.    ║
# ║  '' = the system default (macOS, Windows, Linux XDG). Example: '~/Downloads' ║
DOWNLOADS_DIR = ''
# ║  Seconds to wait for that PDF before giving up.                              ║
SHADOW_WAIT   = 600
# ║                                                                              ║
# ║  Images — download and localise images into a /img subfolder.                ║
# ║  If False, images are left as external URLs in the note.                     ║
ENABLE_IMAGES            = True
# ║  Compression — requires: brew install pngquant jpegoptim                     ║
# ║  pngquant: lossy palette reduction (~60–80% smaller, similar to TinyPNG)     ║
# ║  jpegoptim: re-encodes only if quality > 85; strips metadata.                ║
ENABLE_IMAGE_COMPRESSION = True
# ║  Grayscale figures (with compression) — requires: uv. Grey images (scanned   ║
# ║  graphs, line art) become PNG with 8 grey levels, or 32 when 8 would show    ║
# ║  bands in smooth shading; photos and colour images are left alone.           ║
GRAYSCALE_FIGURES        = False
# ║  PDF compression (--keep-pdf) — requires: uv                                 ║
# ║  Images above 160 dpi go to 150 dpi; fonts are trimmed.                      ║
ENABLE_PDF_COMPRESSION   = True
# ║  Keep PDF — also save the original PDF of papers and PDFs next to the        ║
# ║  note (as with --keep-pdf; --no-keep-pdf skips it for one run).              ║
# ║  OCR — saved PDFs whose pages have no text layer (scans) get an invisible    ║
# ║  OCR text layer, so they can be searched and selected; the scan images stay  ║
# ║  as they are. Runs before compression. Requires: tesseract, uv.              ║
OCR_PDF = False
KEEP_PDF = False
# ║  Parallel jobs — links processed at the same time when several are given,    ║
# ║  and images downloaded at the same time for one note.                        ║
PARALLEL_JOBS = 4
IMAGE_JOBS    = 6
# ║                                                                              ║
# ║  Datalab — converts paper PDFs to markdown via cloud API.                    ║
# ║  Get key at https://datalab.to — free accounts get $10 in credits.           ║
# ║  Costs are about 10-15 cents per paper.                                      ║
# ║  Add to ~/.claude/CLAUDE.md:  **Datalab**: `your-api-key`                    ║
DATALAB_API_KEY = _apikey('Datalab')
# ║                                                                              ║
# ║  PDF processor — which method(s) to use when converting PDFs to markdown.    ║
# ║  Local uses marker-pdf (install with: pipx install marker-pdf).              ║
# ║    'local_first'   — local marker, fall back to Datalab if unavailable       ║
# ║    'datalab_first' — Datalab API, fall back to local marker if it fails      ║
# ║    'local'         — local marker only (ignores Datalab key)                 ║
# ║    'datalab'       — Datalab API only (no local fallback)                    ║
PDF_PROCESSOR = 'local_first'
# ║  MARKER_PRECISE: False = normal (quick, but inline math can break).          ║
# ║  True = precise: full-page OCR, inline math re-read by the LLM (slower).     ║
MARKER_PRECISE = False
# ║                                                                              ║
# ║  AI enrichment — provider for summaries, tags, chapters, and filenames.      ║
# ║  Always falls back to Claude CLI if the provider fails or has no key.        ║
# ║                                                                              ║
# ║  AI_LLM options:                                                             ║
# ║    'gemini' — Google Gemini API. Add to ~/.claude/CLAUDE.md:                 ║
# ║               **Gemini**: `your-api-key`                                     ║
# ║               Free key: https://aistudio.google.com/apikey                   ║
# ║               '-latest' aliases always point at Google's newest model:       ║
# ║               gemini-flash-lite-latest | gemini-flash-latest |               ║
# ║               gemini-pro-latest                                              ║
# ║    'openai' — Any OpenAI-compatible API. Add to ~/.claude/CLAUDE.md:         ║
# ║               **OpenAI**: `your-api-key`                                     ║
# ║               Set AI_BASE_URL for non-OpenAI endpoints, e.g.:                ║
# ║                 OpenCode Zen (free): https://zen.opencode.ai/v1              ║
# ║                 Groq:               https://api.groq.com/openai/v1           ║
# ║                 Together AI:        https://api.together.xyz/v1              ║
# ║               Leave AI_BASE_URL = '' for standard OpenAI.                    ║
# ║    'ollama' — Local Ollama server (no key needed). https://ollama.com        ║
# ║               Set AI_MODEL to any pulled model, e.g. llama3.2                ║
# ║  AI_MODEL can be a list: the next model is tried when one is overloaded      ║
# ║  or rate-limited (HTTP 429/5xx, timeout), before falling back to Claude.     ║
# ║  AI_OLDER_MODELS: for a Gemini '-latest' alias, also try this many older     ║
# ║  versions of it, newest first, found in Gemini's model list when needed.     ║
# ║  Older models are usually less busy than the newest.                         ║
AI_LLM      = 'gemini'
AI_MODEL    = 'gemini-flash-lite-latest'
AI_OLDER_MODELS = 2
AI_BASE_URL = ''
# ║                                                                              ║
# ║  Claude fallback model (used when AI provider fails or has no key).          ║
# ║  Set to '' to use Claude Code's default model.                               ║
# ║  Aliases always point at the newest model of that family:                    ║
# ║    haiku  — fast, cheap, good for structured output (default)                ║
# ║    sonnet — higher quality, slower and more expensive                        ║
AI_FALLBACK_MODEL = 'haiku'
# ║                                                                              ║
# ║  Minimum word count to consider a fetch attempt successful. Few words        ║
# ║  indicate and error and may trigger alternative processing methods.          ║
MIN_WORDS = 25
DUPLICATE_IMAGE_DIFF = 9   # mean pixel difference (0–255) below which neighbouring PDF images count as one
# ║                                                                              ║
# ║  Filename word count — maximum number of words the AI uses when generating   ║
# ║  a filename from a title. Papers prepend the first author's last name;       ║
# ║  podcasts prepend a 1–2 word show slug. Both respect this setting for the    ║
# ║  title portion.                                                              ║
FILENAME_WORDS = 3
# ╚══════════════════════════════════════════════════════════════════════════════╝

# Derived from AI_LLM — not user-configured.
AI_API_KEY = _apikey({'gemini': 'Gemini', 'openai': 'OpenAI'}.get(AI_LLM, ''))


def curl(url, *options, max_time=15, text=False, follow=True, capture=True):
    """Run curl -s on url (following redirects unless follow=False) with extra curl options,
    stopped after max_time seconds. Returns the CompletedProcess: stdout as bytes, or str with text=True."""
    return subprocess.run(['curl', '-sL' if follow else '-s', '--max-time', str(max_time), *options, url],
                          capture_output=capture, text=text, timeout=max_time + 5)


def yaml_str(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def frontmatter_field(key, value, quote=True):
    if value is None or value == '':
        return None
    val = yaml_str(value) if quote else str(value)
    return f'{key}: {val}'


def frontmatter_url(*urls):
    """url is always a YAML list (one or more sources), so the property has one type vault-wide."""
    urls = [u for u in urls if u]
    return ('url:\n' + '\n'.join(f'  - {yaml_str(u)}' for u in urls)) if urls else None


# Frontmatter 'method' says where the content came from, not which internal fetch route was used
_METHOD_LABELS = {'native': 'web', 'native-md': 'web', 'native-page': 'web', 'googlebot': 'web',
                  'wayback': 'archive', 'archive-is': 'archive'}


def method_field(method):
    """'method: defuddle-…' frontmatter line: web, archive, youtube, podcast, arxiv, marker or datalab."""
    return frontmatter_field('method', f'defuddle-{_METHOD_LABELS.get(method, method)}') if method else None


def _with_archive(d, archive_url):
    """Remember which archived copy the content came from (second 'url' value)."""
    return dict(d, archive_url=archive_url) if d else {}


def build_frontmatter(created, tags, extra_fields):
    """Build a YAML frontmatter block from common fields plus source-specific extras.

    extra_fields is a list of pre-formatted YAML lines (or None to skip).
    """
    fields = ['---'] + (['tags:'] + [f'  - {t}' for t in tags] if tags else [])
    if created:
        fields.append(f'created: {created}')
    for line in [x for x in extra_fields if x is not None]:
        fields.append(line)
    fields.append('---')
    return '\n'.join(fields)


_TRACKING_PARAM = re.compile(r'utm_\w+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|yclid|twclid|igshid|'
                             r'mc_cid|mc_eid|_hsenc|_hsmi|mkt_tok|ref_src|ref_url|share_source|si', re.I)


def clean_url(url):
    """Strip the fragment and tracking parameters (utm_*, fbclid, ...) from a URL. Other query
    parameters stay: many pages are chosen by one (e.g. trader.aspx?id=symboldirdefs)."""
    p = urlparse(url)
    query = '&'.join(kv for kv in p.query.split('&') if kv and not _TRACKING_PARAM.fullmatch(kv.split('=', 1)[0]))
    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, ''))


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
    return re.sub(r'^\*\*(\d+:\d{2}(?::\d{2})?)\*\*(?: ·)?', replace, text, flags=re.MULTILINE)


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


def parse_vtt(content, vid_id, interval=30, breaks=()):
    """Parse VTT content into a transcript_body string with paragraph-level timestamps.

    Auto-detects rolling-window captions (take last line per cue) vs standalone
    captions (join all lines per cue). interval groups cues into ~N-second paragraphs;
    breaks (seconds, e.g. chapter starts) always begin a new paragraph.
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
        # Chapter starts are whole seconds and a line often starts a split second early: round
        elif secs - group_start >= interval or any(group_start < b <= round(secs) for b in breaks):
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
        tc = fmt_tc(round(secs))
        link = f'[**{tc}**](https://youtu.be/{vid_id}?t={round(secs)})'
        out.append(f'{link} {text}')

    return '\n\n'.join(out)


def yt_info(url):
    """Fetch video metadata (title, channel, description, duration, chapters, ...) from yt-dlp.
    Returns the yt-dlp info dict, or {} on failure."""
    try:
        result = subprocess.run(
            ['yt-dlp', '--skip-download', '--dump-single-json', url],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(result.stdout)
    except Exception:
        return {}


def fetch_youtube(url):
    """Defuddle's parse of a YouTube page, with title/channel/date filled in from yt-dlp when
    defuddle returns nothing. Returns {} if neither could read the video."""
    d = fetch_defuddle(url)
    if not d.get('title'):
        info = yt_info(url)
        if info.get('title'):
            date = info.get('upload_date') or ''
            d = dict(d, title=info['title'], site='YouTube',
                     author=info.get('channel') or info.get('uploader', ''),
                     published=f'{date[:4]}-{date[4:6]}-{date[6:8]}' if len(date) == 8 else '')
    return d if d.get('title') else {}


NO_SUBTITLES = '*No subtitles available for this video.*'


def yt_duration(info):
    """Video length as M:SS or H:MM:SS, like the transcript timecodes."""
    return fmt_tc(info['duration']) if info.get('duration') else info.get('duration_string', '')


def youtube_transcript(content, url, vid_id, yt_chapters):
    """Transcript for a video: yt-dlp captions (with yt-dlp's chapters), else defuddle's own
    transcript. Returns (transcript_body, has_chapters); the body is '' if the video has no subtitles."""
    # Paragraphs break at chapter starts, so every chapter heading lands right before its own text
    breaks = [ch['start_time'] for ch in yt_chapters]
    vtt_transcript = fetch_vtt(url, vid_id, breaks) if vid_id else None
    if vtt_transcript:
        if not yt_chapters:
            return vtt_transcript, False  # Let Gemini generate chapters
        # Use accurate chapter timestamps from yt-dlp
        chapter_list = [{'time': fmt_tc(int(ch['start_time'])),
                         'title': 'Introduction' if re.match(r'^<Untitled Chapter', ch['title']) else ch['title']}
                        for ch in yt_chapters]
        return insert_chapters(vtt_transcript, chapter_list), True

    # Fall back to defuddle's transcript; without one its content is only the embed and description
    m = re.search(r'^## Transcript[ \t]*$', content, re.MULTILINE)
    if not m:
        return '', False
    transcript_body = content[m.end():].strip()
    if vid_id:
        transcript_body = linkify_timecodes(transcript_body, vid_id)
    return transcript_body, bool(re.search(r'^### ', transcript_body, re.MULTILINE))


def present_youtube(d, url):
    """Read-only output for a video: title, channel, duration, description and transcript."""
    info      = yt_info(url)
    title     = strip_title_suffix(d.get('title', ''))
    meta_line = ' · '.join(x for x in (d.get('author', ''), yt_duration(info)) if x)
    desc      = strip_timecode_lines(info.get('description') or d.get('description', ''))
    transcript_body, _ = youtube_transcript(d.get('content', ''), url, youtube_id(url),
                                            info.get('chapters') or [])
    if not transcript_body:
        print('No subtitles available for this video — showing its title and description only.',
              file=sys.stderr)
    parts = [f'# {title}', meta_line, '## Description', desc.strip(),
             '## Transcript', transcript_body or NO_SUBTITLES]
    print('\n\n'.join(p for p in parts if p))


def fetch_vtt(url, vid_id, breaks=()):
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
            result = parse_vtt(content, vid_id, breaks=breaks)
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
            result = parse_vtt(content, vid_id, breaks=breaks)
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


def download_youtube(url, save_dir, filename_base, video_quality=None, audio_quality=None):
    """Download a YouTube video (or just its audio/video track) next to the note.

    video_quality: max height in pixels (e.g. 720); None = highest; 0 = audio only.
    audio_quality: max audio bitrate in kbps (e.g. 64); None = highest; 0 = video only (no sound).
    Video (with or without sound) → vid/{filename_base}-NNN.mp4; audio only → aud/{filename_base}-NNN.m4a.
    H.264 + AAC are preferred at the chosen resolution for playback in Obsidian on iOS;
    if YouTube has no H.264 at that resolution, another codec (VP9/AV1) is used and a
    warning with a ready-made ffmpeg conversion command is printed.
    English subtitles (manual, else auto-generated), chapters, metadata and the
    thumbnail are embedded in the file. Returns the path relative to save_dir, or None.
    """
    audio_only = video_quality == 0
    no_audio   = audio_quality == 0
    sub, ext   = ('aud', 'm4a') if audio_only else ('vid', 'mp4')
    out_dir = os.path.join(save_dir, sub)
    os.makedirs(out_dir, exist_ok=True)
    # Continue numbering after the highest existing file for this note
    nums = [int(m.group(1)) for f in os.listdir(out_dir)
            if (m := re.match(rf'^{re.escape(filename_base)}-(\d{{3}})\.', f))]
    name = f'{filename_base}-{max(nums, default=0) + 1:03d}'
    out_path = os.path.join(out_dir, f'{name}.{ext}')

    sort = []
    if not audio_only:
        sort += [f'res:{video_quality}' if video_quality else 'res', 'vcodec:h264']
    if not no_audio:
        sort += ['acodec:aac'] + ([f'abr:{audio_quality}'] if audio_quality else [])
    if audio_only:
        # m4a can't take yt-dlp's --embed-subs, so subs are written as .srt and muxed in below
        fmt = ['-f', 'ba/b', '-x', '--audio-format', 'm4a', '--convert-subs', 'srt']
    else:
        fmt = ['-f', 'bv*' if no_audio else 'bv*+ba/b',
               '--merge-output-format', 'mp4', '--remux-video', 'mp4', '--embed-subs']
    cmd = ['yt-dlp', *fmt, '-S', ','.join(sort),
           '--write-subs', '--write-auto-subs', '--sub-langs', 'en',
           '--embed-chapters', '--embed-metadata', '--embed-thumbnail',
           '--quiet', '--no-warnings', '-o', os.path.join(out_dir, f'{name}.%(ext)s'), url]
    # YouTube occasionally returns HTTP 403 on a fragment — one retry usually fixes it
    for attempt in range(2):
        r = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
        if r.returncode == 0 and os.path.exists(out_path):
            break
        print(f'Download failed (attempt {attempt + 1}/2).', file=sys.stderr)

    srt = os.path.join(out_dir, f'{name}.en.srt')
    if audio_only and os.path.exists(out_path) and os.path.exists(srt):
        tmp = os.path.join(out_dir, f'{name}.tmp.m4a')
        r = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', out_path, '-i', srt,
                            '-map', '0', '-map', '1', '-dn', '-c', 'copy', '-c:s', 'mov_text',
                            '-metadata:s:s:0', 'language=eng', tmp],
                           capture_output=True, text=True)
        if r.returncode == 0:
            os.replace(tmp, out_path)
        else:
            print('WARNING: could not embed subtitles in the audio file.', file=sys.stderr)

    # Remove leftover sidecar files (subs/thumbnail/temp) from the run
    for f in glob.glob(os.path.join(out_dir, f'{name}.*')):
        if f != out_path:
            os.unlink(f)
    if not os.path.exists(out_path):
        if not os.listdir(out_dir):
            os.rmdir(out_dir)
        return None

    if not audio_only:
        try:
            codec, height = subprocess.run(
                ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,height',
                 '-of', 'csv=p=0', out_path], capture_output=True, text=True, timeout=30).stdout.strip().split(',')
            if codec != 'h264':
                # H.264 is the safest choice up to 1080p; above that H.265 keeps files much smaller.
                # hvc1/avc1 tags are needed for Apple playback. -dn drops yt-dlp's chapter data track
                # (chapters themselves are kept), -map 0 keeps subs and cover art.
                enc = (['libx264', '-crf', '23', '-preset', 'slow', '-tag:v:0', 'avc1'] if int(height) < 1080
                       else ['libx265', '-crf', '26', '-preset', 'medium', '-tag:v:0', 'hvc1'])
                tmp = out_path[:-4] + '.conv.mp4'
                conv = ' '.join(shlex.quote(a) for a in
                                ['ffmpeg', '-i', out_path, '-map', '0', '-dn', '-c', 'copy', '-c:v:0', *enc,
                                 '-movflags', '+faststart', tmp])
                print(f'WARNING: no H.264 at {height}p — saved as {codec}, which may not play in Obsidian on iPhone.\n'
                      f'Convert to {"H.264" if int(height) < 1080 else "H.265"} '
                      f'(keeps subtitles, chapters, metadata and cover art):\n'
                      f'  {conv} && mv {shlex.quote(tmp)} {shlex.quote(out_path)}', file=sys.stderr)
        except Exception:
            pass
    return f'{sub}/{name}.{ext}'


def older_gemini_models(alias):
    """Older stable versions of a Gemini '-latest' alias's family, newest first, from Gemini's model
    list (e.g. gemini-flash-lite-latest → gemini-3.1-flash-lite, gemini-2.5-flash-lite). The newest
    version is left out: that is the one the alias points at. [] for other names or when the list fails."""
    family = re.fullmatch(r'gemini-(.+)-latest', alias)
    if not family:
        return []
    try:
        r = curl(f'https://generativelanguage.googleapis.com/v1beta/models?key={AI_API_KEY}&pageSize=1000', max_time=10)
        listed = json.loads(r.stdout).get('models', [])
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    versions = []
    for m in listed:
        v = re.fullmatch(rf'models/(gemini-(\d+(?:\.\d+)*)-{re.escape(family.group(1))})', m.get('name', ''))
        if v and 'generateContent' in m.get('supportedGenerationMethods', []):
            versions.append((tuple(map(int, v.group(2).split('.'))), v.group(1)))
    return [name for _, name in sorted(versions, reverse=True)[1:]]


def ai_request(prompt, timeout=60, json_reply=False):
    """Send a prompt to the provider configured by AI_LLM / AI_MODEL and return the reply text.
    With a list of models, an overloaded or rate-limited one (HTTP 429/5xx, timeout) passes to the
    next, and then to AI_OLDER_MODELS older versions of a Gemini '-latest' alias. json_reply asks the
    provider for valid JSON only. Raises when all fail; callers then fall back to the Claude CLI."""
    import urllib.request as _urlreq, urllib.error as _urlerr
    models = [AI_MODEL] if isinstance(AI_MODEL, str) else list(AI_MODEL)
    older = AI_LLM == 'gemini' and AI_OLDER_MODELS > 0   # older versions not added yet
    i = 0
    while i < len(models):
        model = models[i]
        last = i == len(models) - 1 and not older
        if AI_LLM == 'gemini':
            url  = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={AI_API_KEY}'
            body = {'contents': [{'parts': [{'text': prompt}]}],
                    **({'generationConfig': {'responseMimeType': 'application/json'}} if json_reply else {})}
            headers, pick = {}, lambda d: d['candidates'][0]['content']['parts'][0]['text']
        elif AI_LLM == 'openai':
            url  = (AI_BASE_URL.rstrip('/') + '/chat/completions') if AI_BASE_URL else 'https://api.openai.com/v1/chat/completions'
            body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                    **({'response_format': {'type': 'json_object'}} if json_reply else {})}
            headers, pick = {'Authorization': f'Bearer {AI_API_KEY}'}, lambda d: d['choices'][0]['message']['content']
        elif AI_LLM == 'ollama':
            url  = 'http://localhost:11434/api/generate'
            body = {'model': model, 'prompt': prompt, 'stream': False, **({'format': 'json'} if json_reply else {})}
            headers, pick = {}, lambda d: d['response']
        else:
            raise ValueError(f'Unsupported AI_LLM: {AI_LLM!r}')
        req = _urlreq.Request(url, data=json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json', **headers})
        try:
            # An overloaded model can hang instead of answering 503: don't wait long when another follows
            with _urlreq.urlopen(req, timeout=timeout if last else min(timeout, 20)) as r:
                return pick(json.loads(r.read()))
        except (_urlerr.HTTPError, TimeoutError, _urlerr.URLError) as e:
            busy = isinstance(e, TimeoutError) or getattr(e, 'code', 0) in (429, 500, 502, 503, 504) \
                   or isinstance(getattr(e, 'reason', None), TimeoutError)
            if busy and older and i == len(models) - 1:
                older = False
                models += [m for m in older_gemini_models(models[0]) if m not in models][:AI_OLDER_MODELS]
            if not busy or i == len(models) - 1:
                raise
            print(f'{model} unavailable ({e}) — trying {models[i + 1]}...', file=sys.stderr)
        i += 1


def ai_enrich(title, author, content, needs_chapters=False):
    """Call an AI model for description, summary, tags, and optionally chapters.

    Uses the provider configured by AI_LLM / AI_MODEL via direct API call.
    Falls back to the Claude CLI (always available in Claude Code) if the call fails.
    """
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

    # Try configured provider via direct API call
    if AI_API_KEY or AI_LLM == 'ollama':
        try:
            return _parse(ai_request(prompt, json_reply=True))
        except Exception as e:
            print(f'{AI_LLM} enrichment failed ({e}) — falling back to Claude...', file=sys.stderr)

    # Fall back to Claude CLI (always available in Claude Code, no key needed)
    try:
        result = subprocess.run(
            ['claude', '-p', prompt, '--model', AI_FALLBACK_MODEL, '--output-format', 'text'] if AI_FALLBACK_MODEL else ['claude', '-p', prompt, '--output-format', 'text'],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120
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
    headings = re.findall(r'^(#{' + str(min_level) + r',6})\s+(.+)$', content, re.MULTILINE)
    if not headings:
        return None
    # Map distinct heading levels to 0, 2, 4... spaces, but cap at 2 to avoid
    # the 4-space indented code block rendering bug in CommonMark/Obsidian.
    distinct_levels = sorted(set(len(h) for h, _ in headings))
    level_to_indent = {lvl: min(i * 2, 2) for i, lvl in enumerate(distinct_levels)}
    lines = []
    for hashes, title in headings:
        indent = ' ' * level_to_indent[len(hashes)]
        display = _heading_display(title)
        if display != title:
            lines.append(f'{indent}- [[#{title}|{display}]]')
        else:
            lines.append(f'{indent}- [[#{title}]]')
    return '\n'.join(lines)


def _toc_section(toc, heading='Contents'):
    """Wrap a TOC block in a collapsed callout with a trailing divider, for consistent note structure."""
    body = '\n'.join(f'> {line}' if line.strip() else '>' for line in toc.splitlines())
    return f'> [!note]- {heading}\n{body}\n\n---'


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


def build_youtube(d, url, project, created):
    title     = strip_title_suffix(d.get('title', ''))
    author    = d.get('author', '')
    published = (d.get('published') or '')[:10]
    site      = d.get('site', 'YouTube')
    words     = d.get('wordCount')
    content   = d.get('content', '')

    vid_id    = youtube_id(url)
    short_url = f'https://youtu.be/{vid_id}' if vid_id else url

    info     = yt_info(url)
    duration = yt_duration(info)
    desc     = strip_timecode_lines(info.get('description') or d.get('description', ''))

    # VTT captions give granular per-sentence timestamps; defuddle's transcript is the fallback
    transcript_body, has_chapters = youtube_transcript(content, url, vid_id, info.get('chapters') or [])

    # Gemini enrichment
    yt_content = f"Description:\n{desc}\n\nTranscript:\n{transcript_body}"
    ai_desc, ai_summary, ai_tags, ai_chapters = ai_enrich(
        title, author, yt_content, needs_chapters=bool(transcript_body) and not has_chapters
    )

    # Insert AI-generated chapters if needed
    if not has_chapters and ai_chapters:
        transcript_body = insert_chapters(transcript_body, ai_chapters)
        has_chapters = True

    # Collect and deduplicate tags (no # prefix — Obsidian adds it in display)
    desc_tags = extract_desc_tags(desc)
    ai_tags_clean = [t for t in (_slug(t) for t in ai_tags) if t]   # tags can't contain spaces
    all_tags = list(dict.fromkeys(desc_tags + ai_tags_clean))

    # Remove hashtags from description text
    desc = remove_desc_tags(desc)
    # Remove standalone TOC header lines (e.g. "Chapters", "Timestamps") — we build our own index
    desc = re.sub(r'(?im)^(index|contents|chapters|toc|timestamps)\s*$\n?', '', desc)
    # Strip all blank lines — Obsidian renders line spacing natively
    desc = '\n'.join(line for line in desc.splitlines() if line.strip())

    # Build Contents from chapter headings (none without a transcript)
    contents = build_index(transcript_body) or '[Chapters to be added]'
    contents_section = _toc_section(contents) + '\n\n' if transcript_body else ''

    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter(created, all_tags, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_url(short_url),
        frontmatter_field('channel', author),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('duration', duration),
        frontmatter_field('words', words, quote=False) if words else None,
        method_field('youtube'),
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

{contents_section}## Transcript

{transcript_body or NO_SUBTITLES}
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
    row = None
    if os.path.exists(db_path):
        # Title, description and duration moved over app versions (ZCLEANEDTITLE, ZITUNESSUBTITLE and
        # ZDURATION on the episode before 2026); a layout this doesn't know falls back to iTunes below
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
            try:
                row = conn.execute('''
                    SELECT COALESCE(e.ZTITLE, e.ZITUNESTITLE), e.ZAUTHOR, d.ZPLAINTEXT,
                           m.ZDURATION, e.ZPUBDATE, e.ZGUID,
                           p.ZTITLE, p.ZFEEDURL, p.ZSTORECLEANURL,
                           e.ZSEASONNUMBER, e.ZEPISODENUMBER
                    FROM ZMTEPISODE e
                    JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
                    LEFT JOIN ZMTEPISODEDESCRIPTION d ON d.Z_PK = e.ZDESCRIPTIONOBJECT
                    LEFT JOIN ZMTMEDIAENCLOSURE m ON m.Z_PK = e.ZCURRENTMEDIAENCLOSURE
                    WHERE e.ZSTORETRACKID = ?
                ''', (int(episode_id),)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as e:
            print(f'Podcasts library not readable ({e}) — using the iTunes API...', file=sys.stderr)
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
            result = curl(api_url)
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
        result = curl(feed_url)
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
        result = curl(chapters_url)
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


def build_apple_podcast(url, project, created, ttml_path, meta=None):
    """Build an Obsidian note for an Apple Podcasts episode from a local TTML transcript.
    meta: the episode's get_podcast_metadata() result, when the caller already has it."""
    episode_id = apple_podcast_id(url)
    show_id    = apple_show_id(url)
    chunks     = parse_ttml(ttml_path)
    if meta is None:
        meta   = get_podcast_metadata(episode_id, show_id) if episode_id else {}

    title      = meta.get('title', '')
    show       = meta.get('show', '')
    author     = meta.get('author', '') or show
    desc       = strip_timecode_lines(meta.get('description', ''))
    duration   = fmt_tc(meta['duration']) if meta.get('duration') else ''
    published  = meta.get('published', '')
    store_url  = meta.get('store_url', '')
    season     = meta.get('season')
    episode    = meta.get('episode')
    # One 'episode' value: S02E08, or E08 when the show has no seasons
    episode    = ((f'S{int(season):02d}' if season else '') + (f'E{int(episode):02d}' if episode is not None else '')) or None

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
    contents_section = _toc_section(contents)

    # Frontmatter
    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter(created, [t.lower() for t in ai_tags], [
        frontmatter_field('title',     title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_url(url),
        frontmatter_field('show',      show),
        frontmatter_field('episode',   episode),
        frontmatter_field('published', published),
        frontmatter_field('site',      'Apple Podcasts'),
        frontmatter_field('duration',  duration),
        frontmatter_field('words',     words, quote=False) if words else None,
        method_field('podcast'),
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

{contents_section}

## Transcript

{transcript_body}
"""


# ---------------------------------------------------------------------------
# Academic paper support (DOI / arXiv)
# ---------------------------------------------------------------------------

def is_pdf_url(url):
    """True for URLs or local paths ending in .pdf that aren't already handled as papers."""
    path = url if os.path.isabs(url) else urlparse(url).path
    return url in _PDF_URLS or (path.lower().endswith('.pdf') and not is_paper_url(url))

def extract_doi_from_pdf_text(text):
    """Scan text (e.g. first PDF page) for a DOI. Returns the bare DOI string or None."""
    # Match URL-formatted DOIs first (doi.org/..., dx.doi.org/...), then plain 10.xxxx/...
    patterns = [
        r'https?://(?:dx\.)?doi\.org/(10[.][0-9]{4,}(?:[.][0-9]+)*/(?:(?!["&\'<>])\S)+)',
        r'\b(10[.][0-9]{4,}(?:[.][0-9]+)*/(?:(?!["&\'<>])\S)+)\b',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).rstrip('.,;')
    return None

def is_paper_url(url):
    return (
        bool(re.match(r'https?://(dx\.)?doi\.org/', url)) or
        bool(re.match(r'https?://(www\.)?arxiv\.org/abs/', url)) or
        is_shadow_url(url) or
        is_ssrn_url(url) or
        is_academia_url(url)
    )

def is_academia_url(url):
    return bool(re.match(r'https?://(www\.)?academia\.edu/\d+', url))

def is_ssrn_url(url):
    return bool(re.search(r'ssrn\.com.*(abstract_id|abstract)=(\d+)', url, re.IGNORECASE))

def extract_ssrn_doi(url):
    m = re.search(r'(?:abstract_id|abstract)=(\d+)', url, re.IGNORECASE)
    return f'10.2139/ssrn.{m.group(1)}' if m else None

# Publisher and paper-index pages. Most carry the DOI in the URL; Elsevier uses a PII
# (looked up in Crossref, as ScienceDirect blocks scripts), Nature an article ID under
# 10.1038, and NCBI, OpenAlex and Semantic Scholar their own IDs (looked up in their
# APIs). For the rest, the page's citation_doi meta tag is read.
PAPER_SITES = (
    'sciencedirect.com', 'cell.com', 'thelancet.com',                            # Elsevier
    'link.springer.com', 'nature.com', 'biomedcentral.com', 'springeropen.com',  # Springer Nature
    'onlinelibrary.wiley.com', 'hindawi.com',                                    # Wiley
    'tandfonline.com', 'journals.sagepub.com', 'mdpi.com', 'frontiersin.org',
    'ieeexplore.ieee.org', 'pubs.acs.org', 'academic.oup.com', 'cambridge.org',
    'journals.plos.org', 'journals.lww.com', 'dl.acm.org', 'degruyter.com',
    'emerald.com', 'iopscience.iop.org', 'journals.aps.org', 'pubs.aip.org',
    'pubs.rsc.org', 'science.org', 'pnas.org', 'jstor.org', 'bmj.com',
    'jamanetwork.com', 'nejm.org', 'annualreviews.org', 'pubsonline.informs.org',
    'aeaweb.org', 'journals.uchicago.edu', 'direct.mit.edu', 'worldscientific.com',
    'epubs.siam.org', 'royalsocietypublishing.org', 'muse.jhu.edu', 'karger.com',
    'thieme-connect.com', 'brill.com',
    'nber.org', 'ncbi.nlm.nih.gov', 'europepmc.org', 'biorxiv.org', 'medrxiv.org',  # indexes and preprints
    'openalex.org', 'semanticscholar.org',
)

# Page-type suffixes publishers append after the DOI (/full, /abstract, .pdf, ...)
_DOI_TAIL = re.compile(r'(?:/(?:full|abstract|abs|pdf|epdf|pdfdirect|html|fulltext|summary|meta'
                       r'|references|figures|suppl)|\.(?:full|abstract|pdf|full-text))+$', re.IGNORECASE)

# Meta tags that carry a page's own DOI or PDF link (Google Scholar's citation_* tags,
# also used by university repositories such as DSpace, EPrints and Zenodo)
_DOI_META = ('citation_doi', 'bepress_citation_doi', 'prism.doi', 'dc.identifier', 'dcterms.identifier')
_PDF_META = ('citation_pdf_url', 'bepress_citation_pdf_url')
_PDF_URLS = set()   # PDF links without a .pdf extension, found in meta tags (see academic_target)

def _html_meta_tags(page):
    """<meta> tags of an HTML page in the form of defuddle's metaTags."""
    tags = []
    for tag in re.findall(r'<meta\b[^>]*>', page, re.IGNORECASE):
        attrs = {k.lower(): html.unescape(v) for k, _, v in re.findall(r'([\w:.-]+)\s*=\s*(["\'])(.*?)\2', tag)}
        tags.append({'name': attrs.get('name'), 'property': attrs.get('property'), 'content': attrs.get('content')})
    return tags

def _meta_values(tags, names):
    """Contents of the meta tags with these names, in the order of the names."""
    for name in names:
        for t in tags or []:
            if (t.get('name') or t.get('property') or '').lower() == name and t.get('content'):
                yield t['content'].strip()

def doi_from_meta(tags):
    """The page's own DOI from its meta tags, or None."""
    for value in _meta_values(tags, _DOI_META):
        m = re.search(r'10\.\d{4,9}/[^\s"<>]+', unquote(value))
        if m:
            return m.group(0).rstrip('.')
    return None

def academic_target(tags, page_url):
    """For a scholarly page (citation_* meta tags): its doi.org URL, else its PDF URL, else None."""
    doi = doi_from_meta(tags)
    if doi:
        return f'https://doi.org/{doi}'
    pdf = next(_meta_values(tags, _PDF_META), None)
    if pdf and ENABLE_PDF:
        pdf = urljoin(page_url, pdf)
        _PDF_URLS.add(pdf)
        return pdf
    return None

def is_paper_site(url):
    host = urlparse(url).netloc.lower().split(':')[0]
    return any(host == d or host.endswith('.' + d) for d in PAPER_SITES)

_MONTHS = [('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'), ('may',),
           ('june', 'jun'), ('july', 'jul'), ('august', 'aug'), ('september', 'sept', 'sep'),
           ('october', 'oct'), ('november', 'nov'), ('december', 'dec')]


def _date_in_text(date, text):
    """True when a YYYY-MM-DD date is written in text (ISO, 24-02-2026, 02/24/2026, 24 February 2026,
    February 24, 2026)."""
    y, m, d = date.split('-')
    mi, di = int(m), int(d)
    num = rf'0?{di}[-/.]0?{mi}[-/.]{y}|0?{mi}/0?{di}/{y}|{y}-{m}-{d}'
    names = '|'.join(_MONTHS[mi - 1])
    words = rf'\b0?{di}\.?\s+(?:{names})\.?\s+{y}|\b(?:{names})\.?\s+0?{di}(?:st|nd|rd|th)?,?\s+{y}'
    return bool(re.search(rf'(?<!\d)(?:{num})(?!\d)|{words}', text, re.I))


def _pdf_meta_people_date(path, text):
    """Author(s) and creation date from the PDF's metadata (pdfinfo), kept only when the document
    backs them up: a name whose last name appears in it, a date written out in it. Editor names
    ('Microsoft Word', 'Owner'), user names and dates of exporting or scanning are dropped."""
    if not shutil.which('pdfinfo'):
        return [], ''
    try:
        out = subprocess.run(['pdfinfo', '-isodates', path], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return [], ''
    field = lambda k: (re.search(rf'^{k}:\s*(.+)$', out, re.M) or [None, ''])[1].strip()
    seen = (text[:6000] + '\n' + text[-3000:]).lower()   # title page and signature
    raw = field('Author')
    parts = re.split(r'\s*;\s*|\s+(?:and|&)\s+', raw)
    if len(parts) == 1 and raw.count(',') == 1:           # "Hemert, Albert van" -> "Albert van Hemert"
        last, first = (p.strip() for p in raw.split(','))
        if first and len(last.split()) <= 2 and len(first.split()) <= 3:
            parts = [f'{first} {last}']
    elif len(parts) == 1 and ',' in raw and all(len(p.split()) >= 2 for p in raw.split(',')):
        parts = [p.strip() for p in raw.split(',')]
    authors = []
    for name in parts:
        words = name.split()
        if (2 <= len(words) <= 4 and all(re.fullmatch(r"[^\W\d_][\w.'’-]*", w) for w in words)
                and not re.search(r'microsoft|windows|adobe|acrobat|owner|admin|user|unknown|author', name, re.I)
                and len(words[-1]) > 1 and words[-1].lower() in seen):
            authors.append(name)
    date = field('CreationDate')[:10]
    if not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) and _date_in_text(date, text)):
        date = ''
    return authors, date

def _pdf_meta_title(path, raw=False):
    """The PDF's own Title metadata (pdfinfo), unless it is empty, a file name or an ID
    (raw=True returns it as is)."""
    if not shutil.which('pdfinfo'):
        return ''
    try:
        out = subprocess.run(['pdfinfo', path], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ''
    m = re.search(r'^Title:\s*(.+)$', out, re.M)
    title = m.group(1).strip() if m else ''
    if raw:
        return title
    if len(title) < 10 or re.match(r'(PII|DOI)\b', title, re.I) or re.search(r'\.(pdf|docx?|tex|dvi)$|^Microsoft Word|^untitled', title, re.I):
        return ''
    return title

def _title_on_pages(title, text):
    """True when this title appears in the text (letters and digits only, so line breaks don't matter)."""
    key = re.sub(r'[^a-z0-9]', '', _slug(title or ''))
    return len(key) >= 15 and key in re.sub(r'[^a-z0-9]', '', _slug(text or ''))

def crossref_doi_for_pdf(text, hint='', name=''):
    """DOI for a PDF without a printed one: search Crossref with its title (or first-page
    text) and accept a result only if that result's title appears on the PDF's first pages.
    name: the PDF's file name; publishers' download names carry an ID (Elsevier
    1-s2.0-{PII}-main.pdf, SSRN ssrn-{id}.pdf, arXiv {id}v2.pdf)."""
    m = re.match(r'1-s2\.0-(S?[0-9X]{16,17})', name or '', re.I)
    if m:
        pii = m.group(1).upper()
        doi = _crossref_pii_doi(pii if pii.startswith('S') else 'S' + pii) or _crossref_pii_doi(pii.lstrip('S'))
        if doi:
            return doi
    m = re.match(r'ssrn[-_](?:id)?(\d{4,8})\b', name or '', re.I)
    if m and _title_on_pages((get_paper_metadata(f'10.2139/ssrn.{m.group(1)}') or {}).get('title', ''), text):
        return f'10.2139/ssrn.{m.group(1)}'
    m = re.match(r'(\d{4}\.\d{4,5})(?:v\d+)?\.pdf$', name or '', re.I)
    if m:
        return f'10.48550/arXiv.{m.group(1)}'
    # Elsevier PIIs, e.g. 0304-405X(81)90020-9 (often the PDF's Title) or S0304405X14001548
    for src in (hint, text):
        m = re.search(r'\b(S?\d{4}-?\d{3}[\dX]\(\d\d\)\d{5}-?[\dX]|S\d{4}\d{3}[\dX]\d{8}[\dX])', src or '')
        if m:
            doi = _crossref_pii_doi(re.sub(r'[^0-9A-Za-z]', '', m.group(1)).upper())
            if doi:
                return doi
    clean_hint = '' if re.match(r'(PII|DOI)\b', hint or '', re.I) else hint
    for query in dict.fromkeys(q for q in (clean_hint, re.sub(r'\s+', ' ', text or '')[:300]) if q and q.strip()):
        msg = _crossref(f'works?query.bibliographic={quote(query)}&rows=5&select=DOI,title')
        hits = [item['DOI'] for item in (msg or {}).get('items', [])
                if _title_on_pages(' '.join(item.get('title') or []), text)]
        if hits:
            # Several versions (working paper, journal article): prefer the one whose DOI
            # ends in the file name, e.g. hhx084.pdf -> 10.1093/rfs/hhx084
            stem = os.path.splitext(name or '')[0].lower()
            named = [h for h in hits if len(stem) >= 5 and h.lower().rsplit('/', 1)[-1].endswith(stem)]
            return (named or hits)[0]
    return None

def _crossref(path):
    """GET a Crossref API path and return its 'message', or None (one retry: it occasionally doesn't answer)."""
    for attempt in range(2):
        try:
            r = curl(f'https://api.crossref.org/{path}')
            return json.loads(r.stdout.decode('utf-8', errors='replace'))['message']
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return None

def _get_json(url):
    """GET a JSON API, or None."""
    try:
        r = curl(url)
        return json.loads(r.stdout.decode('utf-8', errors='replace'))
    except Exception:
        return None

def _ncbi_doi(path):
    """DOI for a PubMed, PMC or Europe PMC article page, via NCBI's APIs, or None."""
    m = re.search(r'\b(PMC\d+)', path, re.IGNORECASE)
    if m:
        data = _get_json('https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/'
                         f'?ids={m.group(1).upper()}&format=json&tool=defuddle')
        recs = (data or {}).get('records') or [{}]
        return recs[0].get('doi')
    m = re.match(r'/(?:article/MED/|abstract/MED/)?(\d{5,9})\b', path, re.IGNORECASE)
    if m:
        data = _get_json('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
                         f'?db=pubmed&id={m.group(1)}&retmode=json&tool=defuddle')
        ids = ((data or {}).get('result') or {}).get(m.group(1), {}).get('articleids', [])
        return next((i['value'] for i in ids if i.get('idtype') == 'doi'), None)
    return None

def _index_doi(host, path):
    """DOI for an OpenAlex or Semantic Scholar work page, or None (arXiv papers get their arXiv DOI)."""
    if host.endswith('openalex.org'):
        m = re.search(r'\b(W\d+)', path)
        data = _get_json(f'https://api.openalex.org/works/{m.group(1)}?select=doi,ids') if m else None
        doi = (data or {}).get('doi') or ''
        return re.sub(r'^https?://doi\.org/', '', doi) or None
    m = re.search(r'\b([0-9a-f]{40})\b', path)
    if m:
        data = _get_json(f'https://api.semanticscholar.org/graph/v1/paper/{m.group(1)}?fields=externalIds')
        ids = (data or {}).get('externalIds') or {}
        if ids.get('DOI'):
            return ids['DOI']
        if ids.get('ArXiv'):
            return f'10.48550/arXiv.{ids["ArXiv"]}'
    return None

def _crossref_pii_doi(pii):
    """DOI for an Elsevier PII via Crossref's alternative-id field, or None."""
    msg = _crossref(f'works?filter=alternative-id:{pii}&rows=1&select=DOI')
    return msg['items'][0]['DOI'] if msg and msg.get('items') else None

def _mdpi_doi(path):
    """MDPI: /{issn}/{volume}/{issue}/{article} -> 10.3390/{journal code}{vv}{ii}{aaaa}.
    The journal code (e.g. 'su') comes from any DOI of that journal in Crossref."""
    m = re.match(r'/(\d{4}-\d{3}[\dX])/(\d+)/(\d+)/(\d+)', path)
    if not m:
        return None
    issn, vol, issue, art = m.groups()
    msg  = _crossref(f'journals/{issn}/works?rows=1&select=DOI')
    code = re.match(r'10\.3390/([a-z]+)', msg['items'][0]['DOI']) if msg and msg.get('items') else None
    if not code:
        return None
    doi = f'10.3390/{code.group(1)}{int(vol):02d}{int(issue):02d}{int(art):04d}'
    return doi if _crossref(f'works/{doi}') else None

def paper_site_doi(url):
    """DOI for a publisher article page (see PAPER_SITES), or None."""
    p      = urlparse(url)
    path   = unquote(p.path)
    target = path + ('?' + unquote(p.query) if p.query else '')
    m = re.search(r'\b(10\.\d{4,9}/[^?#&\s]+)', target)
    if m:
        doi = _DOI_TAIL.sub('', m.group(1)).rstrip('/.')
        return re.sub(r'v\d+$', '', doi) if doi.startswith('10.1101/') else doi   # bioRxiv versions
    host = p.netloc.lower()
    if host.endswith('nber.org'):
        m = re.search(r'/(w\d{3,6})\b', path)
        if m:
            return f'10.3386/{m.group(1)}'
    if host.endswith(('ncbi.nlm.nih.gov', 'europepmc.org')):
        return _ncbi_doi(path)
    if host.endswith(('openalex.org', 'semanticscholar.org')):
        return _index_doi(host, path)
    # Elsevier: /pii/S1703494920300384, or Cell/Lancet style S0092-8674(11)00127-9
    m = (re.search(r'/pii/([A-Z0-9]+)', path, re.IGNORECASE) or
         re.search(r'/(S\d{4}-?\d{3}[\dX]\(\d\d\)\d{5}-?[\dX])', path, re.IGNORECASE))
    if m:
        doi = _crossref_pii_doi(re.sub(r'[^0-9A-Za-z]', '', m.group(1)).upper())
        if doi:
            return doi
    if p.netloc.lower().endswith('nature.com'):
        m = re.match(r'/articles/([A-Za-z0-9.\-]+)$', path)
        if m:
            return f'10.1038/{m.group(1)}'
    if p.netloc.lower().endswith('mdpi.com'):
        doi = _mdpi_doi(path)
        if doi:
            return doi
    if p.netloc.lower().endswith('jstor.org'):
        # Many (not all) JSTOR items are registered as 10.2307/{stable id}
        m = re.match(r'/stable/(?:pdf/)?(\d+)', path)
        if m and _crossref(f'works/10.2307/{m.group(1)}'):
            return f'10.2307/{m.group(1)}'
    try:
        r = curl(url)
        page = r.stdout.decode('utf-8', errors='replace')
    except (OSError, subprocess.SubprocessError):
        return None
    doi = doi_from_meta(_html_meta_tags(page))
    if doi:
        return doi
    m = re.search(r'"doi"\s*:\s*"(10\.\d{4,9}/[^"]+)"', page)   # IEEE Xplore page metadata
    return m.group(1) if m else None

def extract_academia_pdf_url(url):
    """Fetch an Academia.edu page and return (pdf_url, doi). Either may be None."""
    result = curl(url, '-A', 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)')
    html = result.stdout.decode('utf-8', errors='replace')
    pdf_url = None
    for pattern in [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
        r'"pdf_url"\s*:\s*"([^"]+)"',
        r'href=["\']([^"\']+\.pdf(?:[^"\']*)?)["\']',
    ]:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            pdf_url = m.group(1)
            break
    doi = None
    m = re.search(r'<meta[^>]+name=["\']citation_doi["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_doi["\']', html, re.IGNORECASE)
    if m:
        doi = m.group(1).strip()
    return pdf_url, doi

def is_shadow_url(url):
    return bool(SHADOW_BASE_URL) and url.startswith(SHADOW_BASE_URL.rstrip('/'))

def extract_doi_from_shadow(url):
    path = url[len(SHADOW_BASE_URL.rstrip('/')):].lstrip('/')
    return path if path.startswith('10.') else None

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
        result = curl(f'https://export.arxiv.org/api/query?id_list={arxiv_id}', max_time=10)
        root = ET.fromstring(result.stdout.decode('utf-8', errors='replace'))
        ns = {'atom': 'http://www.w3.org/2005/Atom',
              'arxiv': 'http://arxiv.org/schemas/atom'}
        entry = root.find('atom:entry', ns)
        if entry is None:
            return {}
        title = (entry.findtext('atom:title', '', ns) or '').strip().replace('\n', ' ')
        authors = []
        for a in entry.findall('atom:author', ns):
            name = _clean_author(a.findtext('atom:name', '', ns) or '')
            if name:
                authors.append(name)
        published = (entry.findtext('atom:published', '', ns) or '')[:10]
        journal_ref = (entry.findtext('arxiv:journal_ref', '', ns) or '').strip()
        return {'title': title, 'authors': authors, 'published': published,
                'journal': journal_ref, 'doi': f'10.48550/arXiv.{arxiv_id}'}
    except Exception:
        return {}

_NAME_PARTICLES = {'van', 'von', 'der', 'den', 'de', 'del', 'della', 'di', 'da', 'du', 'la', 'le', 'ten', 'ter'}

def _clean_author(name):
    """Tidy an author name from Crossref/arXiv: 'A.Craig' -> 'A. Craig', and names in
    capitals ('ROBERT A. SCHWARTZ') -> 'Robert A. Schwartz' (keeping McX, O'X, van der)."""
    name = re.sub(r'\s+', ' ', name).strip()
    name = re.sub(r'\b([A-Z])\.(?=[A-Z][a-z])', r'\1. ', name)
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        def word(w, first):
            if not first and w.lower() in _NAME_PARTICLES:
                return w.lower()
            w = '-'.join(p[:1] + p[1:].lower() for p in w.split('-'))
            return re.sub(r"^(Mc|O')([a-z])", lambda m: m.group(1) + m.group(2).upper(), w)
        name = ' '.join(word(w, i == 0) for i, w in enumerate(name.split()))
    return name

def get_paper_metadata(doi):
    """Fetch paper metadata from CrossRef API, with arXiv API fallback."""
    # For arXiv DOIs (10.48550/arXiv.XXXX), use the arXiv API directly
    arxiv_id = extract_arxiv_id_from_doi(doi)
    if arxiv_id:
        return _get_paper_metadata_arxiv(arxiv_id)
    try:
        result = curl(f'https://api.crossref.org/works/{doi}', '-H', 'User-Agent: defuddle/1.0 (mailto:user@example.com)', max_time=10)
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        work = data.get('message', {})
        title = ' '.join(work.get('title', [''])).strip()
        title = re.sub('[\u2010\u2011]', '-', title)   # Crossref's Unicode hyphens
        authors = []
        for a in work.get('author', []):
            name = _clean_author(f'{a.get("given", "")} {a.get("family", "")}')
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
        result = curl(f'https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=externalIds', max_time=10)
        data = json.loads(result.stdout.decode('utf-8', errors='replace'))
        return data.get('externalIds', {}).get('ArXiv')
    except Exception:
        return None

def get_arxiv_latex(arxiv_id, tmp_dir):
    """Download arXiv LaTeX source and return path to main .tex file."""
    import tarfile as tarmod, gzip as gzmod
    src_path = os.path.join(tmp_dir, 'source.bin')
    curl(f'https://arxiv.org/e-print/{arxiv_id}', '-o', src_path, max_time=30, capture=False)
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


def _inline_inputs(content, source_dir, depth=0):
    """Replace \\input/\\include of .tex files in the source folder with their text, so figures and
    labels in separate files are numbered together."""
    def _sub(m):
        name = m.group(2).strip()
        path = os.path.realpath(os.path.join(source_dir, name if name.endswith('.tex') else name + '.tex'))
        if depth >= 5 or not path.startswith(os.path.realpath(source_dir) + os.sep) or not os.path.isfile(path):
            return m.group(0)
        with open(path, encoding='utf-8', errors='replace') as f:
            return m.group(1) + _inline_inputs(f.read(), source_dir, depth + 1)
    return re.sub(r'^([^%\n]*)\\(?:input|include)\{([^}]+)\}', _sub, content, flags=re.MULTILINE)


def _number_figures(content):
    """Prefix each figure caption with "Figure N." the way LaTeX numbers it (pandoc drops the number).
    Sub-figure captions are left alone. Returns (content, {label: number})."""
    content = re.sub(r'^[ \t]*%.*\n', '', content, flags=re.MULTILINE)
    content = re.sub(r'\\iffalse\b.*?\\fi\b', '', content, flags=re.DOTALL)
    labels = {}
    count = [0]

    def _number(fig):
        block = fig.group(0)
        # Sub-figures, and algorithms or tables placed inside a figure, have their own captions
        nested = [s.span() for s in re.finditer(r'\\begin\{(subfigure|algorithm\*?|table\*?)\}.*?\\end\{\1\}', block, re.DOTALL)]
        out, last, current, waiting = [], 0, None, []
        for m in re.finditer(r'\\caption(?:of\{figure\}|(\*?))(?:\[[^\]]*\])?\{|\\label\{([^}]+)\}', block):
            if any(a <= m.start() < b for a, b in nested):
                continue
            if m.group(2):
                if current:
                    labels[m.group(2)] = current
                else:
                    waiting.append(m.group(2))
            elif not m.group(1):
                count[0] += 1
                current = str(count[0])
                labels.update((w, current) for w in waiting)
                waiting = []
                head = '\\caption{' if 'captionof' in m.group(0) else m.group(0)   # pandoc drops \captionof
                out += [block[last:m.start()], head, f'Figure {current}. ']
                last = m.end()
        return ''.join(out) + block[last:]

    return re.sub(r'\\begin\{figure(\*?)\}.*?\\end\{figure\1\}', _number, content, flags=re.DOTALL), labels


def _resolve_figure_refs(content, fig_labels):
    """Replace \\cref/\\Cref/\\autoref to figures with "Figure N" / "Figures N and M"."""
    def _sub(m):
        nums = [fig_labels.get(k.strip()) for k in m.group(1).split(',')]
        if not all(nums):
            return m.group(0)
        if len(nums) == 1:
            return f'Figure {nums[0]}'
        return f'Figures {", ".join(nums[:-1])} and {nums[-1]}'
    return re.sub(r'\\(?:[cC]ref|[aA]utoref)\{([^}]+)\}', _sub, content)


def _resolve_toggles(content):
    """Replace \\iftoggle{name}{a}{b} with the branch set by the paper's \\toggletrue/\\togglefalse."""
    state = {m.group(2): m.group(1) == 'true'
             for m in re.finditer(r'^[^%\n]*\\toggle(true|false)\{(\w+)\}', content, re.MULTILINE)}
    return re.sub(r'\\iftoggle\{(\w+)\}\{([^{}]*)\}\{([^{}]*)\}',
                  lambda m: m.group(2) if state.get(m.group(1)) else m.group(3), content)


def _preprocess_latex(content):
    """Preprocess LaTeX so pandoc can parse it without choking."""
    # Expand simple \newcommand macros so structural commands like \begin/\end
    # are visible to _collect_labels even when wrapped in paper-specific shorthands.
    content = _expand_simple_macros(content)
    content = _resolve_toggles(content)
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
    try:
        result = subprocess.run(
            ['pandoc', '-f', fmt, '-t', 'markdown', '--wrap=none', '-o', out_path, pre_path],
            capture_output=True, cwd=cwd, timeout=60
        )
    except subprocess.TimeoutExpired:
        print(f'pandoc ({fmt}) timed out.', file=sys.stderr)
        return False
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
    source_dir = os.path.dirname(tex_path)
    raw = _inline_inputs(raw, source_dir)
    nt_data = _parse_newtheorem(raw)
    raw, fig_labels = _number_figures(raw)
    preprocessed = _preprocess_latex(raw)
    # Resolve cross-references: replace \ref{}/\eqref{} with numbers and
    # inject \textbf{N} markers into theorem environments.
    labels = {**_collect_labels(preprocessed, nt_data=nt_data), **fig_labels}
    preprocessed = _resolve_figure_refs(preprocessed, fig_labels)
    preprocessed = _inject_ref_numbers(preprocessed, labels, nt_data=nt_data)
    # Inline .bbl bibliography: try \bibliography{name}.bbl → main.bbl →
    # master.bbl → any .bbl in the source directory.
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

def openalex_free(doi):
    """Free copies of a paper according to OpenAlex: (direct PDF links, page with the free
    version or None). The page is for publishers that block scripts but show the PDF in a browser."""
    data = _get_json(f'https://api.openalex.org/works/doi:{quote(doi)}?select=open_access,best_oa_location,locations')
    if not data or not (data.get('open_access') or {}).get('is_oa'):
        return [], None
    locs = [data.get('best_oa_location') or {}] + [l for l in data.get('locations') or [] if l.get('is_oa')]
    pdfs = list(dict.fromkeys(l['pdf_url'] for l in locs if l.get('pdf_url')))
    page = next((l.get('landing_page_url') or l.get('pdf_url') for l in locs if l.get('landing_page_url') or l.get('pdf_url')), None)
    return pdfs, page

def _download_pdf(url, tmp_dir, name='free.pdf'):
    """Download a PDF link; the path if it really is a PDF, else None."""
    path = os.path.join(tmp_dir, name)
    try:
        curl(url, '-o', path, max_time=60)
        with open(path, 'rb') as f:
            ok = f.read(5) == b'%PDF-' and os.path.getsize(path) > 1000
    except (OSError, subprocess.SubprocessError):
        ok = False
    return path if ok else None

def fetch_doi_pdf(doi, tmp_dir):
    """Try to download PDF by following a DOI redirect."""
    pdf_path = os.path.join(tmp_dir, 'paper.pdf')
    result = curl(f'https://doi.org/{doi}', '-o', pdf_path, '-w', '%{content_type}', max_time=20, text=True)
    if 'pdf' in result.stdout.lower() and \
            os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
        return pdf_path
    return None

def downloads_dir():
    """The folder the browser saves to: DOWNLOADS_DIR, else the system default."""
    if DOWNLOADS_DIR:
        return os.path.expanduser(DOWNLOADS_DIR)
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders') as key:
                return winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0]
        except OSError:
            pass
    elif sys.platform.startswith('linux'):
        try:
            # Prints $HOME when no download dir is configured
            path = subprocess.run(['xdg-user-dir', 'DOWNLOAD'], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
            if path and os.path.normpath(path) != os.path.expanduser('~'):
                return path
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.join(os.path.expanduser('~'), 'Downloads')

_PDF_TEXT = """
import sys, pymupdf
pymupdf.TOOLS.mupdf_display_errors(False)
doc = pymupdf.open(sys.argv[1])
print(''.join(doc[i].get_text() for i in range(min(2, len(doc)))))
"""

def _pdf_first_pages_text(path):
    """Text of the first two pages: pdftotext, else PyMuPDF via uv, else a raw byte scan."""
    cmds = []
    if shutil.which('pdftotext'):
        cmds.append(['pdftotext', '-l', '2', '-q', path, '-'])
    if shutil.which('uv'):
        cmds.append(['uv', 'run', '--no-project', '--quiet', '--with', 'pymupdf', 'python', '-c', _PDF_TEXT, path])
    for cmd in cmds:
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
            if text.strip():
                return text
        except (OSError, subprocess.SubprocessError):
            pass
    with open(path, 'rb') as f:
        raw = f.read(65536)
    return b' '.join(re.findall(rb'[\x20-\x7e]{4,}', raw)).decode('ascii', errors='ignore')

def _pdf_matches(text, doi, title):
    """True when the text of a PDF's first pages carries this DOI, or else this title."""
    text = text.lower()
    if re.sub(r'\s+', '', doi.lower()) in re.sub(r'\s+', '', text):
        return True
    # Letters and digits only: survives line breaks and hyphenation in the title
    key = re.sub(r'[^a-z0-9]', '', _slug(title or ''))
    return len(key) >= 15 and key in re.sub(r'[^a-z0-9]', '', _slug(text))

def wait_for_shadow_pdf(doi, title='', url=None, label='shadow library'):
    """Open the shadow library page (or another page: url, label) in the browser and wait for the user to save the PDF.

    Shadow libraries sit behind JavaScript browser checks (e.g. DDoS-Guard) that curl
    can't pass, so the download happens in the user's browser. Returns the path of the
    new PDF in the downloads folder whose first pages show this DOI (or title), or None
    after SHADOW_WAIT. PDFs of other papers are skipped, so several runs can wait at once.
    Partial downloads (.crdownload, .part, .download) are ignored; the file is left in place.
    """
    import webbrowser
    url    = url or f'{SHADOW_BASE_URL.rstrip("/")}/{doi}'
    folder = downloads_dir()
    if not os.path.isdir(folder):
        print(f'Downloads folder not found: {folder} (set DOWNLOADS_DIR).', file=sys.stderr)
        print(f'Download the PDF manually and pass the local file to defuddle: {url}', file=sys.stderr)
        return None
    start  = time.time()
    before = set(os.listdir(folder))
    limit  = f'{SHADOW_WAIT // 60} min' if SHADOW_WAIT >= 120 else f'{SHADOW_WAIT} s'
    print(f'Opening {label} in your browser: {url}', file=sys.stderr)
    print(f'Save the PDF to {folder} — waiting up to {limit}...', file=sys.stderr, flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    sizes, checked = {}, {}
    while time.time() < start + SHADOW_WAIT:
        time.sleep(2)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith('.pdf'):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if name in before and st.st_mtime < start:
                continue
            if any(n in names for n in (name + '.part', name + '.crdownload', name + '.download')):
                continue
            # Firefox creates the final name empty while writing .part; wait for a stable size
            if st.st_size > 1000 and sizes.get(name) == st.st_size:
                key = (st.st_size, st.st_mtime)
                if checked.get(name) == key:
                    continue
                checked[name] = key
                with open(path, 'rb') as f:
                    if f.read(5) != b'%PDF-':
                        continue
                text = _pdf_first_pages_text(path)
                if _pdf_matches(text, doi, title):
                    print(f'Found {name}', file=sys.stderr)
                    return path
                other = extract_doi_from_pdf_text(text)
                why   = f'it is a different paper (DOI {other})' if other else f'no DOI {doi} or matching title on its first pages'
                print(f'Skipping {name}: {why}', file=sys.stderr, flush=True)
            sizes[name] = st.st_size
    print(f'No PDF for DOI {doi} appeared in {folder} within {limit}.', file=sys.stderr)
    if checked:
        print('If one of the skipped PDFs is this paper, pass its path to defuddle.', file=sys.stderr)
    return None

def pdf_to_markdown(pdf_path):
    """Convert a PDF to markdown + images dict via PDF_PROCESSOR setting.

    Returns (markdown_str, images_dict, method_name).
    """
    def _try_datalab():
        if not DATALAB_API_KEY:
            return None
        print('Converting PDF via Datalab Marker API...', file=sys.stderr)
        result = curl('https://www.datalab.to/api/v1/marker', '-X', 'POST', '-H', f'X-Api-Key: {DATALAB_API_KEY}', '-F', f'file=@{pdf_path}', '-F', 'output_format=markdown', '-F', 'mode=balanced', max_time=55)
        try:
            data = json.loads(result.stdout.decode('utf-8', errors='replace'))
            check_url = data.get('request_check_url')
            if check_url:
                print('Waiting for Datalab conversion...', file=sys.stderr)
                for _ in range(60):
                    time.sleep(5)
                    poll = curl(check_url, '-H', f'X-Api-Key: {DATALAB_API_KEY}', max_time=10)
                    status = json.loads(poll.stdout.decode('utf-8', errors='replace'))
                    if status.get('status') == 'complete':
                        return status.get('markdown', ''), status.get('images', {}), 'datalab'
                    if status.get('status') == 'failed':
                        print(f'Datalab failed: {status.get("error", "")}', file=sys.stderr)
                        return None
                print('Datalab timed out.', file=sys.stderr)
            else:
                print(f'Datalab error: {data.get("error", "no request URL")}', file=sys.stderr)
        except Exception as e:
            print(f'Datalab error: {e}', file=sys.stderr)
        return None

    def _try_local():
        if not shutil.which('marker_single'):
            return None
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
            if MARKER_PRECISE:
                cmd += ['--force_ocr']   # without it inline math can stay broken text (*Xt* instead of $X_t$)
            if use_llm:
                cmd += ['--use_llm', '--llm_service', marker_service]
                if MARKER_PRECISE:
                    cmd += ['--redo_inline_math']
                if key_flag and AI_API_KEY:
                    cmd += [key_flag, AI_API_KEY]
                print(f'Running local marker (with {AI_LLM} LLM)...', file=sys.stderr)
            else:
                print('Running local marker...', file=sys.stderr)
            r = _run_marker(cmd)
            md_files = glob.glob(os.path.join(out_dir, '**', '*.md'), recursive=True)
            if not md_files:
                error = '\n'.join([l for l in r.stderr.splitlines() if l.strip()][-5:])
                if AI_API_KEY:
                    error = error.replace(AI_API_KEY, '<API key>')
                print(f'marker failed (exit {r.returncode}):\n{error}', file=sys.stderr)
                return None
            with open(md_files[0], encoding='utf-8') as f:
                markdown = f.read()
            _IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
            images_dict = {}
            for img_file in glob.glob(os.path.join(out_dir, '**', '*'), recursive=True):
                if os.path.splitext(img_file)[1].lower() in _IMG_EXT:
                    try:
                        with open(img_file, 'rb') as f:
                            images_dict[os.path.basename(img_file)] = base64.b64encode(f.read()).decode()
                    except Exception:
                        pass
            return markdown, images_dict, 'marker'

    def _convert():
        return _convert_with_processor(_try_local, _try_datalab)

    result = _convert()
    if result is not None:
        expected = _pdf_text_words(pdf_path)
        got = len(result[0].split())
        if expected and expected > 500 and got < expected * INCOMPLETE_RATIO:
            print(f'Conversion looks incomplete ({got} of about {expected} words) — retrying once...', file=sys.stderr)
            retry = _convert()
            if retry is not None and len(retry[0].split()) > got:
                result, got = retry, len(retry[0].split())
            if got < expected * INCOMPLETE_RATIO:
                print(f'WARNING: conversion looks incomplete ({got} of about {expected} words in the PDF\'s '
                      'text layer); check the note against the PDF.', file=sys.stderr)

    if result is None:
        print(
            'PDF conversion failed. Install local marker with: pipx install marker-pdf\n'
            'Or set a Datalab API key in ~/.claude/CLAUDE.md.',
            file=sys.stderr
        )
        return '', {}, ''

    markdown, images, method = result
    markdown = _clean_pdf_markdown(markdown, images)
    missing = _missing_equations(pdf_path, markdown)
    if missing:
        print(f'WARNING: equation{"s" if len(missing) > 1 else ""} {_number_ranges(missing)} from the PDF '
              'missing in the note (part of the text may be lost there); check it against the PDF.', file=sys.stderr)
    missing = _missing_appendices(pdf_path, markdown)
    if missing:
        print(f'WARNING: {", ".join(missing)} from the PDF missing in the note (no heading for '
              f'{"it" if len(missing) == 1 else "them"}); check it against the PDF.', file=sys.stderr)
    return markdown, images, method


def _run_marker(cmd, timeout=4 * 3600):
    """Run marker and show that it is working: its progress bars as short lines ('marker:
    Recognizing Text 120/360 (30%)', once per 10% per step) when it draws any, and a line
    every 5 minutes. Returns a CompletedProcess whose stderr holds marker's last lines."""
    # marker only draws progress bars on a terminal, so give it a pseudo-terminal where possible
    master = None
    if os.name != 'nt':
        import pty
        master, slave = pty.openpty()
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=slave)
        os.close(slave)
        read = lambda: os.read(master, 4096)
    else:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        read = lambda: p.stderr.read1(4096)
    tail, shown, buf, start = [], {}, b'', time.time()

    # This marker version prints no progress bars, so say every 5 minutes that it is still working
    import threading
    done = threading.Event()
    pages = (re.search(r'^Pages:\s+(\d+)', subprocess.run(['pdfinfo', cmd[1]], capture_output=True, text=True).stdout, re.M)
             if shutil.which('pdfinfo') else None)
    pages = f', {pages.group(1)} pages' if pages else ''

    def heartbeat():
        while not done.wait(300):
            print(f'marker: still converting ({int((time.time() - start) // 60)} min so far{pages})',
                  file=sys.stderr, flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()

    def handle(line):
        line = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', line).strip()   # terminal control codes
        if not line:
            return
        tail.append(line)
        del tail[:-20]
        m = re.match(r'(.+?):\s+(\d+)%\|.*?\|\s*(\d+)/(\d+)', line)
        if m:
            step, pct = m.group(1).strip(), int(m.group(2))
            if shown.get(step) != pct // 10:
                shown[step] = pct // 10
                print(f'marker: {step} {m.group(3)}/{m.group(4)} ({pct}%)', file=sys.stderr, flush=True)

    while True:
        try:
            chunk = read()
        except OSError:   # the pseudo-terminal closes when marker exits
            break
        if not chunk:
            break
        buf += chunk
        *lines, buf = re.split(rb'[\r\n]', buf)   # tqdm redraws its bar with \r
        for line in lines:
            handle(line.decode('utf-8', 'replace'))
        if time.time() - start > timeout:
            p.kill()
            break
    handle(buf.decode('utf-8', 'replace'))
    p.wait()
    done.set()
    if master is not None:
        os.close(master)
    return subprocess.CompletedProcess(cmd, p.returncode, '', '\n'.join(tail))


# A converted PDF normally has 85–125% of the words in the PDF's own text layer; much less
# means part of it was lost (e.g. a conversion that stopped partway through)
INCOMPLETE_RATIO = 0.7

def _missing_equations(path, md):
    """Equation numbers printed in the PDF (a '(12)' on its own line of the text layer) that no
    display equation in the markdown carries. Catches parts lost in the middle of a document."""
    if not shutil.which('pdftotext'):
        return []
    try:
        text = subprocess.run(['pdftotext', '-q', path, '-'], capture_output=True, text=True,
                              errors='replace', timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    in_pdf = {int(n) for n in re.findall(r'^\s*\((\d{1,3})\)\s*$', text, re.M)}
    if len(in_pdf) < 3:
        return []
    eqs = ' '.join(_display_equations(md))
    in_md = {int(a or b) for a, b in re.findall(r'\((\d{1,3})\)|\\tag\{(\d{1,3})\}', eqs)}
    return sorted(in_pdf - in_md)

def _missing_appendices(path, md):
    """Appendix titles in the PDF ('Appendix A. ...' starting a line of the text layer) that no
    heading in the markdown names, e.g. ['Appendix A']. Catches short appendices that the word
    count and equation checks don't notice."""
    if not shutil.which('pdftotext'):
        return []
    try:
        text = subprocess.run(['pdftotext', '-q', path, '-'], capture_output=True, text=True,
                              errors='replace', timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    in_pdf = dict.fromkeys(re.findall(r'^\s*Appendix ([A-Z]|\d{1,2})[.:]\s+[A-Z]', text, re.M))
    in_md = set(re.findall(r'^#{1,6}\s+[*_]*Appendix ([A-Z]|\d{1,2})\b', md, re.M))
    return [f'Appendix {a}' for a in in_pdf if a not in in_md]

def _number_ranges(nums):
    """[3, 4, 5, 9] -> '(3)–(5), (9)'."""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f'({nums[i]})' if i == j else f'({nums[i]})–({nums[j]})')
        i = j + 1
    return ', '.join(out)

def _pdf_text_words(path):
    """Word count of a PDF's text layer (pdftotext), or None when it has none or pdftotext is missing."""
    if not shutil.which('pdftotext'):
        return None
    try:
        text = subprocess.run(['pdftotext', '-q', path, '-'], capture_output=True, text=True,
                              errors='replace', timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return len(text.split()) or None


def _convert_with_processor(_try_local, _try_datalab):
    """Run the PDF processors in PDF_PROCESSOR order; the first result, or None."""
    if PDF_PROCESSOR == 'local':
        result = _try_local()
    elif PDF_PROCESSOR == 'datalab':
        result = _try_datalab()
    elif PDF_PROCESSOR == 'local_first':
        result = _try_local()
        if result is None:
            print('Local marker unavailable — falling back to Datalab...', file=sys.stderr)
            result = _try_datalab()
    else:  # datalab_first
        result = _try_datalab()
        if result is None:
            print('Datalab unavailable — falling back to local marker...', file=sys.stderr)
            result = _try_local()
    return result


def _thumbnail_pixels(data):
    """Pixels of an image (bytes) shrunk to 64×64, for comparing; None without sips or ImageMagick."""
    magick = shutil.which('magick') or (shutil.which('convert') if os.name != 'nt' else None)
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, 'image'), os.path.join(tmp, 'thumb.bmp')
        with open(src, 'wb') as f:
            f.write(data)
        if shutil.which('sips'):
            cmd = ['sips', '-z', '64', '64', '-s', 'format', 'bmp', src, '--out', out]
        elif magick:
            cmd = [magick, f'{src}[0]', '-resize', '64x64!', f'BMP3:{out}']
        else:
            return None
        subprocess.run(cmd, capture_output=True)
        if not os.path.exists(out):
            return None
        with open(out, 'rb') as f:
            bmp = f.read()
    return bmp[int.from_bytes(bmp[10:14], 'little'):]


def _drop_duplicate_images(md, images):
    """Remove an image that directly follows a nearly identical one: PDF converters sometimes detect
    one figure twice, with slightly different crops. A figure repeated later on is left alone."""
    out, pos, prev_end, prev_pixels = [], 0, None, None
    for m in re.finditer(r'!\[[^\]]*\]\(([^)\s]+)\)', md):
        raw = _find_image(images, m.group(1))
        try:
            pixels = _thumbnail_pixels(base64.b64decode(raw.split(',', 1)[1] if ',' in raw else raw)) if raw else None
        except Exception:
            pixels = None
        if (pixels and prev_pixels and len(pixels) == len(prev_pixels) and not md[prev_end:m.start()].strip()
                and sum(abs(a - b) for a, b in zip(pixels, prev_pixels)) / len(pixels) < DUPLICATE_IMAGE_DIFF):
            out.append(md[pos:prev_end])
            pos, prev_end = m.end(), m.end()
            continue
        prev_end, prev_pixels = m.end(), pixels
    return ''.join(out) + md[pos:]


def _clean_pdf_markdown(md, images):
    """Tidy marker/Datalab output for Obsidian: remove page anchors and links, descriptions without
    an image, and figures detected twice; split image descriptions that wrap other images; warn
    about display equations that won't render."""
    # The AI step sometimes wraps one panel's image inside another's description:
    # ![Description 1: ![](a.jpeg) | Description 2: caption](b.jpeg) -> both images
    md = re.sub(r'!\[Description \d+: (!\[[^\]]*\]\([^)\s]+\)).*?\]\(([^)\s]+\.(?:jpe?g|png|gif|webp))\)',
                r'\1\n\n![](\2)', md, flags=re.S)
    md = _separate_inline_math(md)
    md = _tags_out_of_environments(md)
    md = _bold_titles_to_headings(md)
    md = _normalize_heading_levels(md)
    _warn_broken_equations(md)
    md = re.sub(r'!\[[^\]]*\]\(\s*\)[ \t]*\n*', '', md)
    md = re.sub(r'<span id="[^"]*">\s*</span>', '', md)
    md = re.sub(r'\[((?:\\.|[^\]\\])*)\]\(#page-[\d-]+\)',
                lambda m: re.sub(r'\\([\[\]])', r'\1', m.group(1)), md)
    return _drop_duplicate_images(md, images)

# Numbered headings: (kind, pattern). A marker's punctuation ('.', ')', ':', a dash or none) is part of its
# style, so '1.' sections and '1)' items can be different levels. First match wins.
_P = r'(?:[.):]|\s*[-–—])'   # punctuation after a marker
_HEADING_STYLES = [
    ('arabic3', re.compile(r'^\d{1,2}\.\d{1,2}\.\d{1,2}[.):]?\s')),
    ('arabic2', re.compile(r'^\d{1,2}\.\d{1,2}[.):]?\s')),
    ('arabic',  re.compile(rf'^\d{{1,2}}({_P}?)\s+\S')),
    ('roman',   re.compile(rf'^[IVX]{{1,5}}({_P})\s')),
    ('upper',   re.compile(rf'^[A-Z]({_P})\s')),
    ('lroman',  re.compile(rf'^[ivx]{{1,5}}({_P})\s')),
    ('lower',   re.compile(rf'^[a-z]({_P})\s')),
    ('pnum',    re.compile(r'^\(\d{1,2}\)()\s')),
    ('proman',  re.compile(r'^\([ivx]{1,4}\)()\s')),
    ('plower',  re.compile(r'^\([a-z]\)()\s')),
]
# A one-letter roman numeral right after the previous letter is that letter: "I." after "H.", "(i)" after "(h)"
_LETTER_OF = {'roman': 'upper', 'lroman': 'lower', 'proman': 'plower'}

# Letters and roman numerals without punctuation ("A Introduction", "IV Results"): only numbering when
# the document has two of them in sequence, since "A Model of Trading" also starts with a letter
_BARE_STYLES = [('roman',  re.compile(r'^([IVX]{1,5})\s+[A-Z]')),
                ('upper',  re.compile(r'^([A-Z])\s+[A-Z]')),
                ('lroman', re.compile(r'^([ivx]{1,4})\s+[A-Za-z]')),
                ('lower',  re.compile(r'^([a-z])\s+[A-Za-z]'))]

def _marker_value(kind, marker):
    if kind in ('roman', 'lroman'):
        vals = {'i': 1, 'v': 5, 'x': 10}
        n = [vals[c] for c in marker.lower()]
        return sum(-v if i + 1 < len(n) and v < n[i + 1] else v for i, v in enumerate(n))
    return ord(marker.lower()) - ord('a') + 1

def _heading_style(title):
    """(kind, style) of a numbered heading, e.g. ('upper', 'upper:') for 'A: Data'; None if not numbered."""
    for kind, rx in _HEADING_STYLES:
        m = rx.match(title)
        if m:
            punct = (m.group(1) or '').strip() if m.groups() else ''
            return kind, kind + ('-' if punct in ('-', '–', '—') else punct)
    return None

def _bare_numbering(titles):
    """{index: kind} for headings numbered without punctuation, where that kind has two markers in sequence."""
    seen = {}
    for i, t in enumerate(titles):
        if _heading_style(t):
            continue
        for kind, rx in _BARE_STYLES:
            m = rx.match(t)
            if m:
                seen.setdefault(kind, []).append((i, _marker_value(kind, m.group(1))))
                break
    out = {}
    for kind, hits in seen.items():
        vals = {v for _, v in hits}
        if any(v + 1 in vals for v in vals):
            out.update({i: kind for i, _ in hits})
    return out

def _normalize_heading_levels(md):
    """Give numbered section headings consistent levels (marker guesses levels from font size).
    Numbering can be 1 / I / i / A / a followed by '.', ')', ':', a dash or nothing, or (1) / (i) / (a).
    The style that comes first is the main level and each style that appears later one level deeper;
    '2.1' always sits right below '2.' and '2.1.1' below that. Main sections become ##, right below
    the note's title. Only for documents with at least two headings in the main style."""
    matches = list(re.finditer(r'^(#{2,6}) +(.+)$', md, re.M))
    titles = [re.sub(r'[*_]', '', m.group(2)).strip() for m in matches]
    bare = _bare_numbering(titles)
    found, prev_letter = [], {}
    for i, (m, t) in enumerate(zip(matches, titles)):
        ks = _heading_style(t) or ((bare[i], bare[i]) if i in bare else None)
        if not ks:
            continue
        kind, style = ks
        letter = re.match(r'^\(?([A-Za-z])(?:[.):]|\s)', t)
        if kind in _LETTER_OF and letter:
            punct = style[len(kind):]
            prev = prev_letter.get(_LETTER_OF[kind] + punct)
            if prev and ord(letter.group(1)) == ord(prev) + 1:
                kind, style = _LETTER_OF[kind], _LETTER_OF[kind] + punct
        if kind in _LETTER_OF.values() and letter:
            prev_letter[style] = letter.group(1)
        found.append((m.start(1), m.end(1), kind, style))
    if not found:
        return md
    order = []
    for *_, kind, style in found:
        if kind not in ('arabic2', 'arabic3') and style not in order:
            order.append(style)
    deeper = [s for s in ('arabic2', 'arabic3') if any(f[3] == s for f in found)]
    first_arabic = next((i for i, s in enumerate(order) if s.startswith('arabic')), None)
    if first_arabic is not None:        # 2.1 and 2.1.1 sit directly below 2.
        order[first_arabic + 1:first_arabic + 1] = deeper
    else:
        order += deeper
    if sum(1 for f in found if f[3] == order[0]) < 2:
        return md
    for a, b, _, style in reversed(found):
        md = md[:a] + '#' * min(2 + order.index(style), 6) + md[b:]
    return md

def _bold_titles_to_headings(md):
    """A bold line on its own that is a section title ('**Appendix C. Relaxing ...**', '**3. Results**',
    '**2.1 Data**') becomes a heading; marker sometimes outputs titles as bold text. Its level is
    set by _normalize_heading_levels (appendices: ##)."""
    title = (r'(?:Appendix\s+(?:[A-Z]|\d{1,2})\b|\d{1,2}\.\d{1,2}\.?\s|(?:\d{1,2}|[IVX]{1,5}|[A-Z])[.):]\s)'
             r'[^*\n]{2,120}?')
    return re.sub(rf'(?:^|(?<=\n\n))\*\*({title})\*\*[ \t]*(?=\n\n|\n?$)',
                  lambda m: '## ' + m.group(1).strip(), md)

def _tags_out_of_environments(md):
    """Obsidian's math renderer rejects \\tag inside aligned/split/gathered/...: move a single tag
    after \\end{...}; with several tags, write the numbers as text, e.g. '\\qquad (5.11)'."""
    env = r'(aligned|align\*?|gathered|split|array|cases|[pbv]?matrix)'

    def fix(m):
        inner = m.group(2)
        tags = re.findall(r'\\tag\*?\{([^}]*)\}', inner)
        if not tags:
            return m.group(0)
        if len(tags) == 1:
            inner = re.sub(r'\s*\\tag\*?\{[^}]*\}', '', inner)
            return f'\\begin{{{m.group(1)}}}{inner}\\end{{{m.group(1)}}} \\tag{{{tags[0]}}}'
        inner = re.sub(r'\\tag\*?\{([^}]*)\}', r'\\qquad (\1)', inner)
        return f'\\begin{{{m.group(1)}}}{inner}\\end{{{m.group(1)}}}'
    return re.sub(rf'\\begin\{{{env}\}}(.*?)\\end\{{\1\}}', fix, md, flags=re.S)

def _separate_inline_math(md):
    """'$s^1$$d$' (two inline formulas side by side) reads as a display-equation marker in
    Obsidian; put a space between them. Lines that are display equations are left alone."""
    return '\n'.join(l if l.lstrip().startswith('$$') else re.sub(r'(\$[^$\n]+?)\$\$(?=[^$\s][^$\n]*\$)', r'\1$ $', l)
                     for l in md.split('\n'))

def _display_equations(md):
    """Contents of display equations: $$ on its own lines around them, or around a whole line."""
    return (re.findall(r'^[ \t]*\$\$[ \t]*\n(.*?)\n[ \t]*\$\$[ \t]*$', md, re.S | re.M) +
            re.findall(r'^[ \t]*\$\$(?!\s*$)(.+?)\$\$[ \t]*$', md, re.M))

def _warn_broken_equations(md):
    """WARNING for display equations with unbalanced braces or \\left/\\right, which Obsidian can't render."""
    bad = []
    for eq in _display_equations(md):
        if (len(re.findall(r'(?<!\\)\{', eq)) != len(re.findall(r'(?<!\\)\}', eq)) or
                len(re.findall(r'\\left\b', eq)) != len(re.findall(r'\\right\b', eq))):
            num = re.search(r'\((\d+[a-z]?)\)\s*$', eq.strip())
            bad.append(f'({num.group(1)})' if num else repr(eq.strip()[:40] + '…'))
    if bad:
        print(f'WARNING: {len(bad)} display equation(s) have unbalanced brackets and won\'t render in '
              f'Obsidian: {", ".join(bad[:10])}; check them against the PDF.', file=sys.stderr)

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
            raw_author = re.sub(r'(?<!\\)%.*', '', raw_author)                              # comments
            raw_author = re.sub(r'\\(?:[hv]space\*?\{[^{}]*\}|footnotemark(?:\[[^\]]*\])?)', '', raw_author)
            raw_author = re.sub(r'\\(L|l|O|o|ss)(?:\{\}|\b\s*)',                             # \L{}ukasz → Łukasz
                                lambda m: {'L': 'Ł', 'l': 'ł', 'O': 'Ø', 'o': 'ø', 'ss': 'ß'}[m.group(1)], raw_author)
            # \and, \And, \AND separate authors; within one, lines after \\ are affiliations and e-mails
            for name in re.split(r'\\and\b', raw_author, flags=re.I):
                name = _latex_strip_markup(re.split(r'\\\\', name.strip())[0])
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

def fix_paper_markdown(md, latex=False):
    """Sanitise pandoc markdown output for Obsidian compatibility.
    latex: the markdown comes from LaTeX source, so an image's alt text is the figure caption."""
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
    # pandoc writes bitmap figures as <img> and PDF/EPS figures as <embed>; a figure made of
    # sub-figures has several. All become images, the caption goes below them as text, because
    # Obsidian doesn't show alt text. The numbered captions are kept, as a block can hold two
    # figures side by side; without numbers the last <figcaption> is the main one.
    _fig_src = r'<(?:img|embed)[^>]+src=["\']([^"\']+)["\']'
    def _caption_md(text):
        return re.sub(r'^Figure (\d+)\.', r'**Figure \1.**', text.strip())
    def _caption_text(raw):
        # MathML holds the rendered formula and its LaTeX: keep only the LaTeX
        raw = re.sub(r'<math\b.*?<annotation[^>]*>(.*?)</annotation>.*?</math>', r'$\1$', raw, flags=re.DOTALL)
        return html.unescape(re.sub(r'<[^>]+>', '', raw)).strip()
    def _figure_to_md(m):
        block = m.group(0)
        srcs = re.findall(_fig_src, block)
        caps = [_caption_text(c) for c in re.findall(r'<figcaption>(.*?)</figcaption>', block, re.DOTALL)]
        captions = [_caption_md(c) for c in ([c for c in caps if re.match(r'Figure \d+\.', c)] or caps[-1:])]
        images = '\n'.join(f'![]({s})' for s in srcs)
        return '\n' + '\n\n'.join(x for x in [images, *captions] if x) + '\n'
    md = re.sub(r'<figure[^>]*>.*?</figure>', _figure_to_md, md, flags=re.DOTALL)
    if latex:
        md = re.sub(r'^!\[(.+)\]\(([^)\n]+)\)(?:\{[^}\n]*\})?[ \t]*$',
                    lambda m: f'![]({m.group(2)})\n\n{_caption_md(m.group(1))}', md, flags=re.MULTILINE)
    # Pandoc attributes after an image: ![](a.png){#fig:a width="\\linewidth"}
    md = re.sub(r'(!\[[^\n]*?\]\([^)\n]*\))\{[^}\n]*\}', r'\1', md)
    # Stray <embed> outside a <figure> (e.g. a PDF figure inside a table)
    md = re.sub(r'<embed[^>]+src=["\']([^"\']+)["\'][^>]*/?>', r'\n![](\1)\n', md)

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


def _fix_paper_structure(content, title='', authors=None, ai_paragraphs=False):
    """Fix common structural issues in publisher-formatted (PDF-converted) papers:
    - Strip content before the paper title (publisher headers, logos, etc.)
    - Remove duplicate title headings
    - Normalise abstract heading to ## Abstract and move it to the top
    Returns cleaned content."""
    # 0. Strip publisher preamble (headers, logos, author affiliations, download links, etc.)
    #    Preferred anchor: the Abstract heading — it's the cleanest entry point and naturally
    #    skips all the noise that appears between the title and the body of the paper.
    #    Fallback: search for the paper title, then first author name.
    anchor = None
    abs_m = re.search(r'^#{1,4}\s+\*{0,3}Abstract\*{0,3}\s*$', content, re.IGNORECASE | re.MULTILINE)
    if abs_m:
        anchor = abs_m.start()
    else:
        needles = ([title] if title else []) + ([authors[0]] if authors else [])
        for needle in needles:
            m = re.search(re.escape(needle), content, re.IGNORECASE)
            if m:
                anchor = content.rfind('\n', 0, m.start()) + 1
                break
    if anchor:
        content = content[anchor:]

    # 1. Move AI-generated body paragraphs between images and their real captions into the
    #    image alt-text. Datalab/marker emits AI descriptions both as alt-text AND as a
    #    following body paragraph (which may differ). We merge both into the alt-text so the
    #    AI still has full context while keeping the note body clean for human readers.
    #    Format: "Description 1: {original alt} | Description 2: {body text}"
    #    Datalab only: marker and LaTeX output have no AI paragraphs, so text between an image
    #    and its caption is real body text (e.g. a paragraph continued after a page break).
    lines = content.split('\n')
    # Shared vocabulary for figure/table label terms.
    # Require bold markers (**) before the label — Datalab always bolds real captions;
    # plain-text "Figure 2: ..." lines are AI duplicates and must not be treated as captions.
    _cap_vocab  = r'fig(?:ure)?|tab(?:le)?|sch(?:eme)?|pl(?:ate)?|chart|diag(?:ram)?|panel|appendix|app'
    caption_re  = re.compile(rf'(?mi)^\W*\*+(?:{_cap_vocab})\.?\s*\d')
    # Strip a leading bold caption label (e.g. "**Fig. 2.**") from the start of a line.
    cap_label_re = re.compile(rf'^\W*\*+(?:{_cap_vocab})[\w.\s]*?\*+\s*', re.IGNORECASE)
    line_updates  = {}   # line index -> replacement string
    lines_to_remove = set()
    img_re = re.compile(r'^(!\[)(.*?)(\]\([^)]+\))\s*$')
    for i, line in enumerate(lines):
        img_m = img_re.match(line) if ai_paragraphs else None
        if not img_m:
            continue
        # Find the first bolded real caption within 15 lines, before the next image
        cap_idx = None
        for j in range(i + 1, min(i + 16, len(lines))):
            if img_re.match(lines[j]):
                break
            if caption_re.search(lines[j]):
                cap_idx = j
                break
        if cap_idx is None:
            continue
        # Collect all non-empty lines between image and caption (AI-generated; remove from body).
        body_lines   = [lines[j].strip() for j in range(i + 1, cap_idx) if lines[j].strip()]
        body_indices = [j                for j in range(i + 1, cap_idx) if lines[j].strip()]
        # Also include the real caption text in the alt-text (keep it in the body too).
        # Strip the leading bold figure label (e.g. "**Fig. 2.**") — just keep the description.
        cap_text = cap_label_re.sub('', lines[cap_idx].strip()).strip()
        # Merge all sources: original alt-text + AI body paragraphs + real caption.
        # Deduplicate by normalised content (backslash escapes stripped for comparison).
        orig_alt  = img_m.group(2).strip()
        all_descs = ([orig_alt] if orig_alt else []) + body_lines + [cap_text]
        seen_norm = set()
        unique    = []
        for desc in all_descs:
            norm = re.sub(r'\\(.)', r'\1', desc)
            if norm not in seen_norm:
                seen_norm.add(norm)
                unique.append(desc)
        if len(unique) > 1:
            parts   = [f'Description {n}: {desc}' for n, desc in enumerate(unique, start=1)]
            new_alt = ' | '.join(parts)
            line_updates[i] = f'{img_m.group(1)}{new_alt}{img_m.group(3)}'
        for j in body_indices:
            lines_to_remove.add(j)
    if line_updates or lines_to_remove:
        content = '\n'.join(
            line_updates.get(i, l)
            for i, l in enumerate(lines)
            if i not in lines_to_remove
        )

    # 2. Remove duplicate title headings (any level, case-insensitive).
    #    Strip markdown bold/italic markers before comparing so "# **Title**" matches "Title".
    if title:
        _strip_md = lambda t: re.sub(r'[*_`]', '', t)
        title_norm = re.sub(r'\s+', ' ', _strip_md(title).strip().lower())
        def _drop_dup_title(m):
            heading_text = re.sub(r'\s+', ' ', _strip_md(m.group(1)).strip().lower())
            return '' if heading_text == title_norm else m.group(0)
        content = re.sub(r'^#{1,4}\s+(.+?)\s*$', _drop_dup_title, content, flags=re.MULTILINE)

    # 3. Normalise abstract heading and move it to the top of the content
    abs_m = re.search(
        r'^(#{1,4})\s+\*{0,3}Abstract\*{0,3}\s*\n+(.*?)(?=\n#{1,4}\s|\Z)',
        content, re.DOTALL | re.MULTILINE | re.IGNORECASE
    )
    if abs_m:
        abstract_body = abs_m.group(2).strip()
        abstract_section = f'## Abstract\n\n{abstract_body}'
        content = content[:abs_m.start()] + content[abs_m.end():]
        content = abstract_section + '\n\n' + content.lstrip('\n')

    return re.sub(r'\n{3,}', '\n\n', content).strip()


_HTML_TAG = re.compile(r'</?[a-zA-Z!][^<>]*>')


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
        # Strip HTML tags (a real tag only: "<90% error ... >1000" is text)
        elif text[i] == '<' and _HTML_TAG.match(text, i):
            i = _HTML_TAG.match(text, i).end()
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)

def build_paper(url, project, created, pdf_file=None, converted=None):
    """Fetch an academic paper and build an Obsidian note.
    pdf_file: a PDF of this paper already at hand (e.g. a local file whose first page
    named the DOI); used instead of downloading one.
    converted: (markdown, images, method) of pdf_file, when it was already converted."""
    doi      = extract_doi(url)
    arxiv_id = extract_arxiv_id_from_url(url)
    if doi and not arxiv_id:
        arxiv_id = extract_arxiv_id_from_doi(doi)

    # arxiv.org/abs links have no DOI: their LaTeX \author block can hold e-mails and affiliations
    meta = get_paper_metadata(doi) if doi else _get_paper_metadata_arxiv(arxiv_id) if arxiv_id else {}

    if not arxiv_id and doi and not converted:   # a converted PDF needs no preprint
        print('Checking for arXiv preprint...', file=sys.stderr)
        arxiv_id = find_arxiv_id(doi)
    if converted:
        arxiv_id = None
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
                        content = fix_paper_markdown(content, latex=True)
                        method  = 'arxiv'
                        meta['pdf_url'] = f'https://arxiv.org/pdf/{arxiv_id}'
                        # Collect figure files from the source dir (and its subfolders, e.g. figures/)
                        # before tmp_dir is cleaned up, keyed by their path relative to it. PDF/EPS
                        # figures are rendered to PNG/JPG by download_images → finalize_image.
                        _IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.pdf', '.eps', '.ps'}
                        src_dir = os.path.dirname(tex_path)
                        for _root, _dirs, _files in os.walk(src_dir):
                            for _fname in _files:
                                if os.path.splitext(_fname)[1].lower() in _IMG_EXT:
                                    _full = os.path.join(_root, _fname)
                                    _rel = os.path.relpath(_full, src_dir).replace(os.sep, '/')
                                    try:
                                        with open(_full, 'rb') as _f:
                                            images_dict[_rel] = base64.b64encode(_f.read()).decode()
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
            pdf_path = pdf_file
            if not pdf_path and arxiv_id:
                print(f'Trying arXiv PDF ({arxiv_id})...', file=sys.stderr)
                arxiv_pdf_url = f'https://arxiv.org/pdf/{arxiv_id}'
                arxiv_pdf_path = os.path.join(tmp_dir, 'arxiv.pdf')
                result = curl(arxiv_pdf_url, '-o', arxiv_pdf_path, '-w', '%{content_type}', max_time=30, text=True)
                if 'pdf' in result.stdout.lower() and \
                        os.path.exists(arxiv_pdf_path) and os.path.getsize(arxiv_pdf_path) > 1000:
                    pdf_path = arxiv_pdf_path
            if not pdf_path and doi:
                print('Trying direct PDF download...', file=sys.stderr)
                pdf_path = fetch_doi_pdf(doi, tmp_dir)
            if not pdf_path and doi:
                # A free copy (OpenAlex) before the shadow library: a direct PDF link, else the
                # free page in the browser (publishers like ams.org block scripts)
                free_pdfs, free_page = openalex_free(doi)
                for link in free_pdfs:
                    print(f'Trying free copy ({urlparse(link).netloc})...', file=sys.stderr)
                    pdf_path = _download_pdf(link, tmp_dir)
                    if pdf_path:
                        break
                if not pdf_path and free_page:
                    pdf_path = wait_for_shadow_pdf(doi, meta.get('title', ''), url=free_page, label='the free version')
            if not pdf_path and doi:
                if SHADOW_BASE_URL:
                    pdf_path = wait_for_shadow_pdf(doi, meta.get('title', ''))
                else:
                    print('Paper is not publicly accessible and no shadow library is configured (set SHADOW_BASE_URL).', file=sys.stderr)
            if not pdf_path:
                print('Could not retrieve PDF.', file=sys.stderr)
                return '', {}, {}
            content, images_dict, method = converted or pdf_to_markdown(pdf_path)
            with open(pdf_path, 'rb') as f:
                meta['pdf'] = f.read()   # for --keep-pdf; tmp_dir is gone once this returns
            if content:
                content = fix_paper_markdown(content)

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
    if abstract_text and not re.search(r'#{1,4}\s+\*{0,3}Abstract\*{0,3}\b', content, re.IGNORECASE):
        content = f'## Abstract\n\n{abstract_text}\n\n' + content
    # Normalise abstract heading: "--- Abstract" → "Abstract"
    content = re.sub(r'^(#{1,4})\s*---\s*Abstract', r'\1 Abstract', content,
                     flags=re.MULTILINE | re.IGNORECASE)
    # Fix reference list items: "- [N] " → "[N] " (prevents Obsidian checkbox rendering)
    content = re.sub(r'^- (\[\d+\] )', r'\1', content, flags=re.MULTILINE)

    # Fix paper structure: strip pre-title content, remove duplicate titles, move abstract to top
    content = _fix_paper_structure(content, meta.get('title', ''), meta.get('authors', []), ai_paragraphs=method == 'datalab')
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
    abstract_end_m = re.search(r'#{1,4}\s+\*{0,3}Abstract\*{0,3}[^\n]*\n', content, re.IGNORECASE)
    toc_source = content[abstract_end_m.end():] if abstract_end_m else content
    toc = build_index(toc_source, min_level=1)
    if toc:
        next_sec = re.search(r'\n(#{1,4}\s+(?!\*{0,3}Abstract\b))', content, re.IGNORECASE)
        if next_sec:
            ins = next_sec.start(1)
            content = content[:ins] + _toc_section(toc) + '\n\n' + content[ins:]

    published = meta.get('published', '')
    journal   = meta.get('journal', '')
    doi_str   = meta.get('doi', doi or '')
    words     = len(re.findall(r'\S+', content))

    kw_tags = []
    for kw in paper_keywords:
        kw_slug = _slug(kw)
        if kw_slug:
            kw_tags.append(kw_slug)

    author_lines = (['author:'] + [f'  - {yaml_str(a)}' for a in authors]) if authors else []
    fm = build_frontmatter(created, list(ai_tags) + kw_tags, [
        frontmatter_field('title', title),
        f'description: {yaml_str(ai_desc or "[Description to be added]")}',
        frontmatter_url(url),
        f'doi: {doi_str}' if doi_str else None,
        *author_lines,
        frontmatter_field('published', published),
        frontmatter_field('publication', journal),
        f'words: {words}',
        method_field(method),
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
    ai_tags_clean = [t for t in (_slug(t) for t in ai_tags) if t]   # tags can't contain spaces

    # Build index from article headings
    index = build_index(content)

    fm_description = ai_desc if ai_desc else '[Description to be added]'
    fm = build_frontmatter(created, ai_tags_clean, [
        frontmatter_field('title', title),
        f'description: {yaml_str(fm_description)}',
        frontmatter_url(url, d.get('archive_url')),   # archived copy as a second source
        *([f'author:\n  - {yaml_str(author)}'] if author else []),
        frontmatter_field('published', published),
        frontmatter_field('site', site),
        frontmatter_field('words', words, quote=False) if words else None,
        method_field(method),
    ])
    summary = ai_summary if ai_summary else '[Summary to be added]'

    index_section = ('\n' + _toc_section(index, 'Index') + '\n') if index else '\n---\n'

    return f"""{fm}

---

# {title}

## Summary

{summary}
{index_section}
{content}
"""


def build_pdf(url, project, created, skip_doi=False):
    """Download (or copy) a plain PDF and build an Obsidian note."""
    local_path = url if os.path.isabs(url) else None
    with tempfile.TemporaryDirectory() as tmp_dir:
        pdf_name = os.path.basename(url) or 'document.pdf'
        if not pdf_name.lower().endswith('.pdf'):
            pdf_name += '.pdf'
        pdf_path = os.path.join(tmp_dir, pdf_name)
        if local_path:
            import shutil as _shutil
            _shutil.copy2(local_path, pdf_path)
        else:
            print('Downloading PDF...', file=sys.stderr)
            curl(url, '-o', pdf_path, max_time=115)
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1024:
            print('Failed to download PDF or file too small.', file=sys.stderr)
            return '', {}, {}

        # Scan first ~64KB of raw bytes for embedded ASCII text, then check for DOI
        with open(pdf_path, 'rb') as _f:
            raw = _f.read(65536)
        first_page = b' '.join(re.findall(rb'[\x20-\x7e]{4,}', raw)).decode('ascii', errors='ignore')
        doi = extract_doi_from_pdf_text(first_page)
        pdf_title = _pdf_meta_title(pdf_path)
        if not doi and not skip_doi:
            # Text layer of the first pages (raw bytes are usually compressed), then Crossref
            text = _pdf_first_pages_text(pdf_path)
            doi = extract_doi_from_pdf_text(text)
            if doi and not _title_on_pages((get_paper_metadata(doi) or {}).get('title', ''), text):
                doi = None   # a cited paper's DOI, not this one's
            if not doi:
                doi = crossref_doi_for_pdf(text, _pdf_meta_title(pdf_path, raw=True), os.path.basename(url))
                if doi:
                    print(f'Found in Crossref by title: {doi}', file=sys.stderr)
        if doi and not skip_doi:
            print(f'DOI found: {doi} — processing as paper...', file=sys.stderr)
            with open(pdf_path, 'rb') as f:
                return None, None, {'redirect_doi': doi, 'pdf_bytes': f.read()}

        content, images_dict, method = pdf_to_markdown(pdf_path)
        pdf_authors, pdf_published = _pdf_meta_people_date(pdf_path, content or '')
        if content and not doi and not skip_doi:
            # Scans without a text layer only become readable now: search Crossref with the converted text
            head = re.search(r'^#{1,2}\s+(.+)$', content, re.M)
            doi = crossref_doi_for_pdf(content[:4000], head.group(1).strip() if head else '', os.path.basename(url))
            if doi:
                print(f'Found in Crossref from the converted text: {doi} — saving as paper...', file=sys.stderr)
                with open(pdf_path, 'rb') as f:
                    return None, None, {'redirect_doi': doi, 'pdf_bytes': f.read(),
                                        'converted': (content, images_dict, method)}

    if not content or len(content.split()) < MIN_WORDS:
        print('PDF conversion produced no usable content.', file=sys.stderr)
        return '', {}, {}

    # Extract title from first heading, fall back to filename
    title_match = re.search(r'^#{1,2}\s+(.+)$', content, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
        content = content[title_match.end():].lstrip('\n')
    else:
        title = pdf_title or re.sub(r'[-_]+', ' ', os.path.splitext(pdf_name)[0]).strip()

    words = len(content.split())
    ai_desc, ai_summary, ai_tags, _ = ai_enrich(title, '', content)
    ai_tags_clean = [t for t in (_slug(t) for t in ai_tags) if t]   # tags can't contain spaces

    if local_path:
        site = ''
    else:
        p = urlparse(url)
        root_url = f'{p.scheme}://{p.netloc}'
        site = fetch_defuddle(root_url).get('site', '')
        if not site:
            parts = p.netloc.split('.')
            if len(parts) > 2:
                site = fetch_defuddle(f'{p.scheme}://www.{".".join(parts[-2:])}').get('site', '')

    fm = build_frontmatter(created, ai_tags_clean, [
        frontmatter_field('title', title),
        f'description: {yaml_str(ai_desc or "[Description to be added]")}',
        *([] if local_path else [frontmatter_url(url)]),
        *(['author:'] + [f'  - {yaml_str(a)}' for a in pdf_authors] if pdf_authors else []),
        frontmatter_field('published', pdf_published),
        frontmatter_field('site', site),
        frontmatter_field('words', words, quote=False),
        method_field(method),
    ])
    summary = ai_summary or '[Summary to be added]'
    index = build_index(content)
    index_section = ('\n' + _toc_section(index, 'Index') + '\n') if index else '\n---\n'

    note = f"""{fm}

---

# {title}

## Summary

{summary}
{index_section}
{content}
"""
    return note, images_dict, {'title': title}


def get_vault_path():
    """Return vault path: VAULT_PATH setting > ~/.claude/projects.yaml > ~/Documents/Obsidian."""
    if VAULT_PATH:
        return os.path.expanduser(VAULT_PATH)
    try:
        content = open(os.path.expanduser('~/.claude/projects.yaml')).read()
        m = re.search(r'path:\s*(.+)', content)
        return m.group(1).strip() if m else os.path.expanduser('~/Documents/Obsidian')
    except Exception:
        return os.path.expanduser('~/Documents/Obsidian')


def get_save_dir(vault, category, project, subdir):
    """Build save directory path. category and project are optional; SAVE_SUBFOLDER, when set,
    replaces the per-type subdir (docs, papers, transcripts)."""
    parts = [vault] + ([category] if category else []) + ([project] if project else []) + [SAVE_SUBFOLDER or subdir]
    return os.path.join(*parts)


def format_saved_path(category, project, subdir, filename):
    """Format the 'Saved to ...' confirmation string."""
    parts = ([category] if category else []) + ([project] if project else []) + [SAVE_SUBFOLDER or subdir, filename]
    return '/'.join(parts)


_TRANSLIT = str.maketrans({'ß': 'ss', 'æ': 'ae', 'œ': 'oe', 'ø': 'o', 'ł': 'l', 'đ': 'd',
                            'ð': 'd', 'þ': 'th', 'ı': 'i'})
_APOSTROPHES = re.compile("['\u2018\u2019\u02bc\u0060\u00b4]")


_ABBREVIATION = re.compile(r'(?<![\w.])(?:[^\W\d_]{1,2}\.){2,}')   # U.S., Ph.D., e.g.


def _slug(text):
    """Kebab-case slug. Latin letters become plain ASCII (Guéant -> gueant, ß -> ss); other scripts
    (Greek, Cyrillic, kanji…) are kept as they are. Apostrophes and abbreviation dots join words
    instead of splitting them (O'Hara -> ohara, U.S. -> us); & becomes 'and'."""
    text = _APOSTROPHES.sub('', text)
    text = _ABBREVIATION.sub(lambda m: m.group(0).replace('.', ''), text)
    text = unicodedata.normalize('NFC', text.replace('&', ' and ').lower().translate(_TRANSLIT))
    out = []
    for c in text:
        if c.isascii():
            out.append(c if c.isalnum() else '-')
        elif unicodedata.category(c)[0] == 'M':          # mark left over after NFC
            out.append(c if out and not out[-1].isascii() else '')
        else:
            base = ''.join(b for b in unicodedata.normalize('NFKD', c) if not unicodedata.combining(b))
            if base and base.isascii():                  # é -> e, ﬁ -> fi, ² -> 2, full-width letters
                out.append(''.join(b if b.isalnum() else '-' for b in base.lower()))
            elif unicodedata.category(c)[0] == 'L' and not unicodedata.name(c, '').startswith('LATIN'):
                out.append(c)                            # other scripts
            else:
                out.append('-')                          # symbols, Latin letters without an ASCII form
    return re.sub(r'-+', '-', ''.join(out)).strip('-')


def generate_filename(title, max_words=None):
    """Generate a kebab-case filename from a title using the configured AI provider.
    Falls back to filtering title words by length if the AI call fails."""
    n = max_words or FILENAME_WORDS
    ai_prompt = f'Give me a 1 to {n} word kebab-case filename (no extension) for an article titled "{title}". Drop apostrophes instead of hyphenating them (O\'Hara -> ohara). Return ONLY the filename, nothing else.'

    def _clean(raw):
        name = raw.strip().lower().splitlines()[0]
        return _slug(name)

    if title and (AI_API_KEY or AI_LLM == 'ollama'):
        try:
            name = _clean(ai_request(ai_prompt, timeout=30))
            if name:
                return name
        except Exception:
            pass

    # Fallback: try Claude CLI
    if title and shutil.which('claude'):
        try:
            result = subprocess.run(
                ['claude', '-p', ai_prompt, '--output-format', 'text']
                + (['--model', AI_FALLBACK_MODEL] if AI_FALLBACK_MODEL else []),
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30
            )
            name = _clean(result.stdout)
            if name:
                return name
        except Exception:
            pass

    # Fallback: filter words by decreasing minimum length until we get 3.
    # Transliterate accents first (e.g. "Rosé" -> "Rose") — plain
    # [a-zA-Z0-9] matching would otherwise silently truncate a word at its
    # first accented character.
    words = [w for w in _slug(title or '').split('-') if w]
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


def _image_type(path):
    """The real image type from the file's first bytes, whatever its name says:
    'jpg', 'png', 'gif', 'webp', 'avif', 'svg', 'bmp', 'tiff', 'heic', 'jxl', 'jp2', 'ico', 'psd',
    or 'pdf'/'eps' (vector figures, e.g. from LaTeX papers).
    Returns None if the file isn't an image (e.g. an HTML error page)."""
    try:
        with open(path, 'rb') as f:
            head = f.read(1024)
    except Exception:
        return None
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    if head[4:8] == b'ftyp':                     # ISO media: AVIF and HEIC/HEIF share this container
        brands = head[8:12] + head[16:min(len(head), 64)]
        if b'avif' in brands or b'avis' in brands:
            return 'avif'
        if any(b in brands for b in (b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'mif1', b'msf1')):
            return 'heic'
        return None
    if head.startswith((b'\xff\x0a', b'\x00\x00\x00\x0cJXL \r\n\x87\n')):
        return 'jxl'
    if head.startswith(b'\x00\x00\x00\x0cjP  \r\n\x87\n'):
        return 'jp2'
    if head.startswith((b'II*\x00', b'MM\x00*')):
        return 'tiff'
    if head.startswith(b'BM'):
        return 'bmp'
    if head.startswith(b'\x00\x00\x01\x00'):
        return 'ico'
    if head.startswith(b'8BPS'):
        return 'psd'
    if head.startswith(b'%PDF'):
        return 'pdf'
    if head.startswith((b'%!PS', b'\xc5\xd0\xd3\xc6')):
        return 'eps'
    text = head.lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    if text.startswith(b'<') and b'<svg' in text and b'<html' not in text:
        return 'svg'
    return None


def _is_valid_image(path):
    """Check magic bytes to confirm the file is actually an image, not an HTML error page."""
    return _image_type(path) is not None


# Shown as they are in Obsidian everywhere (Mac and iPhone): only the extension is normalised
_KEEP_IMAGE_TYPES = {'jpg', 'png', 'gif', 'svg'}
# Converted to PNG or JPG; extension used if no converter is available
_CONVERT_IMAGE_TYPES = {'bmp': 'bmp', 'tiff': 'tif', 'heic': 'heic', 'jxl': 'jxl', 'jp2': 'jp2', 'ico': 'ico', 'psd': 'psd',
                        'webp': 'webp', 'avif': 'avif', 'pdf': 'pdf', 'eps': 'eps'}


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


def _rasterize(path, kind, out):
    """Render the first page of a PDF or EPS figure to a PNG at 200 dpi (sharp on retina screens).
    PDF: poppler's pdftocairo, Ghostscript, ImageMagick or macOS sips (72 dpi); EPS: Ghostscript or
    ImageMagick (which also uses Ghostscript). Returns True on success."""
    gs = shutil.which('gs') or shutil.which('gswin64c')
    magick = shutil.which('magick') or (shutil.which('convert') if os.name != 'nt' else None)
    attempts = []
    if kind == 'pdf' and shutil.which('pdftocairo'):
        attempts.append(['pdftocairo', '-png', '-r', '200', '-singlefile', '-f', '1', '-l', '1',
                         path, os.path.splitext(out)[0]])
    if gs:
        attempts.append([gs, '-q', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-dEPSCrop', '-dUseCropBox',
                         '-dFirstPage=1', '-dLastPage=1', '-sDEVICE=png16m', '-r200', f'-sOutputFile={out}', path])
    if magick:
        # flatten onto white like a printed page: transparent figures would hide black text in dark mode
        attempts.append([magick, '-density', '200', f'{path}[0]', '-background', 'white', '-alpha', 'remove', '-alpha', 'off', out])
    if kind == 'pdf' and shutil.which('sips'):
        attempts.append(['sips', '-s', 'format', 'png', path, '--out', out])
    for cmd in attempts:
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and _image_type(out) == 'png':
            return True
    return False


def _convert_image(path, base):
    """Convert a BMP/TIFF/HEIC/JPEG XL/JPEG 2000/ICO/PSD file to base.png or base.jpg; returns the new path,
    or None without a converter (sips on macOS, else ImageMagick).

    Transparency → PNG (JPG can't store it). Otherwise both are made (and compressed, if enabled) and
    the smaller one is kept: graphics, screenshots and text compress far better as PNG, photos as JPG.
    PDF/EPS figures are first rendered to a PNG (_rasterize), which then goes through the same choice."""
    kind = _image_type(path)
    if kind in ('pdf', 'eps'):
        raster = base + '.render.png'
        if not _rasterize(path, kind, raster):
            return None
        best = _convert_image(raster, base)
        if not best:                     # no sips/ImageMagick to compare formats: the PNG is fine
            best = base + '.png'
            os.replace(raster, best)
            if ENABLE_IMAGE_COMPRESSION:
                _compress_image(best)
        os.unlink(path)
        return best
    # ImageMagick 7 is 'magick', 6 is 'convert' — but never on Windows, where convert.exe is the
    # system tool that converts FAT drives to NTFS
    magick = shutil.which('magick') or (shutil.which('convert') if os.name != 'nt' else None)
    if shutil.which('sips'):
        def to(fmt, out):
            # top quality when compression follows; otherwise a sensible quality straight away
            q = 'best' if ENABLE_IMAGE_COMPRESSION else 'high'
            opts = ['-s', 'formatOptions', q] if fmt == 'jpeg' else []
            r = subprocess.run(['sips', '-s', 'format', fmt, *opts, path, '--out', out], capture_output=True)
            return r.returncode == 0 and _image_type(out) is not None
        r = subprocess.run(['sips', '-g', 'hasAlpha', path], capture_output=True, text=True)
        transparent = 'hasAlpha: yes' in r.stdout
    elif magick:
        def to(fmt, out):
            q = '100' if ENABLE_IMAGE_COMPRESSION else '85'
            r = subprocess.run([magick, f'{path}[0]', *(['-quality', q] if fmt == 'jpeg' else []), out],
                               capture_output=True)
            return r.returncode == 0 and _image_type(out) is not None
        r = subprocess.run([magick, f'{path}[0]', '-format', '%[opaque]', 'info:'], capture_output=True, text=True)
        transparent = r.stdout.strip().lower() == 'false'
    else:
        return None

    candidates = []
    for fmt, ext in ([('png', '.png')] if transparent else [('png', '.png'), ('jpeg', '.jpg')]):
        out = base + ext
        if out != path and to(fmt, out):
            if ENABLE_IMAGE_COMPRESSION:
                _compress_image(out)
            candidates.append(out)
    if not candidates:
        return None
    best = min(candidates, key=os.path.getsize)
    for c in candidates:
        if c != best:
            os.unlink(c)
    os.unlink(path)
    return best


def finalize_image(path):
    """Give a saved image its final form and return its new path (None if it isn't an image).

    The type comes from the file's contents, not its name. JPG, PNG, SVG and GIF keep their
    format; everything else, including WebP, AVIF and PDF/EPS figures, is converted to PNG or JPG (see
    _convert_image). The extension is normalised (lowercase, .jpeg/.jfif → .jpg, real type over a
    wrong or missing one), then the image is compressed if ENABLE_IMAGE_COMPRESSION is on.
    """
    kind = _image_type(path)
    if kind is None:
        return None
    base = os.path.splitext(path)[0]
    if kind in _CONVERT_IMAGE_TYPES:
        converted = _convert_image(path, base)
        if converted:
            return converted
        needs = {'eps': 'Ghostscript (brew install ghostscript)',
                 'pdf': 'poppler (brew install poppler), Ghostscript, ImageMagick or macOS sips'}.get(kind, 'macOS sips or ImageMagick')
        print(f'WARNING: {os.path.basename(path)} is {kind.upper()} and could not be converted to PNG/JPG '
              f'(needs {needs}) — kept as is.', file=sys.stderr)
        new = f'{base}.{_CONVERT_IMAGE_TYPES[kind]}'
    else:
        new = f'{base}.{kind}'
    if new != path:
        os.replace(path, new)
    if ENABLE_IMAGE_COMPRESSION:
        _compress_image(new)
    return new


def _find_image(images_dict, src):
    """The images_dict entry for an image reference. Tries the exact path, the path without './',
    the file name, then the name without its extension (LaTeX's \\includegraphics often omits it),
    preferring formats that need no conversion."""
    if not images_dict:
        return None
    ref = src.split('?')[0].split('#')[0]
    while ref.startswith('./'):
        ref = ref[2:]
    for key in (src, ref):
        if key in images_dict:
            return images_dict[key]
    name = os.path.basename(ref)
    same_name = [k for k in images_dict if os.path.basename(k) == name]
    if same_name:
        return images_dict[min(same_name, key=len)]
    order = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.pdf', '.eps', '.ps']
    rank = lambda k: order.index(os.path.splitext(k)[1].lower()) if os.path.splitext(k)[1].lower() in order else len(order)
    for match in (lambda k: os.path.splitext(k)[0] == ref,
                  lambda k: os.path.splitext(os.path.basename(k))[0] == name):
        hits = [k for k in images_dict if match(k)]
        if hits:
            return images_dict[min(hits, key=rank)]
    return None


def download_images(content, docs_dir, filename_base, images_dict=None, source_url=None):
    """Save all images referenced in content to img/[filename_base]-NNN.ext.

    Handles two source types:
    - https:// URLs  → downloaded via curl
    - local filenames → decoded from base64 in images_dict (Datalab output)

    Each file is saved under a temporary name, then finalize_image() picks its real type,
    format and extension and compresses it.

    source_url: the original article URL, used as Referer on a retry when the
    first download fails image validation (e.g. CDN hotlink protection).

    Downloads run IMAGE_JOBS at a time. Returns updated content with rewritten img/ paths.
    """
    # The source may contain escaped parentheses (\( \)) and be followed by a "title"
    pattern = re.compile(r'!\[(.*?)\]\(((?:\\.|[^)\\])+?)(?:\s+"[^"]*")?\)', re.DOTALL)
    if not pattern.search(content):
        return content

    img_dir = os.path.join(docs_dir, 'img')
    _src = lambda m: re.sub(r'\\([()])', r'\1', m.group(2))
    # Unique sources in page order; skip inline data, already-local files, and video embeds (YouTube / vid/)
    srcs = list(dict.fromkeys(src for src in map(_src, pattern.finditer(content))
                              if not (src.startswith(('data:', 'img/', 'vid/')) or is_youtube(src))))
    if not srcs:
        return content
    # Create img/ only once there is a real image to save
    os.makedirs(img_dir, exist_ok=True)

    def fetch(i, src):
        """Save one image under a temporary name; returns its final path, or None."""
        tmp_path = os.path.join(img_dir, f'.{filename_base}-{i}.download')
        final = None
        if src.startswith('http'):
            # Download from URL
            def _curl(extra_args=()):
                r = curl(src, '-o', tmp_path, *extra_args, max_time=30)
                return (r.returncode == 0 and os.path.exists(tmp_path)
                        and os.path.getsize(tmp_path) > 0
                        and _is_valid_image(tmp_path))

            try:
                ok = _curl()
                if not ok and source_url:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    ok = _curl(['-H', f'Referer: {source_url}', '-A', BROWSER_UA])
                if ok:
                    final = finalize_image(tmp_path)
            except Exception:
                final = None
        elif images_dict:
            # Decode from base64 dict (Datalab or arXiv)
            # Exact path first, then file name, then name without extension (see _find_image)
            raw = _find_image(images_dict, src)
            if raw is not None:
                try:
                    data = raw.split(',', 1)[1] if ',' in raw else raw
                    with open(tmp_path, 'wb') as f:
                        f.write(base64.b64decode(data))
                    final = finalize_image(tmp_path)
                except Exception:
                    final = None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)   # failed download, or not an image
        return final

    with ThreadPoolExecutor(max(1, IMAGE_JOBS)) as ex:
        finals = list(ex.map(fetch, range(len(srcs)), srcs))

    # Number the saved images in page order; failed ones keep their link and leave no gap
    seen = {}   # original src → local_name
    for src, final in zip(srcs, finals):
        if final:
            seen[src] = f'{filename_base}-{len(seen) + 1:03d}{os.path.splitext(final)[1]}'
            os.replace(final, os.path.join(img_dir, seen[src]))
    content = pattern.sub(lambda m: f'![{m.group(1)}](img/{seen[_src(m)]})' if _src(m) in seen else m.group(0),
                          content)
    saved = [os.path.join(img_dir, n) for n in seen.values()]
    if saved and ENABLE_IMAGE_COMPRESSION and GRAYSCALE_FIGURES:
        for old, new in optimize_grayscale_figures(saved).items():
            content = content.replace(f'(img/{os.path.basename(old)})', f'(img/{os.path.basename(new)})')
    # Don't leave an empty img/ behind when every download failed
    if os.path.isdir(img_dir) and not os.listdir(img_dir):
        os.rmdir(img_dir)
    return content


def html_to_markdown(html_str):
    """Convert HTML to rough markdown, preserving headings, code blocks, and paragraph breaks.

    If the HTML already contains raw markdown (detected via backtick fences), all HTML→markdown
    conversions are skipped — headings, lists, code blocks are already correct. Only HTML tags
    are stripped and entities unescaped. This handles sites that embed pre-rendered markdown
    for bots (e.g. VitePress preload divs) and avoids duplicate/conflicting content.
    """
    # Always remove noise elements first (head strips <title> text, nav/header/footer strips chrome)
    html_str = re.sub(r'<(head|style|script|nav|header|footer)[^>]*>.*?</\1>', '', html_str, flags=re.DOTALL | re.IGNORECASE)

    # Finished code blocks are set aside until the end: once unescaped, text like ">_<" in a
    # plain-text guide would otherwise be taken for a tag and everything up to the next ">" stripped
    fences = []

    def stash_fence(m):
        fences.append('\n\n```\n' + html.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip() + '\n```\n')
        return f'\x00{len(fences) - 1}\x00'

    # Only fences outside <pre> mean the page embeds raw markdown; a ``` inside a code sample doesn't
    if '```' in re.sub(r'<pre[^>]*>.*?</pre>', '', html_str, flags=re.DOTALL | re.IGNORECASE):
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
            stash_fence, html_str, flags=re.DOTALL | re.IGNORECASE
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
    html_str = re.sub(r'\x00(\d+)\x00', lambda m: fences[int(m.group(1))], html_str)
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
        result = curl(url, *(['-A', user_agent] if user_agent else []), max_time=25)
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
        result = curl(url, '-A', BROWSER_UA, max_time=25)
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


_TABLE_SEP = re.compile(r'^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$')
_LIST_START = re.compile(r'([-*+]|\d+[.)])\s')


def tidy_source_md(content):
    """Repair hand-written site markdown that Obsidian won't render (e.g. Databento docs):
    tables without a header separator row, links padded as [ text ]( url ), and
    paragraph continuation lines with stray leading spaces."""
    content = re.sub(r'\[[ \t]+([^\]\n]*?)[ \t]*\]\([ \t]*([^\s)]+)[ \t]*\)', r'[\1](\2)', content)
    content = re.sub(r'\[([^\]\n]*?)[ \t]+\]\(([^\s)]+)\)', r'[\1](\2)', content)
    out, in_fence = [], False
    for line in content.split('\n'):
        prev = out[-1] if out else ''
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        elif not in_fence:
            # First row of a table block: add the separator row if the source has none
            if (prev.startswith('|') and not (len(out) > 1 and out[-2].startswith('|'))
                    and line.startswith('|') and not _TABLE_SEP.match(line)):
                out.append('| ' + ' | '.join('---' for _ in prev.strip().strip('|').split('|')) + ' |')
            # Indented continuation of a plain paragraph line
            elif (line[:1] == ' ' and line.strip() and prev.strip() and prev[:1] not in ' \t|>#'
                    and not _LIST_START.match(prev) and not _LIST_START.match(line.lstrip())):
                line = line.lstrip()
        out.append(line)
    return '\n'.join(out)


def fetch_direct_md(url):
    """Fetch the page's raw .md source: {page}.md (Databento, ReadMe e.g. Alpaca, and other docs
    platforms serve it there, linked or not), else Obsidian Publish's preloadPage source.
    Resolves wikilink images using the same base path. Returns a defuddle-style dict or None."""
    try:
        result = curl(url, '-w', '\n%{url_effective}', max_time=25)
        raw, _, final_url = result.stdout.decode('utf-8', errors='replace').rpartition('\n')

        # Only the page's own source: docs sites also link other pages' .md files (and paywall
        # redirect pages random GitHub/CDN ones). Error pages are rejected below.
        original_netloc = urlparse(url).netloc
        own_md = (final_url or url).split('#')[0].split('?')[0].rstrip('/') + '.md'
        publish_md = re.findall(r'preloadPage=\w+\("(https://[^"]+\.md)"\)', raw)

        # Fetch the raw .md file (an HTML error page is not markdown)
        for md_url in [own_md] + publish_md[:1]:
            md_result = curl(md_url, '-w', '\n%{http_code} %{content_type}', max_time=25)
            body, _, status = md_result.stdout.decode('utf-8', errors='replace').rpartition('\n')
            if status.startswith('200') and 'html' not in status:
                break
        else:
            return None
        content = tidy_source_md(re.sub(r'\A---\n.*?\n---\n', '', body.strip(), flags=re.S))
        if not content or len(content.split()) < MIN_WORDS:
            return None
        # A page built mostly from code (e.g. Databento's API reference) can have a .md source
        # with only a snippet: then the page itself is the better source
        page_text = re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style|svg)\b.*?</\1>', ' ', raw))
        if len(content.split()) < 0.1 * len(page_text.split()):
            return None
        # Links to sibling pages' .md sources → the pages themselves
        if md_url == own_md:
            content = re.sub(r'(\]\(https?://' + re.escape(original_netloc) + r'/[^)\s#]+?)\.md(?=[)#])',
                             r'\1', content)

        # Resolve ![[image.ext]] wikilinks using base path + Assets/
        base_m = re.match(r'(https?://[^/]+/[^/]+/[a-f0-9]+/)', md_url)
        if base_m:
            assets_base = base_m.group(1) + 'Assets/'
            content = re.sub(
                r'!\[\[([^\]]+\.(?:png|jpg|jpeg|gif|webp|svg|avif))\]\]',
                lambda m: f'![{m.group(1)}]({assets_base}{quote(m.group(1), safe="")})',
                content, flags=re.IGNORECASE
            )

        # Extract title from first # heading outside code blocks (a "# comment" in code is not one)
        title_m = next((m for m in re.finditer(r'^#\s+(.+)$', content, re.MULTILINE)
                        if content[:m.start()].count('```') % 2 == 0), None)
        title = title_m.group(1).strip() if title_m else unquote(md_url.rsplit('/', 1)[1][:-3])
        # Strip the leading # heading from content (build_article re-adds it), plus a short
        # preamble before it (e.g. "Source: {url}"), but never real body text
        if title_m:
            before = content[:title_m.start()]
            rest = content[title_m.end():].lstrip()
            content = before.rstrip() + '\n\n' + rest if len(before.split()) > 10 else rest

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
        result = curl(api, max_time=10, text=True, follow=False)
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
        result = curl(f'https://archive.is/newest/{url}', '-I', max_time=10, text=True, follow=False)
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
        result = curl('https://archive.is/submit/', '-D', '-', '-o', '/dev/null', '-X', 'POST', '-d', f'url={quote(url, safe="")}&anyway=1', max_time=85, text=True, follow=False)
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
        return _with_archive(fetch_html_as_markdown(archive_url, original_url=url), archive_url), 'wayback'
    elif method == 'archive-is':
        archive_url = archive_is_snapshot(url)
        if not archive_url:
            archive_url = archive_is_save(url)
        if archive_url:
            d = fetch_defuddle(archive_url)
            if d:
                d = clean_archive_content(d, url)
            return _with_archive(d, archive_url), 'archive-is'
        print(
            f'archive.is requires a captcha for this URL.\n'
            f'Please visit this URL, save the page, and solve the captcha:\n'
            f'https://archive.is/?url={quote(url, safe=":/?")}',
            file=sys.stderr
        )
        return {}, 'archive-is'
    else:
        return {}, method


# Scans above 160 dpi go to 150 dpi at JPEG quality 75, fonts are trimmed to the characters used.
# Forms and protected files are left alone; the result is only used if pages and text are unchanged.
_GRAY_FIGURES = r'''
import json, os, subprocess, sys
import numpy as np
from PIL import Image

def box(a, r=3):
    p = np.pad(a, r + 1, mode='edge').cumsum(0).cumsum(1); k = 2 * r + 1
    s = p[k:, k:] - p[:-k, k:] - p[k:, :-k] + p[:-k, :-k]
    return s[:a.shape[0], :a.shape[1]] / (k * k)

out = {}
for path in sys.argv[1:]:
    try:
        im = Image.open(path)
        if im.mode in ('RGBA', 'LA', 'P') and 'transparency' in im.info or im.mode in ('RGBA', 'LA'):
            continue                                        # keep transparency as it is
        rgb = np.asarray(im.convert('RGB'), dtype=np.float64)
        if np.percentile(np.ptp(rgb, axis=2), 99) > 24:
            continue                                        # a colour image
        g = rgb.mean(axis=2)
        clean = np.where(g > 225, 255, np.where(g < 30, 0, g))   # scan noise to pure white / black
        mean = box(g); std = np.sqrt(np.maximum(box(g * g) - mean ** 2, 0))
        smooth = (g > 40) & (g < 215) & (std < 6)            # grey areas without edges: where bands would show
        if ((g > 40) & (g < 215)).mean() > 0.6 and smooth.mean() < 0.1:
            continue                                        # mostly textured grey: a photo, not a figure
        base = Image.fromarray(clean.astype(np.uint8), 'L')
        tmp = os.path.splitext(path)[0] + '.gray.png'
        for levels in (8, 32):
            q = base.quantize(colors=levels, dither=Image.Dither.NONE)
            band = float(np.abs(np.asarray(q.convert('L'), dtype=np.float64) - mean)[smooth].mean()) if smooth.sum() > 500 else 0.0
            if smooth.mean() <= 0.03 or band <= 3.0:
                q.save(tmp, optimize=True)
                subprocess.run(['pngquant', '--force', '--skip-if-larger', '--speed', '1', '--quality', '90-100',
                                '--ext', '.png', tmp], capture_output=True)
                break
        else:
            continue                                        # a photo: bands even with 32 greys
        if os.path.getsize(tmp) > 2 * os.path.getsize(path):
            os.remove(tmp); continue
        final = os.path.splitext(path)[0] + '.png'
        os.replace(tmp, final)
        if final != path:
            os.remove(path)
        out[path] = final
    except Exception:
        continue
print(json.dumps(out))
'''

def optimize_grayscale_figures(paths):
    """Grey images (scanned graphs, line art) as PNG with 8 grey levels, or 32 when 8 would show bands
    in smooth shading; colour images, photos and transparent images are left alone. One uv run for
    all paths (Pillow + numpy in isolation). Returns {old path: new path} for the images that changed."""
    if not paths or not shutil.which('uv'):
        return {}
    try:
        r = subprocess.run(['uv', 'run', '--no-project', '--quiet', '--with', 'pillow', '--with', 'numpy',
                            'python', '-c', _GRAY_FIGURES, *paths], capture_output=True, text=True, timeout=600)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}

_PDF_OCR = '''
import sys, pymupdf
pymupdf.TOOLS.mupdf_display_errors(False)
src, out, tessdata = sys.argv[1:4]
doc = pymupdf.open(src)
font = pymupdf.Font('helv')
done = 0
for page in doc:
    if len(page.get_text('words')) >= 50:       # the page already has a text layer
        continue
    tp = page.get_textpage_ocr(language='eng', dpi=300, full=True, tessdata=tessdata)
    words = page.get_text('words', textpage=tp)
    if not words:
        continue
    tw = pymupdf.TextWriter(page.rect)          # all of a page's text in one go keeps the file small
    for x0, y0, x1, y1, word, *_ in words:
        w = font.text_length(word, fontsize=1)
        size = min((x1 - x0) / w if w else y1 - y0, (y1 - y0) * 1.1)
        tw.append((x0, y1 - (y1 - y0) * 0.2), word, font=font, fontsize=size)
    tw.write_text(page, render_mode=3)          # invisible: searchable and selectable only
    done += 1
if done:
    doc.save(out, garbage=4, deflate=True, clean=True)
print(done)
'''

def _tessdata_dir():
    """Folder with Tesseract's language data (TESSDATA_PREFIX, else what tesseract reports), or None."""
    if os.environ.get('TESSDATA_PREFIX'):
        return os.environ['TESSDATA_PREFIX']
    try:
        out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=20)
        m = re.search(r'"([^"]+)"', out.stdout + out.stderr)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None

def _ocr_pdf(path):
    """Add an invisible OCR text layer to the pages of a PDF that have no text (scans), in place.
    The scan images are left untouched, so the file only grows by the text (a few KB per page)."""
    words = _pdf_text_words(path) or 0
    try:
        pages = int(re.search(r'^Pages:\s+(\d+)', subprocess.run(['pdfinfo', path], capture_output=True,
                                                                text=True).stdout, re.M).group(1))
    except Exception:
        pages = 0
    if pages and words / pages >= 50:
        return                                          # has a text layer already
    if not shutil.which('tesseract') or not shutil.which('uv'):
        print('The PDF has no text layer; install tesseract (and uv) to add one with OCR_PDF.', file=sys.stderr)
        return
    tessdata = _tessdata_dir()
    if not tessdata:
        return
    print('Adding an OCR text layer to the PDF...', file=sys.stderr, flush=True)
    out = path + '.ocr.pdf'
    try:
        r = subprocess.run(['uv', 'run', '--no-project', '--quiet', '--with', 'pymupdf', 'python', '-c', _PDF_OCR,
                            path, out, tessdata], capture_output=True, text=True, timeout=3600)
        done = int((r.stdout.strip().splitlines() or ['0'])[-1])
        if done and os.path.exists(out):
            os.replace(out, path)
            print(f'Added an OCR text layer to {done} page{"s" if done != 1 else ""}', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    finally:
        if os.path.exists(out):
            os.remove(out)

_PDF_COMPRESS = '''
import sys, pymupdf
src, out = sys.argv[1:3]
pymupdf.TOOLS.mupdf_display_errors(False)
doc = pymupdf.open(src)
if doc.needs_pass or doc.is_encrypted or doc.is_form_pdf:
    sys.exit(3)
doc.rewrite_images(dpi_threshold=160, dpi_target=150, quality=75)
doc.subset_fonts()
doc.save(out, garbage=4, deflate=True)
a, b = pymupdf.open(src), pymupdf.open(out)
if len(a) != len(b) or any(x.get_text() != y.get_text() for x, y in zip(a, b)):
    sys.exit(4)
'''


def _compress_pdf(path):
    """Compress a saved PDF in place with PyMuPDF, run through uv so nothing has to be installed.
    Skips silently without uv, and keeps the original if the result isn't smaller."""
    if not shutil.which('uv'):
        return
    path = os.path.abspath(path)
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'compressed.pdf')
        try:
            r = subprocess.run(['uv', 'run', '--no-project', '--quiet', '--with', 'pymupdf', 'python', '-c', _PDF_COMPRESS, path, out],
                               capture_output=True, cwd=tmp, timeout=600)
        except Exception:
            return
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) < os.path.getsize(path):
            before, after = os.path.getsize(path), os.path.getsize(out)
            shutil.move(out, path)
            print(f'Compressed the PDF: {before / 1e6:.1f} → {after / 1e6:.1f} MB')


def save_original_pdf(save_dir, filename, source=None, meta=None):
    """--keep-pdf: save the original PDF next to the note as {filename}.pdf.
    source is the PDF's URL or local path; without one, the PDF build_paper found (meta) is used."""
    meta = meta or {}
    data = None if source else meta.get('pdf')
    source = source or meta.get('pdf_url')
    if data is None and source and os.path.isfile(source):
        with open(source, 'rb') as f:
            data = f.read()
    elif data is None and source:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'original.pdf')
            try:
                curl(source, '-o', path, max_time=120)
                with open(path, 'rb') as f:
                    data = f.read()
            except Exception:
                data = None
    if not data or b'%PDF' not in data[:1024]:
        print('WARNING: the original PDF could not be saved — the note was saved without it.', file=sys.stderr)
        return
    pdf_path = os.path.join(save_dir, f'{filename}.pdf')
    with open(pdf_path, 'wb') as f:
        f.write(data)
    print(f'Saved the original PDF as {filename}.pdf next to the note')
    if OCR_PDF:
        _ocr_pdf(pdf_path)
    if ENABLE_PDF_COMPRESSION:
        _compress_pdf(pdf_path)


def run_batch(urls, argv):
    """Process several links, PARALLEL_JOBS at a time, each in its own defuddle process.
    Progress lines are prefixed with the link's number; each link's output follows when it is done."""
    rest, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a == '--url':
            skip = True
        elif not a.startswith('--url=') and a not in urls:
            rest.append(a)
    print(f'Processing {len(urls)} links, {PARALLEL_JOBS} at a time...', file=sys.stderr, flush=True)

    def one(n, url):
        with tempfile.TemporaryFile('w+', encoding='utf-8') as out:
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), url] + rest,
                                 stdout=out, stderr=subprocess.PIPE, text=True)
            for line in p.stderr:
                print(f'[{n}] {line}', end='', file=sys.stderr, flush=True)
            p.wait()
            out.seek(0)
            return p.returncode, out.read()

    with ThreadPoolExecutor(max(1, PARALLEL_JOBS)) as ex:
        futures = [ex.submit(one, n, u) for n, u in enumerate(urls, 1)]
        codes = []
        for n, (url, fut) in enumerate(zip(urls, futures), 1):
            code, out = fut.result()
            codes.append(code)
            print(f'\n=== [{n}] {url}{"" if code == 0 else f" (failed, exit {code})"} ===')
            print(out.strip())
    return 0 if all(c == 0 for c in codes) else 1


def in_background(fn, *args, **kwargs):
    """Start fn(*args, **kwargs) in a thread and return its Future (.result() waits for it).
    Used to run the filename AI call while the note is being built."""
    ex = ThreadPoolExecutor(1)
    future = ex.submit(fn, *args, **kwargs)
    ex.shutdown(wait=False)
    return future


def read_only(args):
    """No --project and no --category: print the content instead of saving it."""
    return args.project is None and args.category is None


def print_note(note):
    """Print a built note without its frontmatter (read-only mode)."""
    print(re.sub(r'^---\n.*?\n---\n\n?', '', note, count=1, flags=re.DOTALL).strip())


def save_note(note, args, subdir, filename, images_dict=None, source_url=None, finish=None):
    """Write a note to {vault}/[category/][project/]{subdir}/{filename}.md, with its images in img/.
    finish(note, save_dir) can change the note just before it is written. Returns the folder."""
    save_dir = get_save_dir(get_vault_path(), args.category, args.project, subdir)
    os.makedirs(save_dir, exist_ok=True)
    if ENABLE_IMAGES:
        note = download_images(note, save_dir, filename, images_dict=images_dict, source_url=source_url)
    if finish:
        note = finish(note, save_dir)
    note = _dedup_note_tags(note)
    with open(os.path.join(save_dir, f'{filename}.md'), 'w', encoding='utf-8') as f:
        f.write(note)
    print(f'Saved to {format_saved_path(args.category, args.project, subdir, filename)}')
    return save_dir


def paper_filename(args, meta):
    """{first author's last name}-{AI filename from the title}, unless --filename is given."""
    if args.filename:
        return args.filename
    authors   = meta.get('authors', [])
    last_name = _slug(authors[0].split()[-1]) if authors else ''
    ai_name   = generate_filename(meta.get('title', ''))
    return f'{last_name}-{ai_name}' if last_name else ai_name


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('url_pos',    nargs='*', default=[], metavar='URL')
    parser.add_argument('--url',      default=None)
    parser.add_argument('--project',  default=None)
    parser.add_argument('--category', default=None, help=argparse.SUPPRESS)   # optional parent folder above --project
    parser.add_argument('--created',  default=None)
    parser.add_argument('--filename', default=None)
    parser.add_argument('--method',   default=None, choices=['native', 'native-md', 'native-page', 'googlebot', 'wayback', 'archive-is'])
    _num = lambda unit: (lambda s: int(re.sub(rf'(?i){unit}$', '', s.strip())))
    parser.add_argument('--download', action='store_true',
                        help='YouTube: also save the video to vid/ and embed the local file')
    parser.add_argument('--video-quality', type=_num('p'), default=None, metavar='HEIGHT',
                        help='max video height, e.g. 720 or 720p; 0 = audio only, saved to aud/ '
                             '(implies --download; default: highest)')
    parser.add_argument('--audio-quality', type=_num('k'), default=None, metavar='KBPS',
                        help='max audio bitrate in kbps, e.g. 64 or 64k; 0 = video without sound '
                             '(implies --download; default: highest)')
    parser.add_argument('--doi', default=None,
                        help='PDFs: the paper\'s DOI when it is known (e.g. a PDF saved from a shadow library); '
                             'saves it as a paper without searching')
    parser.add_argument('--keep-pdf', action=argparse.BooleanOptionalAction, default=None,
                        help='papers and PDFs: also save the original PDF next to the note, with the same name '
                             '(default: KEEP_PDF)')
    args = parser.parse_args(argv)
    if args.video_quality == args.audio_quality == 0:
        parser.error('--video-quality and --audio-quality cannot both be 0')
    # 0 is a valid value (skip that track), so test for None rather than truthiness
    if args.video_quality is not None or args.audio_quality is not None:
        args.download = True
    if args.keep_pdf is None:
        args.keep_pdf = KEEP_PDF
    if not args.created:
        args.created = datetime.now().date().isoformat()
    args.urls = ([args.url] if args.url else []) + args.url_pos
    if len(args.urls) > 1 and args.filename:
        parser.error('--filename works with one link at a time')
    if not args.urls:
        parser.error('URL required as first argument or via --url')
    return args


def route_academia(url, args):
    """Academia.edu: save the page's PDF, or continue with the paper's DOI through the shadow library.
    Returns (url to continue with or None when done, the paper's DOI if found)."""
    print('Detected Academia.edu page — extracting PDF URL...', file=sys.stderr)
    pdf_url, academia_doi = extract_academia_pdf_url(url)
    if pdf_url:
        print(f'Found PDF: {pdf_url}', file=sys.stderr)
        note, images_dict, meta = build_pdf(pdf_url, args.project, args.created, skip_doi=True)
        if not note:
            return None, academia_doi
        if read_only(args):
            print_note(note)
            return None, academia_doi
        filename = args.filename or generate_filename(meta.get('title', ''))
        save_dir = save_note(note, args, 'docs', filename, images_dict, source_url=pdf_url)
        if args.keep_pdf:
            save_original_pdf(save_dir, filename, source=pdf_url)
        return None, academia_doi
    if ENABLE_PAPER and academia_doi and SHADOW_BASE_URL:
        print(f'PDF not publicly available — trying shadow library for DOI {academia_doi}...', file=sys.stderr)
        # Continue with the paper pipeline
        return SHADOW_BASE_URL.rstrip('/') + '/' + academia_doi, academia_doi
    msg = 'PDF requires login.'
    if academia_doi:
        msg += f' DOI: {academia_doi}'
    msg += ' Download the PDF manually and pass the local file location to defuddle.'
    print(msg, file=sys.stderr)
    return None, academia_doi


def paper_url(url, raw_url):
    """Rewrite paper links (SSRN, arXiv PDF/HTML, publisher and index pages, shadow library,
    doi.org variants) to the form the paper pipeline expects. Returns None to stop."""
    if not ENABLE_PAPER:
        return url
    # SSRN: derive DOI from abstract_id
    if is_ssrn_url(url):
        ssrn_doi = extract_ssrn_doi(url)
        if not ssrn_doi:
            print('Could not extract abstract_id from SSRN URL.', file=sys.stderr)
            return None
        print(f'Detected SSRN paper — using DOI {ssrn_doi}...', file=sys.stderr)
        url = f'https://doi.org/{ssrn_doi}'

    # arXiv PDF and HTML links: use the abstract page, so the LaTeX source route is tried
    m = re.match(r'https?://(?:www\.)?arxiv\.org/(?:pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?$', raw_url)
    if m:
        url = f'https://arxiv.org/abs/{m.group(1)}'

    # Publisher and paper-index pages (ScienceDirect, Wiley, PubMed, ...): find the DOI
    elif is_paper_site(raw_url):
        pub_doi = paper_site_doi(raw_url)
        if pub_doi:
            print(f'Detected paper page — using DOI {pub_doi}...', file=sys.stderr)
            url = f'https://doi.org/{pub_doi}'
        else:
            print('Paper page detected but no DOI found (the site may block scripts); '
                  'pass the DOI link instead. Reading it as an article...', file=sys.stderr)

    # Normalize shadow library URLs to canonical doi.org form
    if is_shadow_url(url):
        doi_part = extract_doi_from_shadow(url)
        if doi_part:
            url = f'https://doi.org/{doi_part}'
        else:
            print('Shadow library URL detected but no DOI found in path.', file=sys.stderr)

    # Normalize doi.org URLs to canonical form (strip dx., www., etc.)
    if 'doi.org' in url and 'arxiv.org' not in url:
        doi_part = extract_doi(url)
        if doi_part:
            url = f'https://doi.org/{doi_part}'
    return url


def save_paper(url, args, academia_doi=None, pdf_file=None, converted=None, pdf_source=None):
    """Academic papers (DOI / arXiv / shadow library) → papers/. pdf_file and converted reuse a PDF
    and its conversion that the caller already has; pdf_source is where that PDF came from."""
    if read_only(args):
        note, _, _ = build_paper(url, None, args.created, pdf_file=pdf_file, converted=converted)
        if note:
            print_note(note)
        return
    note, images_dict, meta = build_paper(url, args.project, args.created, pdf_file=pdf_file, converted=converted)
    if not note:
        if academia_doi:
            print(f'Paper not found in shadow library either. Download the PDF manually and pass the local file location to defuddle. DOI: {academia_doi}', file=sys.stderr)
        return
    filename = paper_filename(args, meta)
    save_dir = save_note(note, args, 'papers', filename, images_dict, source_url=pdf_source or url)
    if args.keep_pdf:
        if pdf_source and not meta.get('pdf'):
            save_original_pdf(save_dir, filename, source=pdf_source)
        else:
            save_original_pdf(save_dir, filename, meta=meta)


def save_pdf(url, args):
    """Plain PDFs (links and local files): scan the first pages for a DOI and continue as a paper
    when there is one, else save the PDF's text to docs/."""
    if args.doi:   # DOI given: no need to search for it
        doi = re.sub(r'^(https?://(dx\.)?doi\.org/|doi:)', '', args.doi.strip(), flags=re.I)
        note, images_dict, meta = None, None, {'redirect_doi': doi}
        if os.path.isabs(url):
            with open(url, 'rb') as f:
                meta['pdf_bytes'] = f.read()
    else:
        note, images_dict, meta = build_pdf(url, args.project, args.created)

    if meta and meta.get('redirect_doi'):
        # First page had a DOI — continue as a paper, reusing the PDF we already have
        pdf_file = None
        if meta.get('pdf_bytes'):
            fd, pdf_file = tempfile.mkstemp(suffix='.pdf')
            with os.fdopen(fd, 'wb') as f:
                f.write(meta['pdf_bytes'])
        try:
            save_paper(f'https://doi.org/{meta["redirect_doi"]}', args, pdf_file=pdf_file,
                       converted=meta.get('converted'), pdf_source=url)
        finally:
            if pdf_file:
                os.remove(pdf_file)
        return
    if not note:
        return
    if read_only(args):
        print_note(note)
        return
    filename = args.filename or generate_filename(meta.get('title', ''))
    save_dir = save_note(note, args, 'docs', filename, images_dict, source_url=url)
    if args.keep_pdf:
        if meta and meta.get('pdf'):
            save_original_pdf(save_dir, filename, meta=meta)
        else:
            save_original_pdf(save_dir, filename, source=url)


def save_podcast(url, args):
    """Apple Podcasts: local TTML transcript, no HTTP cascade needed → transcripts/."""
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
    if read_only(args):
        for chunk in parse_ttml(ttml_path):
            speaker_label = re.sub(r'SPEAKER_(\d+)', r'Speaker \1', chunk['speaker'])
            print(f'[{fmt_tc(chunk["time"])}] **{speaker_label}:** {chunk["text"]}')
        return
    meta  = get_podcast_metadata(episode_id, apple_show_id(url))
    show  = meta.get('show', '')
    title = meta.get('title', '')
    # Strip words containing digits (e.g. "S10E5", "256") from episode title
    cleaned_title = ' '.join(w for w in title.split() if not re.search(r'\d', w)).strip()
    show_slug = in_background(generate_filename, show, max_words=2) if show and not args.filename else None
    ep_slug   = in_background(generate_filename, cleaned_title or title) if not args.filename else None
    # Canonical URL: keep only ?i= param
    clean_pod_url = re.sub(r'[?&]i=(\d+).*', r'?i=\1', url)
    note = build_apple_podcast(clean_pod_url, args.project, args.created, ttml_path, meta=meta)
    filename = args.filename or (f'{show_slug.result()}-{ep_slug.result()}' if show_slug else ep_slug.result())
    save_note(note, args, 'transcripts', filename, source_url=url)


_ERROR_TITLE = re.compile(
    r"^\s*(?:error\s*)?404\b|\b404\s*(?:error|not found)|\berror\s*404\b|^\s*not found\b|\bno longer available\b|"
    r"\b(?:page|file|document)\s+(?:not found|not available|unavailable|(?:does not|doesn['’]t) exist|"
    r"(?:could not|cannot|can['’]t) be found|(?:has been|was) (?:moved|removed|retired|deleted))", re.I)


def usable(d):
    """A fetch result worth keeping: enough words, and not an error page served with a normal
    status (title "Page Not Found", "Page Not Available", ...), which the next method may get past."""
    return bool(d) and d.get('wordCount', 0) > MIN_WORDS and not _ERROR_TITLE.search(d.get('title') or '')


def fetch_article(url):
    """Auto-cascade: native-md → native-page → googlebot → wayback → archive-is.
    Returns (d, used_method, target): target is the paper or PDF to continue with when the page
    is a scholarly landing page, else None."""
    # 1. native-md: the page's own .md source (docs platforms, Obsidian Publish)
    candidate = fetch_direct_md(url)
    if usable(candidate):
        return candidate, 'native-md', None

    # 2. native-page: defuddle parse
    candidate = fetch_defuddle(url)
    # Scholarly pages (repositories, preprint servers, ...): continue as the paper or its PDF
    target = academic_target(candidate.get('metaTags'), url) if ENABLE_PAPER else None
    if target and target != url:
        return {}, None, target
    if usable(candidate):
        return candidate, 'native-page', None

    # 3. googlebot: Googlebot UA + script-strip
    candidate = fetch_googlebot(url)
    if usable(candidate):
        candidate = dict(candidate)
        candidate['site'] = re.sub(r'^www\.', '', urlparse(url).netloc)
        return candidate, 'googlebot', None

    # 4. Archive fallbacks
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
            if usable(candidate):
                return _with_archive(candidate, archive_url), method_name, None

    print(
        f'No usable copy found: the page appears JS-rendered or shows an error page, and no archive\n'
        f'snapshot could be retrieved.\n'
        f'Please visit this URL, save the page, and solve the captcha:\n'
        f'https://archive.is/?url={quote(url, safe=":/?")}',
        file=sys.stderr
    )
    return {}, None, None


def save_web(url, args, d, used_method):
    """Web pages and YouTube videos → docs/ or transcripts/."""
    youtube = ENABLE_YOUTUBE and is_youtube(url)
    if read_only(args):
        if args.download:
            print('--download only works when saving (--project/--category) — skipped.', file=sys.stderr)
        if youtube:
            present_youtube(d, url)
        else:
            print(d.get('content', ''), end='')
        return

    # Don't save an empty note
    if d.get('wordCount', 0) <= MIN_WORDS and not youtube:
        print('No content retrieved — skipping save.', file=sys.stderr)
        return

    title = d.get('title', '') or title_from_content(d.get('content', ''))
    filename = None if args.filename else in_background(generate_filename, title)
    if youtube:
        note = build_youtube(d, url, args.project, args.created)
    else:
        note = build_article(d, url, args.project, args.created, method=used_method)
    filename = args.filename or filename.result()

    def finish(note, save_dir):
        if not (args.download and youtube):
            return note
        local = download_youtube(url, save_dir, filename, args.video_quality, args.audio_quality)
        if not local:
            print('WARNING: download failed after 2 attempts — nothing saved locally; '
                  'the note keeps the YouTube embed.', file=sys.stderr)
            return note
        # Swap the YouTube embed for the local file; the URL stays in the frontmatter
        return re.sub(r'^!\[\]\(https://youtu\.be/[^)]+\)$', f'![]({local})', note, count=1, flags=re.M)

    save_note(note, args, 'transcripts' if is_youtube(url) else 'docs', filename, source_url=url, finish=finish)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parse_args(argv)
    if len(args.urls) > 1:
        sys.exit(run_batch(args.urls, argv))
    raw_url = args.urls[0]

    _is_special = (
        os.path.isabs(raw_url) or
        (ENABLE_PAPER   and is_paper_url(raw_url)) or
        (ENABLE_PODCAST and is_apple_podcast(raw_url)) or
        (ENABLE_YOUTUBE and is_youtube(raw_url))
    )
    url = raw_url if _is_special else clean_url(raw_url)

    academia_doi = None
    if is_academia_url(url):
        url, academia_doi = route_academia(url, args)
        if not url:
            return
    url = paper_url(url, raw_url)
    if not url:
        return

    if ENABLE_PAPER and is_paper_url(url):
        return save_paper(url, args, academia_doi)
    if is_pdf_url(url):
        return save_pdf(url, args)
    if ENABLE_PODCAST and is_apple_podcast(url):
        return save_podcast(url, args)

    # YouTube: defuddle's page parse plus yt-dlp, no archive cascade. A video without subtitles
    # has only a few words but isn't JS-rendered, and archives don't have its transcript either
    if ENABLE_YOUTUBE and is_youtube(url):
        d, used_method = fetch_youtube(url), 'native-page'
        if not d:
            print('Could not read this video (defuddle and yt-dlp both failed).', file=sys.stderr)
            return
    elif args.method:
        d, used_method = fetch_for_method(url, args.method)
    else:
        d, used_method, target = fetch_article(url)
        if target:
            kind = 'DOI ' + target.split('doi.org/', 1)[1] if 'doi.org/' in target else 'its PDF'
            print(f'Scholarly page detected — using {kind}...', file=sys.stderr)
            return main([target if a == raw_url else a for a in argv])
    save_web(url, args, d, used_method)

if __name__ == '__main__':
    main()
