# WikiSnap

CLI tool that fetches Wikipedia pages, converts them to clean Markdown, and archives the results as timestamped `.tar.gz` snapshots — with built-in change detection between runs.

---

## Quick Start

```bash
git clone https://github.com/xbadev/wikisnap.git
cd wikisnap
pip install -r requirements.txt
chmod +x bin/wikisnap bin/diffcheck
```

```bash
# Fetch pages and create an archive
./bin/wikisnap --input example/urls.psv --output-dir ./archives

# Compare today's archive against 7 days ago
./bin/diffcheck --days 7 --output-dir ./archives
```


## How It Works

[`wikisnap`](bin/wikisnap) reads a pipe-delimited file of Wikipedia URLs, fetches each page, extracts the article body using MediaWiki's HTML structure, converts it to Markdown with YAML frontmatter, and bundles everything into a timestamped `.tar.gz` in the output directory.

[`diffcheck`](bin/diffcheck) compares today's archive against one from N days ago and reports which pages have changed — designed to run on a schedule after `wikisnap` to track edits over time.

```
urls.psv → wikisnap → archives/2025-02-13_03-30-00.tar.gz
                                      ↓
                              diffcheck --days 7
                                      ↓
                          "These pages changed: ..."
```

> **Note:** The scraper targets Wikipedia's MediaWiki HTML structure specifically. It is not a general-purpose HTML-to-Markdown converter.


## Input Format

The input file uses `|` as a delimiter with three columns — see [`example/urls.psv`](example/urls.psv):

```
Linked list|https://en.wikipedia.org/wiki/Linked_list|2025-01-15
Hash table|https://en.wikipedia.org/wiki/Hash_table|2025-01-15
```

Pages with a `download_date` in the future are skipped.


## CLI Reference

### wikisnap

| Flag | Description |
|------|-------------|
| `-i`, `--input` | Path to pipe-delimited input file (required) |
| `-o`, `--output-dir` | Directory for `.tar.gz` archives (required) |
| `-v`, `--verbose` | Print progress to stderr |
| `-h`, `--help` | Show usage |

### diffcheck

| Flag | Description |
|------|-------------|
| `-n`, `--days` | Days to look back (required) |
| `-o`, `--output-dir` | Directory containing archives (required) |
| `-v`, `--verbose` | Print progress to stderr |
| `-h`, `--help` | Show usage |



## Cron Automation

See [`example/crontab.example`](example/crontab.example) for ready-to-use schedules — daily snapshots, daily diffcheck, and weekly snapshots. Replace `USER` with your username and test manually before enabling.

```cron
# Daily snapshot at 2:00 AM
0 2 * * *  /home/USER/wikisnap/.venv/bin/python \
  /home/USER/wikisnap/bin/wikisnap \
  -i /home/USER/wikisnap/example/urls.psv \
  -o /home/USER/wikisnap/snapshots >> /home/USER/wikisnap/cron.log 2>&1

# Daily diffcheck at 2:30 AM (compare with 1 day ago)
30 2 * * *  /home/USER/wikisnap/.venv/bin/python \
  /home/USER/wikisnap/bin/diffcheck \
  -o /home/USER/wikisnap/snapshots \
  -n 1 >> /home/USER/wikisnap/cron.log 2>&1
```


## Exit Codes

### wikisnap

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Bad arguments |
| `2` | Input file error (missing, malformed, or empty) |
| `3` | Archive creation failed |

### diffcheck

| Code | Meaning |
|------|---------|
| `0` | Success (changes found or no changes) |
| `1` | Bad arguments |
| `2` | Required archive not found |


## Repo Structure

```
├── bin/
│   ├── wikisnap            # Fetch → convert → archive
│   └── diffcheck           # Compare snapshots for changes
├── example/
│   ├── urls.psv            # Sample input (pipe-delimited)
│   └── crontab.example     # Cron schedule reference
├── requirements.txt        # Python dependencies
├── .gitignore
└── LICENSE                 # MIT
```

## Requirements

- Python 3.10+
- [`requests`](https://pypi.org/project/requests/) and [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) (installed via [`requirements.txt`](requirements.txt))

---

## Author

**Bader Alansari** — [@xbadev](https://github.com/xbadev)
