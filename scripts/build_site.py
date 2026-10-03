#!/usr/bin/env python3
"""Build the static AshAndGrain.com site from the old Nice Pair WordPress dump.

Usage:
    python3 scripts/build_site.py /path/to/nicepair.sql [--no-fetch]

Output is written to docs/ (served by GitHub Pages). Images are recovered from
the Internet Archive and cached in docs/wp-content/uploads/, so re-runs only
download what is missing. The SQL dump contains private data (user accounts,
shop orders) and must never be committed.
"""
import html
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
UPLOADS = OUT / "wp-content" / "uploads"
EXTERNAL = OUT / "wp-content" / "external"
CDX_CACHE = ROOT / "scripts" / ".cache"
CONTENT = ROOT / "content" / "posts"
GENERATED = OUT / "assets" / "generated"

SITE_NAME = "Ash & Grain"
SITE_TAGLINE = "How to Pair Cigars & Drinks"
SITE_URL = "https://ashandgrain.com"
DOMAIN = "ashandgrain.com"
GA_ID = "G-7Q8NNK3BLB"
POSTS_PER_PAGE = 12

EXCLUDED_CATEGORIES = {"personal"}
INCLUDED_PAGES = {"about-us", "stores"}
LOCAL_CATEGORIES = {"spirit-guides": ("Spirit Guides", "Where popular spirits come from, how they're made, "
                                                       "and what to look for in a cigar to pair with each.")}
NAV = [("Pairings", "/category/pairings/"), ("Guides", "/category/spirit-guides/"), ("Articles", "/category/article/"),
       ("Events", "/category/events/"), ("Videos", "/category/videos/"),
       ("Stores", "/stores/"), ("About", "/about-us/")]
CATEGORY_HEADINGS = {"events": "Past Events"}
NO_SIDEBAR = {"events"}
# Home page features: (slider slugs, thumbnail slugs). Pairing slides use the original 960x285 slider banners.
FEATURED_PAIRINGS = (["warres-vintage-port-1983-cohiba-robusto",
                      "dows-1985-vintage-port-montecristo-edmundo",
                      "pairing-dom-perignon-2003-perdomo-10th-anniversary-champaign"],
                     ["1973-chateau-de-laubade-armagnac-espinosa-601-blue-label-maduro",
                      "pairing-laphroaig-and-cigars-18-year-and-san-cristobal",
                      "taylor-fladgate-30-year-paul-stulac-red-screaming-sun"])
FEATURED_GUIDES = (["cigar-and-spirits-pairing-guide", "scotch-cigar-pairing-guide", "cognac-cigar-pairing-guide"],
                   ["rum-cigar-pairing-guide", "port-cigar-pairing-guide", "bourbon-cigar-pairing-guide"])

TABLES = {"wp_posts", "wp_terms", "wp_term_taxonomy", "wp_term_relationships", "wp_postmeta"}
OLD_HOST = r"https?://(?:www\.)?nicepair\.ca"
UPLOAD_RE = re.compile(OLD_HOST + r"/wp-content/uploads/([^\"'\s)>]+)", re.I)
SIZE_SUFFIX = re.compile(r"-\d+x\d+(?=\.\w+$)")


# --------------------------------------------------------------------------- SQL

def parse_values(s, i):
    rows, n = [], len(s)
    token = re.compile(r"[^,)]*")
    escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "Z": "\x1a"}
    while i < n:
        while i < n and s[i] in " ,\n":
            i += 1
        if i >= n or s[i] != "(":
            break
        i += 1
        row = []
        while True:
            if s[i] == "'":
                i += 1
                buf = []
                while True:
                    c = s[i]
                    if c == "\\":
                        buf.append(escapes.get(s[i + 1], s[i + 1]))
                        i += 2
                    elif c == "'":
                        if s[i + 1] == "'":
                            buf.append("'")
                            i += 2
                        else:
                            i += 1
                            break
                    else:
                        buf.append(c)
                        i += 1
                row.append("".join(buf))
            else:
                m = token.match(s, i)
                tok = m.group(0).strip()
                i = m.end()
                row.append(None if tok == "NULL" else (int(tok) if re.fullmatch(r"-?\d+", tok) else tok))
            if s[i] == ",":
                i += 1
                continue
            i += 1
            break
        rows.append(row)
    return rows


def load_dump(path):
    data = Path(path).read_text(encoding="utf-8", errors="replace")
    cols = {}
    for m in re.finditer(r"CREATE TABLE `(\w+)` \((.*?)\n\)", data, re.S):
        if m.group(1) in TABLES:
            cols[m.group(1)] = re.findall(r"^\s+`(\w+)`", m.group(2), re.M)
    tables = {t: [] for t in cols}
    for m in re.finditer(r"^INSERT INTO `(\w+)` VALUES ", data, re.M):
        t = m.group(1)
        if t in cols:
            tables[t].extend(dict(zip(cols[t], r)) for r in parse_values(data, m.end()))
    return tables


def load_local_posts(categories):
    """Read articles from content/posts/*.html, each starting with a <!-- key: value --> header."""
    by_slug = {c["slug"]: c for c in categories.values()}
    posts = []
    for n, path in enumerate(sorted(CONTENT.glob("*.html")), start=1):
        text = path.read_text(encoding="utf-8")
        m = re.match(r"\s*<!--(.*?)-->\s*", text, re.S)
        if not m:
            sys.exit(f"{path}: missing <!-- header -->")
        meta = dict(re.findall(r"^\s*(\w+):\s*(.*?)\s*$", m.group(1), re.M))
        cats = []
        for slug in (s.strip() for s in meta.get("categories", "").split(",") if s.strip()):
            if slug not in by_slug:
                if slug not in LOCAL_CATEGORIES:
                    sys.exit(f"{path}: unknown category '{slug}'")
                name, desc = LOCAL_CATEGORIES[slug]
                new_id = -len(by_slug) - 1
                categories[new_id] = by_slug[slug] = {
                    "term_id": new_id, "name": name, "slug": slug,
                    "parent": 0, "description": desc, "posts": []}
            cats.append(by_slug[slug])
        posts.append({
            "ID": -n, "post_name": meta.get("slug") or path.stem, "post_title": meta["title"],
            "post_content": text[m.end():], "post_excerpt": meta.get("excerpt", ""),
            "post_date": meta["date"], "post_modified": meta.get("modified", meta["date"]),
            "cats": sorted(cats, key=lambda c: c["name"]),
            "description": meta.get("description", ""), "header_image": meta.get("image")})
    return posts


# ---------------------------------------------------------------------- content

BLOCK = (r"(?:table|thead|tfoot|caption|col|colgroup|tbody|tr|td|th|div|dl|dd|dt|ul|ol|li|pre|form|map|"
         r"area|blockquote|address|math|style|p|h[1-6]|hr|fieldset|legend|section|article|aside|hgroup|"
         r"header|footer|nav|figure|figcaption|details|menu|summary|iframe)")


def wpautop(text):
    """Port of WordPress's wpautop(): blank lines become paragraphs, single newlines <br />."""
    if not text.strip():
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pres = {}

    def save_pre(m):
        key = f"<pre wp-pre-tag-{len(pres)}></pre>"
        pres[key] = m.group(0)
        return key

    text = re.sub(r"<pre[\s\S]*?</pre>", save_pre, text) + "\n"
    text = re.sub(r"<br\s*/?>\s*<br\s*/?>", "\n\n", text)
    text = re.sub(r"(<" + BLOCK + r"[\s/>])", r"\n\n\1", text)
    text = re.sub(r"(</" + BLOCK + r">)", r"\1\n\n", text)
    text = re.sub(r"(<hr\s*?/?>)", r"\1\n\n", text)
    text = re.sub(r"\n\n+", "\n\n", text)
    text = "".join("<p>" + p.strip("\n") + "</p>\n" for p in re.split(r"\n\s*\n", text) if p.strip())
    text = re.sub(r"<p>\s*</p>", "", text)
    text = re.sub(r"<p>([^<]+)</(div|address|form)>", r"<p>\1</p></\2>", text)
    text = re.sub(r"<p>\s*(</?" + BLOCK + r"[^>]*>)\s*</p>", r"\1", text)
    text = re.sub(r"<p>(<li.+?)</p>", r"\1", text)
    text = re.sub(r"<p><blockquote([^>]*)>", r"<blockquote\1><p>", text, flags=re.I)
    text = text.replace("</blockquote></p>", "</p></blockquote>")
    text = re.sub(r"<p>\s*(</?" + BLOCK + r"[^>]*>)", r"\1", text)
    text = re.sub(r"(</?" + BLOCK + r"[^>]*>)\s*</p>", r"\1", text)
    text = re.sub(r"<(script|style).*?</\1>",
                  lambda m: m.group(0).replace("\n", "<WPPreserveNewline />"), text, flags=re.S)
    text = re.sub(r"(?<!<br />)\s*\n", "<br />\n", text)
    text = text.replace("<WPPreserveNewline />", "\n")
    text = re.sub(r"(</?" + BLOCK + r"[^>]*>)\s*<br />", r"\1", text)
    text = re.sub(r"<br />(\s*</?(?:p|li|div|dl|dd|dt|th|pre|td|ul|ol)[^>]*>)", r"\1", text)
    text = re.sub(r"\n</p>$", "</p>", text)
    for key, val in pres.items():
        text = text.replace(key, val)
    return text


def shortcode_attrs(s):
    return {k: v1 or v2 for k, v1, v2 in re.findall(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\')', s)}


class Images:
    """Resolves old upload URLs to files recovered from the Wayback Machine."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.archived = {}      # upload path -> (original url, timestamp)
        self.by_base = {}       # base path without size suffix -> [upload paths]
        self.by_name = {}       # file name -> [upload paths]
        self.missing = set()
        self.external = {}
        self._load_cdx()

    def _load_cdx(self):
        CDX_CACHE.mkdir(parents=True, exist_ok=True)
        cache = CDX_CACHE / "uploads-cdx.txt"
        if not cache.exists():
            if not self.fetch:
                return
            url = ("https://web.archive.org/cdx/search/cdx?url=nicepair.ca/wp-content/uploads/"
                   "&matchType=prefix&output=txt&collapse=urlkey&filter=statuscode:200"
                   "&fl=original,timestamp,mimetype")
            cache.write_bytes(http_get(url))
        for line in cache.read_text().splitlines():
            parts = line.split()
            if len(parts) < 3 or not parts[2].startswith("image/"):
                continue
            m = UPLOAD_RE.match(parts[0])
            if not m:
                continue
            path = urllib.parse.unquote(m.group(1).split("?")[0])
            self.archived[path] = (parts[0], parts[1])
            self.by_base.setdefault(SIZE_SUFFIX.sub("", path).lower(), []).append(path)
            self.by_name.setdefault(path.rsplit("/", 1)[-1].lower(), []).append(path)

    def resolve(self, path):
        """Return a local site URL for an upload path, or None if unrecoverable."""
        path = urllib.parse.unquote(path.split("?")[0])
        candidates = []
        if path in self.archived:
            candidates.append(path)
        variants = self.by_base.get(SIZE_SUFFIX.sub("", path).lower(), [])
        candidates += sorted(variants, key=lambda p: (SIZE_SUFFIX.search(p) is not None, -area(p)))
        for cand in candidates:
            if self._ensure(cand):
                return "/wp-content/uploads/" + cand
        self.missing.add(path)
        return None

    def _ensure(self, path):
        dest = UPLOADS / path
        if dest.exists():
            return True
        if not self.fetch:
            return False
        original, ts = self.archived[path]
        try:
            data = http_get(f"https://web.archive.org/web/{ts}id_/{original}")
        except Exception as e:
            print(f"  ! failed {path}: {e}")
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print(f"  + {path}")
        return True

    def resolve_external(self, url):
        """Try to recover an externally hosted image (e.g. old Instagram) from the Wayback Machine."""
        if url in self.external:
            return self.external[url]
        name = re.sub(r"[^\w.-]", "_", urllib.parse.urlsplit(url).netloc + urllib.parse.urlsplit(url).path)
        dest = EXTERNAL / name
        result = None
        archived = self.by_name.get(url.rsplit("/", 1)[-1].lower())
        if dest.exists():
            result = "/wp-content/external/" + name
        elif self.fetch:
            try:
                data = http_get(f"https://web.archive.org/web/2014id_/{url}")
                if data[:4] in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\x89PNG", b"GIF8") or data[:3] == b"\xff\xd8\xff":
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    print(f"  + external {url}")
                    result = "/wp-content/external/" + name
            except Exception:
                pass
        if result is None and archived:
            result = self.resolve(archived[0])
        if result is None:
            self.missing.add(url)
        self.external[url] = result
        return result


def area(path):
    m = re.search(r"-(\d+)x(\d+)\.\w+$", path)
    return int(m.group(1)) * int(m.group(2)) if m else 0


def http_get(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cigarpairing-site-builder"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(3 * (attempt + 1))


class Site:
    def __init__(self, dump, fetch):
        self.images = Images(fetch)
        self.posts_by_id = {p["ID"]: p for p in dump["wp_posts"]}
        self.meta = {}
        for m in dump["wp_postmeta"]:
            self.meta.setdefault(m["post_id"], {})[m["meta_key"]] = m["meta_value"]

        terms = {t["term_id"]: t for t in dump["wp_terms"]}
        self.categories = {}
        for tt in dump["wp_term_taxonomy"]:
            if tt["taxonomy"] == "category":
                t = terms[tt["term_id"]]
                self.categories[tt["term_taxonomy_id"]] = {
                    "term_id": t["term_id"], "name": html.unescape(t["name"]), "slug": t["slug"],
                    "parent": tt["parent"], "description": tt["description"], "posts": []}
        cat_of_post = {}
        for r in dump["wp_term_relationships"]:
            if r["term_taxonomy_id"] in self.categories:
                cat_of_post.setdefault(r["object_id"], []).append(self.categories[r["term_taxonomy_id"]])

        self.posts = []
        for p in dump["wp_posts"]:
            if p["post_type"] != "post" or p["post_status"] != "publish":
                continue
            cats = cat_of_post.get(p["ID"], [])
            if any(c["slug"] in EXCLUDED_CATEGORIES for c in cats):
                continue
            p["cats"] = sorted(cats, key=lambda c: c["name"])
            self.posts.append(p)
        self.posts += load_local_posts(self.categories)
        self.posts.sort(key=lambda p: p["post_date"], reverse=True)
        self.pages = [p for p in dump["wp_posts"] if p["post_type"] == "page"
                      and p["post_status"] == "publish" and p["post_name"] in INCLUDED_PAGES]

        self.url_by_id = {p["ID"]: f"/{p['post_name']}/" for p in self.posts + self.pages}
        self.slugs = {p["post_name"] for p in self.posts + self.pages}

        by_term = {c["term_id"]: c for c in self.categories.values()}
        for p in self.posts:
            seen = set()
            for c in p["cats"]:
                while c and c["term_id"] not in seen:
                    seen.add(c["term_id"])
                    c["posts"].append(p)
                    c = by_term.get(c["parent"])
        self.cat_list = sorted((c for c in self.categories.values()
                                if c["posts"] and c["slug"] not in EXCLUDED_CATEGORIES),
                               key=lambda c: c["name"])
        self.cat_slugs = {c["slug"] for c in self.cat_list}
        self.drink_cats = [c for c in self.cat_list if by_term.get(c["parent"], {}).get("slug") == "pairings"]

    # -- content conversion

    def attachment_url(self, att_id):
        path = self.meta.get(int(att_id), {}).get("_wp_attached_file")
        return self.images.resolve(path) if path else None

    def featured(self, post):
        if post.get("header_image"):
            return post["header_image"]
        thumb = self.meta.get(post["ID"], {}).get("_thumbnail_id")
        url = self.attachment_url(thumb) if thumb and str(thumb).isdigit() else None
        if not url:
            m = re.search(r'<img[^>]+src="(/wp-content/[^"]+)"', post["html"])
            url = m.group(1) if m else None
        if not url and (GENERATED / f"{post['post_name']}.jpg").exists():
            url = f"/assets/generated/{post['post_name']}.jpg"
        return url

    def gallery(self, m):
        items = []
        for att_id in shortcode_attrs(m.group(1)).get("ids", "").split(","):
            if att_id.strip().isdigit():
                url = self.attachment_url(att_id.strip())
                if url:
                    items.append(f'<a href="{url}"><img src="{url}" alt="" loading="lazy"></a>')
        return f'<div class="gallery">{"".join(items)}</div>' if items else ""

    def caption(self, m):
        attrs = shortcode_attrs(m.group(1))
        body = m.group(2).strip()
        img = re.match(r"((?:<a [^>]*>)?\s*<img[^>]*>\s*(?:</a>)?)(.*)", body, re.S)
        figure_img, text = (img.group(1), img.group(2).strip()) if img else (body, attrs.get("caption", ""))
        align = attrs.get("align", "alignnone")
        cap = f"<figcaption>{text}</figcaption>" if text else ""
        return f'<figure class="wp-caption {align}">{figure_img}{cap}</figure>'

    def rewrite_link(self, url):
        """Map old nicepair.ca links to new paths. Returns None when the target no longer exists."""
        if not re.match(OLD_HOST, url, re.I) and not url.startswith("/"):
            return url
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(parts.query)
        path = parts.path or "/"
        if "s" in q:
            return "/search/?q=" + urllib.parse.quote(q["s"][0])
        for key in ("p", "page_id"):
            if key in q and q[key][0].isdigit():
                return self.url_by_id.get(int(q[key][0]))
        if path == "/":
            return "/"
        m = re.fullmatch(r"/category/(?:[\w-]+/)*([\w-]+)/?", path)
        if m:
            return f"/category/{m.group(1)}/" if m.group(1) in self.cat_slugs else None
        m = re.fullmatch(r"/([\w-]+)/?", path)
        if m and m.group(1) in self.slugs:
            return f"/{m.group(1)}/"
        return None

    def convert(self, content):
        content = re.sub(r"\[gallery([^\]]*)\]", self.gallery, content)
        content = re.sub(r"\[caption([^\]]*)\](.*?)\[/caption\]", self.caption, content, flags=re.S)
        content = wpautop(content)

        def img(m):
            tag = m.group(0)
            src = re.search(r'src="([^"]*)"', tag)
            if not src:
                return ""
            url = src.group(1)
            up = UPLOAD_RE.match(url)
            if up:
                new = self.images.resolve(up.group(1))
            elif url.startswith("/wp-content/"):
                new = url
            elif re.match(r"https?://", url):
                new = self.images.resolve_external(url)
            else:
                new = None
            if not new:
                return ""
            if not up or not new.endswith("/" + up.group(1)):
                tag = re.sub(r'\s(width|height)="[^"]*"', "", tag)
            tag = tag.replace(src.group(0), f'src="{new}"')
            tag = re.sub(r'\s(srcset|sizes)="[^"]*"', "", tag)
            if "loading=" not in tag:
                tag = tag.replace("<img", '<img loading="lazy"', 1)
            return tag

        content = re.sub(r"<img\b[^>]*>", img, content)

        def anchor(m):
            open_tag, inner = m.group(1), m.group(2)
            href = re.search(r'href="([^"]*)"', open_tag)
            if not href:
                return m.group(0)
            url = html.unescape(href.group(1))
            up = UPLOAD_RE.match(url)
            if up:
                new = self.images.resolve(up.group(1))
                if not new:
                    inner_img = re.search(r'src="(/wp-content/[^"]+)"', inner)
                    new = inner_img.group(1) if inner_img else None
            else:
                new = self.rewrite_link(url)
            if not new:
                return inner
            if not inner.strip():
                return ""
            return open_tag.replace(href.group(0), f'href="{html.escape(new)}"') + inner + "</a>"

        content = re.sub(r"(<a\b[^>]*>)(.*?)</a>", anchor, content, flags=re.S)
        content = re.sub(r'src="//', 'src="https://', content)
        content = re.sub(r'src="http://(www\.)?youtube\.com', r'src="https://\1youtube.com', content)
        content = re.sub(r"(<iframe[^>]*youtube[^>]*></iframe>)", r'<div class="video">\1</div>', content)
        content = re.sub(r'<div class="video-container"><div class="video">(.*?)</div></div>',
                         r'<div class="video">\1</div>', content, flags=re.S)
        content = re.sub(r"<p>\s*(&nbsp;)?\s*</p>", "", content)
        return content

    def excerpt(self, post, words=32):
        if post.get("post_excerpt", "").strip():
            text = post["post_excerpt"]
        else:
            text = re.sub(r"\[[^\]]*\]", " ", post["post_content"])
            text = re.sub(r"<(h\d)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I) if len(text) > 800 else text
            text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html.unescape(text).replace("\xa0", " ")).strip()
        ws = text.split(" ")
        return " ".join(ws[:words]) + ("…" if len(ws) > words else "")

    def prepare(self):
        print("Converting posts and recovering images…")
        for p in self.posts + self.pages:
            p["title"] = html.unescape(p["post_title"]).strip()
            p["html"] = self.convert(p["post_content"])
            p["date"] = datetime.strptime(p["post_date"], "%Y-%m-%d %H:%M:%S")
            desc = p.get("description") or self.meta.get(p["ID"], {}).get("_yoast_wpseo_metadesc")
            p["description"] = html.unescape(desc).strip() if desc else self.excerpt(p, 30)
        for p in self.posts:
            p["image"] = self.featured(p)
            p["summary"] = self.excerpt(p)


# ---------------------------------------------------------------------- render

def esc(s):
    return html.escape(s or "", quote=True)


def fmt_date(d):
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def layout(title, body, description=SITE_TAGLINE, path="/", image=None, og_type="website"):
    full_title = f"{title} | {SITE_NAME}" if title != SITE_NAME else f"{SITE_NAME} — {SITE_TAGLINE}"
    nav = "".join(f'<a href="{u}">{esc(n)}</a>' for n, u in NAV)
    og_image = f'<meta property="og:image" content="{SITE_URL}{esc(image or "/assets/og-default.jpg")}">'
    return f"""<!doctype html>
<html lang="en">
<head>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('consent', 'default', {{ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'denied'}});
  gtag('js', new Date());
  gtag('config', '{GA_ID}');
</script>
<script src="/assets/consent.js" data-ga-id="{GA_ID}" defer></script>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{SITE_URL}{esc(path)}">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{SITE_URL}{esc(path)}">
{og_image}
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="brand" href="/"><img class="brand-logo" src="/assets/logo-light.png" alt="{esc(SITE_NAME)}" width="300" height="229"><span class="brand-tag">{esc(SITE_TAGLINE)}</span></a>
    <nav class="main-nav">{nav}<a class="search-link" href="/search/" aria-label="Search">Search</a></nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>&copy; 2012–{datetime.now().year} {esc(SITE_NAME)}. Archived cigar &amp; drink pairing reviews. Please enjoy responsibly.</p>
    <p><button type="button" class="cookie-settings" data-cookie-settings>Cookie settings</button></p>
  </div>
</footer>
</body>
</html>
"""


def card(p):
    img = (f'<img src="{esc(p["image"])}" alt="" loading="lazy">' if p["image"]
           else f'<span class="placeholder">{esc(p["title"][:1])}</span>')
    cats = " ".join(f'<span class="tag">{esc(c["name"])}</span>' for c in p["cats"][:3])
    return f"""<article class="card">
  <a class="card-img" href="/{p['post_name']}/">{img}</a>
  <div class="card-body">
    <h2><a href="/{p['post_name']}/">{esc(p['title'])}</a></h2>
    <p>{esc(p['summary'])}</p>
    <div class="tags">{cats}</div>
  </div>
</article>"""


def slider(posts, label, variant):
    slides = "".join(f"""<li class="slide" aria-label="{i + 1} of {len(posts)}">
  <a class="slide-img" href="/{p['post_name']}/"><img src="{esc(p['image'])}" alt="{esc(p['title'])}"{' loading="lazy"' if i else ''}></a>
  <div class="slide-body">
    <p class="eyebrow">{esc(label)}</p>
    <h3><a href="/{p['post_name']}/">{esc(p['title'])}</a></h3>
    <p>{esc(p['summary'])}</p>
  </div>
</li>""" for i, p in enumerate(posts))
    dots = "".join(f'<button type="button" aria-label="Show slide {i + 1}"></button>' for i in range(len(posts)))
    return f"""<div class="slider slider-{variant}" aria-roledescription="carousel" aria-label="{esc(label)}">
  <ul class="slides">{slides}</ul>
  <div class="slider-controls">
    <button type="button" class="slider-prev" aria-label="Previous slide">&#8249;</button>
    <div class="slider-dots">{dots}</div>
    <button type="button" class="slider-next" aria-label="Next slide">&#8250;</button>
  </div>
</div>"""


def featured_section(site, title, label, variant, link, link_text, slugs):
    by_slug = {p["post_name"]: p for p in site.posts}
    slides, thumbs = ([by_slug[s] for s in group] for group in slugs)
    return f"""<section class="featured">
  <header class="section-head"><h2>{esc(title)}</h2><a href="{link}">{esc(link_text)} &rarr;</a></header>
  {slider(slides, label, variant)}
  <div class="thumbs">{''.join(card(p) for p in thumbs)}</div>
</section>"""


def sidebar(site):
    drinks = "".join(f'<li><a href="/category/{c["slug"]}/">{esc(c["name"])}</a> <span>{len(c["posts"])}</span></li>'
                     for c in site.drink_cats)
    return f"""<aside class="sidebar">
  <section><h3>Pairings by Drink</h3><ul class="cat-list">{drinks}</ul></section>
</aside>"""


def pagination(base, page, pages):
    if pages <= 1:
        return ""
    def href(n):
        return base if n == 1 else f"{base}page/{n}/"
    links = []
    if page > 1:
        links.append(f'<a href="{href(page - 1)}" rel="prev">&larr; Newer</a>')
    links.append(f"<span>Page {page} of {pages}</span>")
    if page < pages:
        links.append(f'<a href="{href(page + 1)}" rel="next">Older &rarr;</a>')
    return f'<nav class="pagination">{"".join(links)}</nav>'


def write(path, text):
    dest = OUT / path.strip("/") / "index.html" if not path.endswith(".html") else OUT / path.strip("/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def render_listing(site, posts, base, title, heading, description, with_sidebar=True):
    pages = max(1, math.ceil(len(posts) / POSTS_PER_PAGE))
    for n in range(1, pages + 1):
        chunk = posts[(n - 1) * POSTS_PER_PAGE: n * POSTS_PER_PAGE]
        grid = f'<div class="grid">{"".join(card(p) for p in chunk)}</div>'
        listing = f"""<div class="layout">
  {grid}
  {sidebar(site)}
</div>""" if with_sidebar else grid
        body = f"""{heading}
{listing}
{pagination(base, n, pages)}"""
        path = base if n == 1 else f"{base}page/{n}/"
        write(path, layout(title if n == 1 else f"{title} — Page {n}", body, description, path))


def render_post(site, p, idx):
    cats = " ".join(f'<a class="tag" href="/category/{c["slug"]}/">{esc(c["name"])}</a>' for c in p["cats"])
    newer = site.posts[idx - 1] if idx > 0 else None
    older = site.posts[idx + 1] if idx + 1 < len(site.posts) else None
    adj = ""
    if newer or older:
        adj = '<nav class="post-nav">' + \
              (f'<a class="prev" href="/{older["post_name"]}/"><small>Older</small>{esc(older["title"])}</a>' if older else "<span></span>") + \
              (f'<a class="next" href="/{newer["post_name"]}/"><small>Newer</small>{esc(newer["title"])}</a>' if newer else "<span></span>") + \
              "</nav>"
    hero = ""
    if p["image"] and "<img" not in p["html"]:
        hero = f'<img class="post-hero" src="{esc(p["image"])}" alt="{esc(p["title"])}">'
    body = f"""<article class="post">
  <header class="post-header">
    <div class="tags">{cats}</div>
    <h1>{esc(p['title'])}</h1>
  </header>
  {hero}
  <div class="content">
{p['html']}
  </div>
</article>
{adj}"""
    path = f"/{p['post_name']}/"
    write(path, layout(p["title"], body, p["description"], path, p["image"], "article"))


def render_page(p):
    body = f"""<article class="post page">
  <header class="post-header"><h1>{esc(p['title'])}</h1></header>
  <div class="content">
{p['html']}
  </div>
</article>"""
    path = f"/{p['post_name']}/"
    write(path, layout(p["title"], body, p["description"], path))


def render_search(site):
    index = [{"t": p["title"], "u": f"/{p['post_name']}/",
              "c": [c["name"] for c in p["cats"]], "s": p["summary"],
              "x": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p["html"])))[:4000]}
             for p in site.posts]
    (OUT / "search-index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    body = """<h1 class="page-title">Search</h1>
<form class="search-form" action="/search/" role="search">
  <input type="search" name="q" id="q" placeholder="Search cigars, scotch, rum…" aria-label="Search" autofocus>
  <button type="submit">Search</button>
</form>
<p id="search-status" class="muted"></p>
<div id="results" class="results"></div>
<script src="/assets/search.js" defer></script>"""
    write("/search/", layout("Search", body, "Search cigar and drink pairing reviews.", "/search/"))


def render_404():
    body = """<div class="not-found">
  <h1>Page not found</h1>
  <p>That page may have moved when we rebuilt the site. Try searching, or browse the latest pairings.</p>
  <p><a class="button" href="/search/">Search the archive</a> <a class="button ghost" href="/">Home</a></p>
</div>"""
    (OUT / "404.html").write_text(layout("Page not found", body, path="/404.html"), encoding="utf-8")


def render_sitemap(site):
    urls = ["/"] + [f"/category/{c['slug']}/" for c in site.cat_list] + \
           [f"/{p['post_name']}/" for p in site.posts + site.pages]
    lastmod = {f"/{p['post_name']}/": p["post_modified"][:10] for p in site.posts + site.pages}
    items = "".join(f"<url><loc>{SITE_URL}{u}</loc>" + (f"<lastmod>{lastmod[u]}</lastmod>" if u in lastmod else "") + "</url>\n"
                    for u in urls)
    (OUT / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}</urlset>\n',
        encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")


def clean_output():
    """Remove generated HTML but keep recovered images and static assets."""
    keep = {"wp-content", "assets", "CNAME", ".nojekyll"}
    if OUT.exists():
        for child in OUT.iterdir():
            if child.name in keep:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    OUT.mkdir(exist_ok=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    fetch = "--no-fetch" not in sys.argv
    print("Reading dump…")
    site = Site(load_dump(args[0]), fetch)
    site.prepare()

    clean_output()
    (OUT / "CNAME").write_text(DOMAIN + "\n")
    (OUT / ".nojekyll").write_text("")

    intro = f"""<section class="hero">
  <h1>{esc(SITE_TAGLINE)}</h1>
  <p>Hands-on reviews of cigars paired with scotch, whisky, rum, cognac, port, wine and beer — what we smoked, what we poured, and how they played together.</p>
</section>"""
    top = featured_section(site, "Featured Pairings", "Featured pairing", "banner",
                           "/category/pairings/", "All pairings", FEATURED_PAIRINGS) + \
        featured_section(site, "Spirit Guides", "Spirit guide", "wide",
                         "/category/spirit-guides/", "All guides", FEATURED_GUIDES) + \
        '<script src="/assets/slider.js" defer></script>'
    guides = next(c for c in site.cat_list if c["slug"] == "spirit-guides")["posts"]
    home = f"""{top}{intro}
<div class="grid-3">{''.join(card(p) for p in guides[:6])}</div>
<p class="more"><a class="button" href="/category/spirit-guides/">Show All Spirit Guides</a></p>"""
    write("/", layout(SITE_NAME, home, SITE_TAGLINE, "/"))
    for c in site.cat_list:
        desc = c["description"] or f"{c['name']} pairing reviews and articles."
        heading = f'<header class="archive-header"><p class="eyebrow">Category</p><h1>{esc(CATEGORY_HEADINGS.get(c["slug"], c["name"]))}</h1>' + \
                  (f"<p>{esc(c['description'])}</p>" if c["description"] else "") + "</header>"
        render_listing(site, c["posts"], f"/category/{c['slug']}/", c["name"], heading, desc,
                       c["slug"] not in NO_SIDEBAR)
    for i, p in enumerate(site.posts):
        render_post(site, p, i)
    for p in site.pages:
        render_page(p)
    render_search(site)
    render_404()
    render_sitemap(site)

    print(f"\nBuilt {len(site.posts)} posts, {len(site.pages)} pages, {len(site.cat_list)} categories into {OUT}")
    if site.images.missing:
        print(f"{len(site.images.missing)} images could not be recovered (removed from pages):")
        for m in sorted(site.images.missing):
            print("  -", m)


if __name__ == "__main__":
    main()
