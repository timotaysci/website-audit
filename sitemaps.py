"""Slow, resumable sitemap fetcher.

Collects every URL listed in a site's XML sitemaps, following sitemap index files
to any depth. Results go to a JSON file that crawl.py can use as its seed list.

Usage:
    python3 sitemaps.py https://example.com [-o sitemaps.json] [--delay 0.6]
    python3 sitemaps.py https://example.com --sitemap https://example.com/custom-sitemap.xml
"""
import argparse
import json
import os
import sys
import time
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests

NS = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
XHTML_LINK = '{http://www.w3.org/1999/xhtml}link'
DEFAULT_UA = 'Mozilla/5.0 (compatible; website-audit)'


def find_sitemaps(session, site):
    """Return the Sitemap: URLs from robots.txt, or /sitemap.xml if there are none."""
    try:
        r = session.get(urljoin(site, '/robots.txt'), timeout=30)
        if r.status_code == 200:
            found = [line.split(':', 1)[1].strip() for line in r.text.splitlines()
                     if line.lower().startswith('sitemap:')]
            if found:
                return found
    except requests.RequestException:
        pass
    return [urljoin(site, '/sitemap.xml')]


def get(session, url):
    """GET with backoff on 429 and on empty 200 responses (a quiet form of rate limiting)."""
    r = None
    for attempt in range(8):
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException:
            time.sleep(5)
            continue
        if r.status_code == 429 or (r.status_code == 200 and not r.content.strip() and attempt < 4):
            time.sleep(min(120, 10 * 2 ** attempt))
            continue
        break
    return r


def parse(content):
    """Return (child_sitemaps, url_entries) from a sitemap or sitemap index document."""
    x = ET.fromstring(content)
    children = [e.text.strip() for e in x.findall('s:sitemap/s:loc', NS) if e.text]
    entries = []
    for url in x.findall('s:url', NS):
        loc = url.find('s:loc', NS)
        if loc is None or not loc.text:
            continue
        lm = url.find('s:lastmod', NS)
        entries.append({'loc': loc.text.strip(),
                        'lastmod': lm.text if lm is not None else None,
                        'alts': [a.get('href') for a in url.findall(XHTML_LINK)]})
    return children, entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('site', help='site root, e.g. https://example.com')
    ap.add_argument('-o', '--out', default='sitemaps.json', help='output file (default: sitemaps.json)')
    ap.add_argument('--sitemap', action='append', help='sitemap URL to start from (repeatable; default: from robots.txt)')
    ap.add_argument('--delay', type=float, default=0.6, help='seconds between requests (default: 0.6)')
    ap.add_argument('--user-agent', default=DEFAULT_UA)
    a = ap.parse_args()

    s = requests.Session()
    s.headers['User-Agent'] = a.user_agent

    # Each record is [sitemap_url, status, entries]. Status is an HTTP code, "200-empty",
    # "200-badxml" or an error string. Records with status 200 / "200-*" count as done.
    if os.path.exists(a.out):
        res = json.load(open(a.out))
    else:
        res = [[u, None, []] for u in (a.sitemap or find_sitemaps(s, a.site))]
    known = {r[0] for r in res}

    i = 0
    while i < len(res):  # res grows as sitemap indexes are discovered
        u, c, o = res[i]
        if c == 200 or (isinstance(c, str) and c.startswith('200-')):
            i += 1
            continue
        r = get(s, u)
        children = []
        if r is None:
            code, o = 'ERR', []
        elif r.status_code != 200:
            code, o = r.status_code, []
        elif not r.content.strip():
            code, o = '200-empty', []
        else:
            try:
                children, o = parse(r.content)
                code = 200
            except ET.ParseError:
                code, o = '200-badxml', []
        res[i] = [u, code, o]
        for ch in children:
            if ch not in known:
                known.add(ch)
                res.append([ch, None, []])
        if i % 50 == 0:
            json.dump(res, open(a.out, 'w'))
            print(i, len(res), code, u, flush=True)
        i += 1
        time.sleep(a.delay)

    json.dump(res, open(a.out, 'w'))
    ok = sum(1 for x in res if x[1] == 200)
    urls = {e['loc'] for x in res for e in x[2]}
    print(f'done: {ok} of {len(res)} sitemaps fetched, {len(urls)} unique URLs', file=sys.stderr)


if __name__ == '__main__':
    main()
