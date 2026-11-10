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

If the crawl hits `--max` because filter or pagination parameters produce thousands of near-duplicate URLs, rerun with `--strip-params` and a new `-o` file. Keep both outputs, because the URL explosion is a finding in itself.

### Step 3: capture site-level config

The crawler doesn't save robots.txt, the sitemap index or response headers, but a report needs them. Replace `example.com` and run:

```
curl -s https://example.com/robots.txt -o robots.txt
```

```
curl -s https://example.com/sitemap.xml -o sitemap.xml
```

```
for u in http://example.com/ http://www.example.com/ https://www.example.com/ https://example.com/; do echo "== $u"; curl -sI "$u"; done > headers.txt
```

## Generating a report

Give the files below to an AI assistant that can read files and run code, such as Claude Code. `crawl.json` is usually too large to paste into a chat.

| File | Needed? | What it provides |
|---|---|---|
| `crawl.json` | Required | Page data, status codes, redirects, links |
| `sitemaps.json` | Recommended | Sitemap coverage, orphans, lastmod dates, failed sitemaps |
| `robots.txt` | Recommended | Robots rules and `Sitemap:` lines |
| `sitemap.xml` | Recommended | The sitemap index as served |
| `headers.txt` | Recommended | http→https and www redirects, HSTS and other headers |
| `crawl.log` | Optional | Progress and errors during the crawl |
| Second crawl output | Optional | If you reran with `--strip-params` |

Then use this prompt. Fill in the bracketed parts first.

```
You are writing a technical SEO audit report for [SITE], based on a crawl run on [CRAWL DATE]. The audience is [e.g. the site owner and their web team]: knowledgeable but busy, so lead with what matters and keep it plain.

The files in this folder are: crawl.json (output of crawl.py), sitemaps.json (output of sitemaps.py), robots.txt, sitemap.xml, headers.txt (curl -sI against the http/https and www/bare variants of the homepage) and crawl.log. [Mention any second crawl and the --strip-params used.] The README describes the crawl.json format. Note that "src" holds only the first page found linking to a URL, so count inbound links by searching every page's "links".

Analyse the data with code rather than by reading the JSON by eye, and base every number in the report on that analysis. Check counts before using them, and give concrete example URLs for each finding. Do not guess at causes you can't support from the data. Where something is a likely explanation rather than a certainty, say so.

Cover at least: (1) Site-level configuration: robots.txt syntax and whether it blocks what it appears meant to block, Sitemap: lines, redirect behaviour between http/https and www/bare (including stray ports or extra hops), and security headers such as HSTS. (2) Sitemap quality: the share of sitemap URLs that are not clean 200 pages, grouped by cause (404s, internal redirects, off-site redirects, non-canonical, noindex), failed or empty child sitemaps, the structure of the sitemap index, orphan URLs (in the sitemap but linked from no crawled page), indexable pages missing from the sitemap, and stale or suspicious lastmod dates. (3) Broken links and redirects: dead URLs and which pages link to them, grouped into patterns; redirect counts by type (301 vs 302) and chain length; and internal links that point at redirects, ranked by the number of linking pages so that site-wide navigation and footer links stand out. (4) On-page SEO: missing, duplicate or overlong titles and meta descriptions, missing or multiple H1s, canonical problems (missing, multiple, pointing at non-200 or other pages), hreflang problems, noindex pages, images without alt text, thin pages, missing structured data or Open Graph tags, and parameter-driven duplicate URLs. (5) Performance: median, p95 and maximum response time and the slowest pages. Look out for patterns that suggest a single root cause, such as malformed URL structures generated by the CMS, or draft and test pages that are live and indexable, and report the pattern rather than every instance.

Structure the report as: a title line with the site, crawl date and report date; a Summary of two or three sentences followed by a numbered list of the five things to fix first, ordered by impact; a Scope and method section with a table of key measures (sitemaps listed and fetched, unique sitemap URLs, URLs crawled, HTML pages parsed, files checked, status code breakdown, response times, external links found but not checked) and a note of any crawl limits hit; then one section for each area above, using tables for issue/evidence/effect and short bullets for patterns; and a short Limitations section (external links not checked, JavaScript not rendered, a snapshot on one date). Keep it to about 4 to 6 pages. Only put recommendations where the fix is clear, and say who would typically make it (server config, CMS template, content editors).

Write in plain British English with short sentences. Avoid jargon where a plain word works, and explain any SEO term the first time it appears. Produce the report as a clean, printable A4 PDF with a page header or footer carrying the report title and page numbers. Long URLs must wrap rather than overflow tables. Save it as seo-audit-report.pdf, then render a page or two to check the layout before you finish.
```

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
