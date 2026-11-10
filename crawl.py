"""Polite single-site SEO crawler.

Starts from the homepage plus any URLs found by sitemaps.py, follows internal links,
and records status codes, redirect chains, titles, meta tags, canonicals, hreflang,
headings, images and links for every page. Output is raw JSON for later analysis.

Usage:
    python3 crawl.py https://example.com [--max 5000] [-o crawl.json] [--sitemaps sitemaps.json]
"""
import argparse
import json
import os
import re
import time
from collections import deque
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

DEFAULT_UA = 'Mozilla/5.0 (compatible; website-audit)'
FILE_EXT = re.compile(r'\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|pptx?|mp4|mp3|ico|css|js|woff2?)$', re.I)
REDIRECTS = (301, 302, 303, 307, 308)


def host_key(netloc):
    """Lower-case host with any default port removed."""
    netloc = netloc.lower()
    for port in (':443', ':80'):
        if netloc.endswith(port):
            netloc = netloc[:-len(port)]
    return netloc


class Crawler:
    def __init__(self, a):
        self.a = a
        self.s = requests.Session()
        self.s.headers['User-Agent'] = a.user_agent
        self.s.headers['Accept-Language'] = a.accept_language
        host = host_key(urlsplit(a.site).netloc)
        bare = host[4:] if host.startswith('www.') else host
        self.hosts = {bare, 'www.' + bare} | {host_key(h) for h in a.host}
        self.strip = set(a.strip_params)
        self.robots = None
        if not a.ignore_robots:
            self.robots = RobotFileParser()
            r = self.s.get(urljoin(a.site, '/robots.txt'), timeout=30)
            self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])

    def norm(self, u):
        p = urlsplit(u)
        query = p.query
        if self.strip:
            query = urlencode([kv for kv in parse_qsl(p.query, keep_blank_values=True) if kv[0] not in self.strip])
        return urlunsplit((p.scheme.lower(), host_key(p.netloc), p.path or '/', query, ''))

    def internal(self, u):
        p = urlsplit(u)
        return p.scheme in ('http', 'https') and host_key(p.netloc) in self.hosts

    def skip_reason(self, u):
        p = urlsplit(u)
        if any(p.path.startswith(x) for x in self.a.exclude):
            return 'excluded'
        params = {k for k, _ in parse_qsl(p.query, keep_blank_values=True)}
        if params & set(self.a.exclude_param):
            return 'excluded'
        if self.robots and not self.robots.can_fetch(self.a.user_agent, u):
            return 'robots'
        return None

    def fetch(self, u, method='GET'):
        """Fetch without auto-redirects, one hop at a time. Return (chain, final_response, seconds)."""
        chain, cur = [], u
        for _ in range(10):
            r, err, el = None, None, 0
            for attempt in range(7):
                try:
                    t = time.time()
                    r = self.s.request(method, cur, allow_redirects=False, timeout=30)
                    el = time.time() - t
                except requests.RequestException as e:
                    r, err = None, str(e)[:200]
                    time.sleep(3)
                    continue
                if r.status_code == 429:
                    time.sleep(min(120, 10 * 2 ** attempt))
                    continue
                # Some servers return an empty 200 when throttling; retry a few times.
                if method == 'GET' and r.status_code == 200 and not r.content.strip() and attempt < 4:
                    time.sleep(10 * 2 ** attempt)
                    continue
                break
            if r is None:
                chain.append({'url': cur, 'status': 'ERR', 'error': err})
                return chain, None, 0
            chain.append({'url': cur, 'status': r.status_code, 'location': r.headers.get('Location')})
            if r.status_code in REDIRECTS and r.headers.get('Location'):
                cur = urljoin(cur, r.headers['Location'])
                r.close()
                continue
            return chain, r, el
        return chain, None, 0

    @staticmethod
    def parse(u, r):
        html = r.content.decode(r.encoding or 'utf-8', errors='replace')
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.get_text(strip=True) if soup.title else None
        md = soup.find('meta', attrs={'name': re.compile('^description$', re.I)})
        mr = soup.find('meta', attrs={'name': re.compile('^robots$', re.I)})
        links = []
        for a in soup.find_all('a', href=True):
            h = a['href'].strip()
            if not h or h.startswith(('mailto:', 'tel:', 'javascript:', '#', 'data:')):
                continue
            links.append({'href': urljoin(u, h), 'text': a.get_text(' ', strip=True)[:80],
                          'nofollow': 'nofollow' in (a.get('rel') or [])})
        imgs = soup.find_all('img')
        body = soup.body.get_text(' ', strip=True) if soup.body else ''
        return {
            'title': title,
            'meta_desc': md.get('content') if md else None,
            'meta_robots': mr.get('content') if mr else None,
            'canonical': [t.get('href') for t in soup.select('link[rel=canonical]')],
            'hreflang': [(t.get('hreflang'), t.get('href')) for t in soup.select('link[rel=alternate][hreflang]')],
            'h1': [h.get_text(' ', strip=True)[:150] for h in soup.find_all('h1')],
            'h2_count': len(soup.find_all('h2')),
            'lang': soup.html.get('lang') if soup.html else None,
            'og_title': bool(soup.find('meta', property='og:title')),
            'og_image': bool(soup.find('meta', property='og:image')),
            'jsonld': len(soup.select('script[type="application/ld+json"]')),
            'viewport': bool(soup.find('meta', attrs={'name': 'viewport'})),
            'img_count': len(imgs),
            'img_no_alt': sum(1 for i in imgs if not (i.get('alt') or '').strip()),
            'words': len(body.split()),
            'html_bytes': len(r.content),
            'links': links,
        }

    def save(self, pages, resources, queue_left=None):
        out = {'pages': pages, 'resources': resources}
        if queue_left is not None:
            out['queue_left'] = queue_left
        with open(self.a.out, 'w') as f:
            json.dump(out, f)

    def run(self):
        a = self.a
        home = self.norm(urljoin(a.site, '/'))
        seeds = [(home, 'seed')]
        if a.sitemaps and os.path.exists(a.sitemaps):
            for _, _, entries in json.load(open(a.sitemaps)):
                seeds += [(e['loc'], 'sitemap') for e in entries]
        seen, q = set(), deque()
        for u, src in seeds:
            n = self.norm(u)
            if n not in seen:
                seen.add(n)
                q.append((n, src, 0))

        pages, resources, count = {}, {}, 0
        while q and count < a.max:
            u, src, depth = q.popleft()
            reason = self.skip_reason(u)
            if reason:
                pages[u] = {'skipped': reason, 'src': src}
                continue

            if FILE_EXT.search(urlsplit(u).path):
                chain, r, _ = self.fetch(u, 'HEAD')
                if r is not None and r.status_code in (403, 405, 501):
                    chain, r, _ = self.fetch(u, 'GET')
                if r is not None:
                    r.close()
                resources[u] = {'src': src, 'chain': chain, 'final': chain[-1]['status'] if chain else None}
                count += 1
                time.sleep(a.delay)
                continue

            chain, r, el = self.fetch(u)
            count += 1
            rec = {'src': src, 'depth': depth, 'chain': chain, 'final_url': chain[-1]['url'],
                   'status': chain[-1]['status'], 'time': round(el, 2)}
            if r is not None:
                ct = r.headers.get('Content-Type', '')
                rec['ct'] = ct
                rec['x_robots'] = r.headers.get('X-Robots-Tag')
                if r.status_code == 200 and 'html' in ct and self.internal(rec['final_url']):
                    try:
                        rec.update(self.parse(rec['final_url'], r))
                    except Exception as e:
                        rec['parse_error'] = str(e)
                    for link in rec.get('links', []):
                        if self.internal(link['href']):
                            n = self.norm(link['href'])
                            if n not in seen:
                                seen.add(n)
                                q.append((n, u, depth + 1))
                fin = self.norm(rec['final_url'])
                if fin not in seen and self.internal(fin):
                    seen.add(fin)
                    q.append((fin, u, depth))
                r.close()
            pages[u] = rec

            if count % 100 == 0:
                print(count, len(q), u, rec['status'], flush=True)
                self.save(pages, resources)
            time.sleep(a.delay)

        self.save(pages, resources, len(q))
        print('done', count, 'queue left', len(q))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('site', help='site root, e.g. https://example.com')
    ap.add_argument('--max', type=int, default=5000, help='max URLs to fetch, files included (default: 5000)')
    ap.add_argument('-o', '--out', default='crawl.json', help='output file (default: crawl.json)')
    ap.add_argument('--sitemaps', default='sitemaps.json', help='seed URLs from sitemaps.py output, if present')
    ap.add_argument('--delay', type=float, default=0.4, help='seconds between requests (default: 0.4)')
    ap.add_argument('--exclude', action='append', default=[], metavar='PATH_PREFIX',
                    help='skip URLs whose path starts with this (repeatable)')
    ap.add_argument('--exclude-param', action='append', default=[], metavar='NAME',
                    help='skip URLs that have this query parameter, e.g. q (repeatable)')
    ap.add_argument('--strip-params', default='', type=lambda v: [x for x in v.split(',') if x],
                    help='comma-separated query parameters to drop, collapsing filter/pagination variants')
    ap.add_argument('--host', action='append', default=[], help='extra host to treat as internal (repeatable)')
    ap.add_argument('--ignore-robots', action='store_true', help='do not apply robots.txt rules')
    ap.add_argument('--user-agent', default=DEFAULT_UA)
    ap.add_argument('--accept-language', default='en-GB,en;q=0.9')
    Crawler(ap.parse_args()).run()


if __name__ == '__main__':
    main()
