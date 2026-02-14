#!/usr/bin/env python3

"""
wikisnap — fetch Wikipedia pages, convert to Markdown, and archive.

Reads a pipe-delimited CSV of (title, url, download_date) entries,
fetches each page, extracts the article body, converts it to clean
Markdown with YAML frontmatter, and bundles the results into a
timestamped .tar.gz archive.

Wikipedia-specific: relies on MediaWiki HTML structure (mw-parser-output,
infobox, navbox, etc.) to locate and filter article content.

Usage:
wikisnap --input urls.csv --output-dir ./archives
wikisnap --input urls.csv --output-dir ./archives --verbose
wikisnap --help
"""

import sys
import os
import csv
import hashlib
import tempfile
import argparse
from pathlib import Path
from datetime import date, datetime
from urllib.parse import urlparse
import tarfile

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


# --- Exit codes ---
EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_INPUT_ERROR = 2
EXIT_ARCHIVE_ERROR = 3

VERBOSE = False


def log(msg):
    """Print to stderr only when --verbose is set."""
    if VERBOSE:
        print(msg, file=sys.stderr)


def sanitize_title(title):
    """Convert a title string into a safe, lowercase, underscore-separated filename stem."""
    lowered = title.lower()
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    parts = [p for p in cleaned.split() if p]
    return "_".join(parts) if parts else "untitled"


def url_hash(url):
    """Return a short MD5 hash of a URL for unique filenames."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:8]


def fetch_content(url):
    """Fetch HTML content from a URL or local file path. Returns None on failure."""
    parsed = urlparse(url)

    if parsed.scheme in ("file", ""):
        path = Path(parsed.path)
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"Error: failed to read local file {url} ({e})", file=sys.stderr)
            return None

    if parsed.scheme not in ("http", "https"):
        print(f"Error: unsupported URL scheme for {url}", file=sys.stderr)
        return None

    try:
        response = requests.get(url, timeout=20)
    except Exception as e:
        print(f"Error: failed to download {url} ({e})", file=sys.stderr)
        return None

    if response.status_code != 200:
        print(f"Error: failed to download {url} (status {response.status_code})", file=sys.stderr)
        return None

    return response.text


# ---------------------------------------------------------------------------
# HTML → Markdown conversion (Wikipedia-specific)
# ---------------------------------------------------------------------------

def convert_inline(node):
    """Recursively convert inline HTML elements to Markdown."""
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = node.name
    classes = node.get("class", [])

    # Skip Wikipedia reference superscripts and edit-section links
    if name == "sup" and "reference" in classes:
        return ""
    if name == "span" and ("mw-editsection" in classes or "mw-editsection-visualeditor" in classes):
        return ""

    inner = "".join(convert_inline(child) for child in node.children)

    if name in ("b", "strong"):
        return f"**{inner}**"
    if name in ("i", "em"):
        return f"*{inner}*"
    if name == "code" and node.parent and node.parent.name != "pre":
        return f"`{inner}`"
    if name == "a":
        href = node.get("href")
        return f"[{inner}]({href})" if href else inner

    return inner


def convert_heading(tag):
    """Convert an h1–h6 tag to a Markdown heading line."""
    text = "".join(convert_inline(child) for child in tag.children).strip()
    if not text:
        return []
    level = max(1, min(6, int(tag.name[1])))
    return [f"{'#' * level} {text}", ""]


def convert_paragraph(tag):
    """Convert a <p> tag to a Markdown paragraph."""
    text = "".join(convert_inline(child) for child in tag.children).strip()
    return [text, ""] if text else []


def convert_list(tag, indent=0, ordered=False):
    """Convert a <ul> or <ol> tag to Markdown list items, with nesting up to 2 levels."""
    lines = []
    index = 1

    for li in tag.find_all("li", recursive=False):
        sub_lists = []
        inline_children = []

        for child in li.children:
            if isinstance(child, Tag) and child.name in ("ul", "ol"):
                sub_lists.append(child)
            else:
                inline_children.append(child)

        item_text = "".join(convert_inline(c) for c in inline_children).strip()
        marker = f"{index}." if ordered else "-"

        if item_text:
            lines.append("  " * indent + f"{marker} {item_text}")

        for sub in sub_lists:
            next_indent = min(indent + 1, 2)
            lines.extend(convert_list(sub, next_indent, sub.name == "ol"))

        index += 1

    if lines:
        lines.append("")
    return lines


def convert_blockquote(tag):
    """Convert a <blockquote> tag to Markdown blockquote lines."""
    text = "".join(convert_inline(child) for child in tag.children).strip()
    if not text:
        return []
    lines = ["> " + line.strip() for line in text.splitlines() if line.strip()]
    lines.append("")
    return lines


def convert_code_block(tag):
    """Convert a <pre> tag to a fenced Markdown code block."""
    content = tag.get_text("\n").rstrip("\n")
    return ["```", content, "```", ""]


# Sections to skip entirely (Wikipedia boilerplate)
SKIP_SECTIONS = {"References", "External links", "See also", "Further reading", "Notes"}

# CSS classes to skip (Wikipedia UI/metadata elements)
SKIP_CLASSES = {"infobox", "navbox", "vertical-navbox", "hatnote", "metadata", "mwe-math-element", "sidebar"}


def html_to_markdown(html_content, csv_title, url, download_date):
    """
    Parse a Wikipedia page and convert the article body to Markdown.
    Returns the Markdown string with YAML frontmatter, or None on failure.
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception as e:
        print(f"Error: HTML parsing failed for {url} ({e})", file=sys.stderr)
        return None

    content = soup.find("div", {"class": "mw-parser-output"})
    if content is None:
        print(f"Error: could not find main article content for {url}", file=sys.stderr)
        return None

    title_tag = soup.find("h1")
    article_title = title_tag.get_text(strip=True) if title_tag else csv_title

    # YAML frontmatter
    lines = [
        "---",
        f"title: {csv_title}",
        f"url: {url}",
        f"download_date: {download_date.isoformat()}",
        "---",
        "",
        f"# {article_title}",
        "",
    ]

    ignore_section = False

    for child in content.children:
        if isinstance(child, NavigableString) or not isinstance(child, Tag):
            continue

        classes = child.get("class", [])

        # Skip non-content elements
        if child.name in ("table", "figure", "img", "style", "script"):
            continue
        if child.name == "div" and child.get("id") == "toc":
            continue
        if any(cls in SKIP_CLASSES for cls in classes):
            continue

        # Toggle section skipping for boilerplate headings
        if child.name == "h2":
            heading_text = child.get_text(" ", strip=True)
            if heading_text in SKIP_SECTIONS:
                ignore_section = True
                continue
            ignore_section = False

        if ignore_section:
            continue

        # Convert supported block elements
        if child.name in ("h2", "h3", "h4", "h5", "h6"):
            lines.extend(convert_heading(child))
        elif child.name == "p":
            lines.extend(convert_paragraph(child))
        elif child.name in ("ul", "ol"):
            lines.extend(convert_list(child, ordered=(child.name == "ol")))
        elif child.name == "pre":
            lines.extend(convert_code_block(child))
        elif child.name == "blockquote":
            lines.extend(convert_blockquote(child))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CSV parsing and archiving
# ---------------------------------------------------------------------------
def parse_csv(csv_path):
    """
    Read a pipe-delimited CSV file with columns: title | url | download_date.
    Returns a list of (title, url, date) tuples.
    """
    entries = []
    try:
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="|")
            for row in reader:
                if len(row) != 3:
                    print("Error: CSV row does not have exactly 3 fields.", file=sys.stderr)
                    sys.exit(EXIT_INPUT_ERROR)
                title = row[0].strip()
                url = row[1].strip()
                try:
                    download_date = date.fromisoformat(row[2].strip())
                except ValueError:
                    print(f"Error: invalid date format in CSV: {row[2].strip()}", file=sys.stderr)
                    sys.exit(EXIT_INPUT_ERROR)
                entries.append((title, url, download_date))
    except Exception as e:
        print(f"Error: failed to parse CSV ({e})", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    if not entries:
        print("Error: CSV file is empty.", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    return entries


def main():
    parser = argparse.ArgumentParser(
        prog="wikisnap",
        description="Fetch Wikipedia pages, convert to Markdown, and archive as .tar.gz.",
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="path to pipe-delimited CSV file (title|url|download_date)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        required=True,
        help="directory where timestamped .tar.gz archives are saved",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="print progress messages to stderr",
    )
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    csv_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not csv_path.is_file():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)

    entries = parse_csv(csv_path)
    today = date.today()
    output_dir.mkdir(parents=True, exist_ok=True)

    converted = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        for title, url, download_date in entries:
            if download_date > today:
                log(f"Skipping (future date): {title}")
                continue

            log(f"Fetching: {url}")
            html_content = fetch_content(url)
            if html_content is None:
                continue

            markdown = html_to_markdown(html_content, title, url, download_date)
            if markdown is None:
                continue

            filename = sanitize_title(title) + "_" + url_hash(url) + ".md"
            md_path = tmpdir_path / filename
            try:
                md_path.write_text(markdown, encoding="utf-8")
                converted += 1
                log(f"Converted: {title} → {filename}")
            except Exception as e:
                print(f"Error: failed to write {filename} ({e})", file=sys.stderr)

        if converted == 0:
            print("Warning: no pages were converted.", file=sys.stderr)

        # Create timestamped archive
        archive_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".tar.gz"
        archive_path = output_dir / archive_name

        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                for md_file in sorted(tmpdir_path.glob("*.md")):
                    tar.add(md_file, arcname=md_file.name)
        except Exception as e:
            print(f"Error: failed to create archive ({e})", file=sys.stderr)
            sys.exit(EXIT_ARCHIVE_ERROR)

    log(f"Archive created: {archive_path} ({converted} pages)")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
