# Website audit

Two small Python scripts for a technical SEO audit of a single website:

1. **`sitemaps.py`** collects every URL listed in the site's XML sitemaps.
2. **`crawl.py`** crawls the site from the homepage and those URLs. For each page it records status codes, redirect chains, dead links, titles, meta tags, canonicals, hreflang, headings, images and links.

The output is raw JSON for you to analyse however you like. Both scripts are deliberately slow and polite, and they back off when the server rate-limits them.

## Requirements

- Python 3.8+
- `requests` and `beautifulsoup4`

```
pip install -r requirements.txt
```

## Usage

Run both scripts from the folder where you want the output. Long crawls are best run in `tmux` or with `nohup`, so they keep going after you disconnect.

### Step 1: collect sitemap URLs (optional but recommended)

```
python3 sitemaps.py https://example.com
```

- Finds sitemaps from the `Sitemap:` lines in robots.txt, falling back to `/sitemap.xml`. Use `--sitemap URL` (repeatable) to choose them yourself.
- Follows sitemap index files to any depth.
- Makes one request every 0.6 s (change it with `--delay`). On HTTP 429, or on an empty HTTP 200 response, it waits and retries with backoff (10 s, 20 s, 40 s… up to 120 s).
- Writes `sitemaps.json` (change it with `-o`) every 50 sitemaps and at the end. **Resumable:** run it again and it skips sitemaps already fetched.
- A sitemap that still comes back empty or as invalid XML after retries is recorded as `200-empty` or `200-badxml`. Both are findings in themselves.

Each record in `sitemaps.json` is `[sitemap_url, status, [{"loc", "lastmod", "alts"}, ...]]`.

### Step 2: crawl the site

```
python3 crawl.py https://example.com --max 5000 2>&1 | tee crawl.log
```

- Seeds from the homepage plus every URL in `sitemaps.json`, if that file exists. Use `--sitemaps FILE` to point it at a different file.
- Follows internal links. The site's host counts as internal, along with its `www.` or bare-domain counterpart. Add more hosts with `--host`.
- Follows redirects one hop at a time and records the whole chain.
- Checks files (PDF, images, Office documents, CSS/JS, etc.) with a HEAD request, falling back to GET if the server refuses HEAD.
- Obeys robots.txt. Blocked URLs are recorded as `skipped: robots` rather than fetched. Pass `--ignore-robots` to turn this off.
- `--max` caps the number of URLs fetched (default 5000). Files count towards it.
- Prints a progress line every 100 URLs (count, queue length, current URL, status) and saves the output at the same time.
- **Not resumable.** If it dies, it starts over, though the partial output is still usable.

Other options:

| Option | Effect |
|---|---|
| `-o FILE` | Output file (default `crawl.json`) |
| `--delay SECONDS` | Pause between requests (default 0.4) |
| `--exclude /path/` | Skip URLs whose path starts with this (repeatable) |
| `--exclude-param q` | Skip URLs that carry this query parameter, e.g. site search (repeatable) |
| `--strip-params a,b` | Drop these query parameters, collapsing filter and pagination variants onto one URL |
| `--user-agent UA` | Default `Mozilla/5.0 (compatible; website-audit)` |
| `--accept-language` | Default `en-GB,en;q=0.9` |

## Output: `crawl.json`

```
{
  "pages": {
    "<url>": {
      "src": "sitemap" | "seed" | "<url of page that linked here>",
      "depth": 0,
      "chain": [{"url": ..., "status": 301, "location": ...}, ...],
      "final_url": "...", "status": 200, "time": 0.42,
      "ct": "text/html;charset=UTF-8", "x_robots": null,
      "title": ..., "meta_desc": ..., "meta_robots": ...,
      "canonical": [...], "hreflang": [[lang, href], ...],
      "h1": [...], "h2_count": 3, "lang": "en-GB",
      "og_title": true, "og_image": true, "jsonld": 1, "viewport": true,
      "img_count": 12, "img_no_alt": 4, "words": 850, "html_bytes": 210000,
      "links": [{"href": ..., "text": ..., "nofollow": false}, ...]
    }
  },
  "resources": { "<file url>": {"src": ..., "chain": [...], "final": 200} },
  "queue_left": 0
}
```

- **`src`** shows only the *first* page found linking to a URL. To find every page that links to a dead URL, search all pages' `links`.
- **`status`** can be `"ERR"`, meaning a network error or timeout. The error text is in the last `chain` entry.
- **Skipped URLs** appear as `{"skipped": "robots" | "excluded", "src": ...}`.
- **`queue_left`** above 0 means the crawl hit `--max` before finishing. Rerun with a higher limit.

## Limitations

- **External links aren't checked.** They're collected in each page's `links` but never requested.
- **JavaScript isn't rendered.** The crawl sees the raw server HTML only, so it suits server-rendered sites best.

## Being polite

- Only crawl sites you own or have permission to audit.
- Run one script at a time against a site, since they share its rate limit.
- If you see a lot of 429s, raise `--delay`.
