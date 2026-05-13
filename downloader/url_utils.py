"""URL validation, cleaning, and shell argument rejoining."""

from downloader.config import YOUTUBE_URL_PATTERNS


def validate_url(url):
    """Basic validation of YouTube URL."""
    return any(pattern in url for pattern in YOUTUBE_URL_PATTERNS)


def clean_url(url):
    """Strip surrounding quotes and whitespace from URL.

    Handles matched quotes, unmatched trailing/leading quotes,
    and partial quoting (e.g., URL copied with a stray end-quote).
    """
    url = url.strip()
    if (url.startswith('"') and url.endswith('"')) or \
       (url.startswith("'") and url.endswith("'")) or \
       (url.startswith("`") and url.endswith("`")):
        url = url[1:-1]
    else:
        while url and url[0] in ('"', "'", "`"):
            url = url[1:]
        while url and url[-1] in ('"', "'", "`"):
            url = url[:-1]
    url = url.strip()
    return url


def resolve_args(args):
    """Pre-process command line arguments to handle unquoted YouTube URLs.

    When URLs contain '&' characters and are passed without quotes,
    the shell splits them into separate tokens. This function detects
    and re-joins such fragments back into a single URL.

    Examples handled:
        python main.py https://youtu.be/abc&list=xyz --audio-only
        -> URL is 'https://youtu.be/abc&list=xyz'

        python main.py https://youtube.com/watch?v=abc&t=120 --format mp4
        -> URL is 'https://youtube.com/watch?v=abc&t=120'

        python main.py https://youtu.be/abc" --audio-only
        -> URL is 'https://youtu.be/abc' (strips trailing quote)
    """
    if not args:
        return args

    quote_chars = set('"\'`')

    def strip_quotes(s):
        """Remove leading and trailing quote characters from a string."""
        if not s:
            return s
        while s and s[0] in quote_chars:
            s = s[1:]
        while s and s[-1] in quote_chars:
            s = s[:-1]
        search_start = len(s) - 1
        for pos in range(search_start, max(len(s) - 32, 0), -1):
            if s[pos] in quote_chars:
                s = s[:pos]
                break
            if s[pos] in ('&', '='):
                break
        for pos in range(min(5, len(s) - 1), -1, -1):
            if s[pos] in quote_chars:
                s = s[pos + 1:]
                break
        return s.strip()

    def looks_like_url_start(s):
        """Check if a string looks like the beginning of a URL."""
        s = strip_quotes(s)
        return s.startswith('http://') or s.startswith('https://')

    def looks_like_url_fragment(s):
        """Check if a string looks like a continuation fragment of a URL."""
        s = strip_quotes(s)
        if '=' in s and not s.startswith('--') and not s.startswith('-'):
            return True
        if s.startswith('/') and not s.startswith('//'):
            return True
        if (s
            and not s.startswith('-')
            and ' ' not in s
            and all(c.isalnum() or c in '_-.' for c in s)
            and len(s) < 64):
            return True
        return False

    result = []
    i = 0
    url_found = False

    while i < len(args):
        arg = args[i]

        if not url_found and looks_like_url_start(arg):
            url_parts = [strip_quotes(arg)]

            j = i + 1
            while j < len(args):
                next_arg = args[j]
                if next_arg.startswith('-'):
                    break
                if looks_like_url_fragment(strip_quotes(next_arg)):
                    cleaned = strip_quotes(next_arg)
                    url_parts.append(cleaned)
                    j += 1
                    continue
                break

            if len(url_parts) == 1:
                url = url_parts[0]
            else:
                base = url_parts[0]
                fragments = url_parts[1:]
                if base and not base.endswith('&'):
                    url = base + '&' + '&'.join(fragments)
                else:
                    url = base + '&'.join(fragments)
            result.append(url)
            url_found = True
            i = j
        else:
            result.append(arg)
            if not arg.startswith('-') and not looks_like_url_start(arg):
                url_found = True
            i += 1

    return result