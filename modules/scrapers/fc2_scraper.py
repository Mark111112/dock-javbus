import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class FC2Scraper:
    """FC2 metadata scraper.

    Goals:
    - Support FC2-PPV / PPV / pure number / article URL normalization
    - Primary: scrape official page adult.contents.fc2.com/article/{id}/
    - Fallback: fc2club.net/html/FC2-{id}.html
    - Return structure compatible with bus movie_data
    - Support list search (via FC2ListProvider) returning standard keyword results UX
    """

    ID_PATTERNS = [
        re.compile(r"FC2[-_ ]*PPV[-_ ]*(?P<id>\d{2,10})", re.I),
        re.compile(r"\bPPV[-_ ]*(?P<id>\d{2,10})\b", re.I),
        re.compile(r"adult\.contents\.fc2\.com/article/(?P<id>\d{2,10})/?", re.I),
        re.compile(r"fc2club\.(?:net|com)/html/FC2[-_ ]*(?P<id>\d{2,10})\.html", re.I),
        re.compile(r"\b(?P<id>\d{5,10})\b"),
    ]

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self.official_base = "https://adult.contents.fc2.com"
        self.fallback_bases = ["https://fc2club.net", "https://fc2club.com"]
        # Lazy-initialized list provider (avoids import at module level)
        self._list_provider = None

    @property
    def list_provider(self):
        """Lazy-init the FC2 list provider to avoid circular imports."""
        if self._list_provider is None:
            from .fc2_list_provider import FC2ListProvider
            self._list_provider = FC2ListProvider()
        return self._list_provider

    def is_fc2_query(self, query: str) -> bool:
        """Exact FC2 id recognition only (used by detail/id paths)."""
        return self.normalize_id(query) is not None

    def is_fc2_search_keyword(self, query: str) -> bool:
        """Broader FC2 search keyword recognition (used by /search_keyword).

        Matches:
        - exact IDs (FC2-PPV-123456, PPV-123456, etc.)
        - generic prefixes like FC2, FC2-PPV, FC2PPV
        - mixed text containing FC2/PPV intent
        """
        q = (query or '').strip().lower()
        if not q:
            return False
        if self.is_fc2_query(q):
            return True
        if q in {'fc2', 'fc2-ppv', 'fc2ppv', 'ppv'}:
            return True
        if 'fc2' in q and 'ppv' in q:
            return True
        if q.startswith('fc2') or q.startswith('ppv'):
            return True
        return False

    def normalize_id(self, query: str) -> Optional[str]:
        if not query:
            return None
        q = query.strip()
        for pattern in self.ID_PATTERNS:
            m = pattern.search(q)
            if m:
                return f"FC2-PPV-{m.group('id')}"
        return None

    def _extract_numeric_id(self, movie_id: str) -> Optional[str]:
        canonical = self.normalize_id(movie_id)
        if not canonical:
            return None
        return canonical.rsplit('-', 1)[-1]

    def _get(self, url: str) -> Optional[requests.Response]:
        for verify in (True, False):
            try:
                resp = self.session.get(url, timeout=30, verify=verify)
                if resp.status_code == 200 and resp.text:
                    return resp
                logger.warning("FC2 request failed: %s -> HTTP %s", url, resp.status_code)
            except Exception as e:
                logger.warning("FC2 request error: %s -> %s", url, e)
        return None

    def _parse_date(self, text: str) -> str:
        if not text:
            return ""
        m = re.search(r"(\d{4})[/-](\d{2})[/-](\d{2})", text)
        if not m:
            return ""
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""

    def _clean_title(self, title: str, canonical_id: str) -> str:
        if not title:
            return canonical_id
        title = re.sub(r"\s+", " ", title).strip()
        title = re.sub(r"^FC2[-_ ]*PPV[-_ ]*\d+\s*", "", title, flags=re.I).strip(" -:")
        title = re.sub(rf"^{re.escape(canonical_id)}\s*", "", title, flags=re.I).strip(" -:")
        return title or canonical_id

    def _make_genres(self, names: List[str]) -> List[Dict]:
        out = []
        seen = set()
        for name in names or []:
            n = (name or "").strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append({"id": n, "name": n})
        return out

    def _make_samples(self, urls: List[str]) -> List[Dict]:
        out = []
        seen = set()
        for url in urls or []:
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({"src": url, "thumbnail": url})
        return out

    def _parse_official(self, numeric_id: str, canonical_id: str) -> Optional[Dict]:
        url = f"{self.official_base}/article/{numeric_id}/"
        resp = self._get(url)
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        def meta(prop: str) -> str:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            return (tag.get("content") or "").strip() if tag else ""

        raw_title = meta("og:title")
        title = self._clean_title(raw_title, canonical_id)
        description = meta("og:description")
        cover = meta("og:image")
        if cover.startswith("//"):
            cover = "https:" + cover
        trailer = meta("og:video")

        seller = ""
        seller_link = soup.select_one(".items_article_headerInfo a[href*='/users/']")
        if seller_link:
            seller = seller_link.get_text(strip=True)

        tags = [x.get_text(strip=True) for x in soup.select(".items_article_TagArea a.tag, .tag.tagTag")]
        date_text = ""
        for el in soup.select(".items_article_softDevice p"):
            txt = el.get_text(" ", strip=True)
            if "販売日" in txt or "配信" in txt or "Releasedate" in txt:
                date_text = txt
                break
        date = self._parse_date(date_text)

        screenshots = []
        for a in soup.select(".items_article_SampleImagesArea a[href]"):
            href = (a.get("href") or "").strip()
            if href:
                screenshots.append(urljoin(url, href if not href.startswith("//") else "https:" + href[2:]))
        if not screenshots:
            for img in soup.select(".items_article_SampleImagesArea img[src]"):
                src = (img.get("src") or "").strip()
                if src.startswith("//"):
                    src = "https:" + src
                elif src:
                    src = urljoin(url, src)
                if src:
                    screenshots.append(src)

        rating = None
        try:
            for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
                raw = script.get_text(strip=True)
                if not raw:
                    continue
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("aggregateRating"):
                    rating = data["aggregateRating"].get("ratingValue")
                    break
        except Exception:
            pass

        movie = {
            "id": canonical_id,
            "title": title,
            "original_title": raw_title or canonical_id,
            "date": date,
            "img": cover,
            "description": description,
            "videoLength": "",
            "director": {"id": "", "name": ""},
            "publisher": {"id": seller, "name": seller},
            "producer": {"id": seller, "name": seller},
            "series": {"id": "fc2", "name": "FC2"},
            "genres": self._make_genres(tags),
            "stars": [],
            "data_source": "fc2:official",
            "samples": self._make_samples(screenshots),
            "product_code": canonical_id,
            "magnets": [],
            "trailer_url": trailer,
        }
        if rating is not None:
            movie["community_rating"] = rating
        return movie

    def _parse_fc2club(self, numeric_id: str, canonical_id: str) -> Optional[Dict]:
        resp = None
        url = ""
        for base in self.fallback_bases:
            candidate = f"{base}/html/FC2-{numeric_id}.html"
            resp = self._get(candidate)
            if resp:
                url = candidate
                break
        if not resp:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")

        title = canonical_id
        title_el = soup.select_one(".show-top-grids h3") or soup.find("h3")
        if title_el:
            title = self._clean_title(title_el.get_text(" ", strip=True), canonical_id)

        info = {}
        for row in soup.select(".show-top-grids h5"):
            text = row.get_text(" ", strip=True)
            if "：" in text:
                k, v = text.split("：", 1)
                info[k.strip()] = v.strip()
            elif ":" in text:
                k, v = text.split(":", 1)
                info[k.strip()] = v.strip()

        seller = info.get("卖家信息", "") or info.get("販売者", "")
        date = self._parse_date(info.get("影片日期", "") or info.get("配信日", ""))
        tags = []
        tag_text = info.get("影片标签", "") or info.get("タグ", "")
        if tag_text:
            tags = [x.strip() for x in re.split(r",|/|、", tag_text) if x.strip()]

        screenshots = []
        for img in soup.select("ul.slides img[src]"):
            src = (img.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            elif src:
                src = urljoin(url, src)
            if src:
                screenshots.append(src)

        cover = screenshots[0] if screenshots else ""
        movie = {
            "id": canonical_id,
            "title": title,
            "original_title": canonical_id,
            "date": date,
            "img": cover,
            "description": "",
            "videoLength": "",
            "director": {"id": "", "name": ""},
            "publisher": {"id": seller, "name": seller},
            "producer": {"id": seller, "name": seller},
            "series": {"id": "fc2", "name": "FC2"},
            "genres": self._make_genres(tags),
            "stars": [],
            "data_source": "fc2:fc2club",
            "samples": self._make_samples(screenshots),
            "product_code": canonical_id,
            "magnets": [],
        }
        return movie

    def _merge_movie(self, primary: Dict, fallback: Optional[Dict]) -> Dict:
        if not fallback:
            return primary
        merged = dict(primary)
        for key in ["description", "date", "img", "videoLength"]:
            if not merged.get(key) and fallback.get(key):
                merged[key] = fallback[key]
        if (not merged.get("publisher") or not merged.get("publisher", {}).get("name")) and fallback.get("publisher"):
            merged["publisher"] = fallback["publisher"]
        if (not merged.get("producer") or not merged.get("producer", {}).get("name")) and fallback.get("producer"):
            merged["producer"] = fallback["producer"]
        if not merged.get("genres") and fallback.get("genres"):
            merged["genres"] = fallback["genres"]
        if not merged.get("samples") and fallback.get("samples"):
            merged["samples"] = fallback["samples"]
        if not merged.get("img") and fallback.get("img"):
            merged["img"] = fallback["img"]
        return merged

    def get_movie_info(self, movie_id: str) -> Optional[Dict]:
        canonical_id = self.normalize_id(movie_id)
        if not canonical_id:
            return None
        numeric_id = self._extract_numeric_id(canonical_id)
        if not numeric_id:
            return None

        official = self._parse_official(numeric_id, canonical_id)
        fallback = self._parse_fc2club(numeric_id, canonical_id)

        if official and fallback:
            return self._merge_movie(official, fallback)
        return official or fallback

    def search_movies(self, keyword: str) -> List[Dict]:
        movie = self.get_movie_info(keyword)
        return [movie] if movie else []

    # ------------------------------------------------------------------
    # Phase 2: Standard keyword results list UX
    # ------------------------------------------------------------------

    def search_keyword(self, keyword: str, *, page: int = 1) -> Dict:
        """Unified FC2 search with standard keyword results list UX.

        Strategy:
        1. If keyword is an exact FC2 ID -> detail lookup wrapped in
           standard list/pagination format (single-item list).
        2. If keyword is broader (or exact ID failed), delegate to
           FC2ListProvider for aggregator-based multi-result search.
        3. Graceful degradation: if list provider fails, fall back to
           exact-ID attempt.

        Returns a dict matching the JavBus search result structure:
            {
                "movies": [...],
                "pagination": {...},
            }
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return {"movies": [], "pagination": _empty_pagination()}

        # Try exact-ID match first (highest confidence, page 1 only)
        canonical = self.normalize_id(keyword)
        if canonical and page == 1:
            exact = self._exact_id_as_list(canonical)
            if exact["movies"]:
                return exact

        # Try list search via aggregator (works for any keyword)
        try:
            list_result = self.list_provider.search(keyword, page=page)
            if list_result.get("movies"):
                return {
                    "movies": list_result["movies"],
                    "pagination": list_result.get("pagination", {}),
                }
        except Exception as exc:
            logger.warning("[FC2] List provider failed for %r: %s", keyword, exc)

        # Fallback: if we had a canonical ID but exact detail failed, try list by numeric ID
        if canonical and page == 1:
            numeric = self._extract_numeric_id(canonical)
            if numeric:
                try:
                    list_result = self.list_provider.search(numeric, page=1)
                    if list_result.get("movies"):
                        return {
                            "movies": list_result["movies"],
                            "pagination": list_result.get("pagination", {}),
                        }
                except Exception:
                    pass

        # Last resort: empty results
        return {"movies": [], "pagination": _empty_pagination()}

    def _exact_id_as_list(self, canonical_id: str) -> Dict:
        """Wrap exact-ID detail lookup in standard list/pagination format.

        This makes the exact-ID path return the same structure as JavBus
        keyword search, so the frontend can render it uniformly.
        """
        movie = self.get_movie_info(canonical_id)
        if not movie:
            return {"movies": [], "pagination": _empty_pagination()}

        # Normalize movie to standard list-item format (same as JavBus)
        list_item = _movie_to_list_item(movie)
        return {
            "movies": [list_item],
            "pagination": {
                "currentPage": 1,
                "pages": [1],
                "hasNextPage": False,
                "nextPage": 1,
            },
        }


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _empty_pagination() -> Dict:
    return {
        "currentPage": 1,
        "pages": [],
        "hasNextPage": False,
        "nextPage": 1,
    }


def _movie_to_list_item(movie: Dict) -> Dict:
    """Convert a full FC2 movie dict to the standard keyword results list-item format.

    This mirrors what webserver.py does for JavBus results in search_keyword:
        {id, title, img, date, tags, translated_title, data_source}
    plus we preserve extra detail fields for downstream consumption.
    """
    return {
        "id": movie.get("id", ""),
        "title": movie.get("title", ""),
        "img": movie.get("img", ""),
        "date": movie.get("date", ""),
        "tags": movie.get("tags", []),
        "translated_title": movie.get("translated_title", ""),
        "data_source": movie.get("data_source", "fc2"),
        # Preserve full detail for detail pages
        "original_title": movie.get("original_title", ""),
        "description": movie.get("description", ""),
        "genres": movie.get("genres", []),
        "stars": movie.get("stars", []),
        "samples": movie.get("samples", []),
        "magnets": movie.get("magnets", []),
        "series": movie.get("series", {"id": "fc2", "name": "FC2"}),
        "publisher": movie.get("publisher", {}),
        "producer": movie.get("producer", {}),
        "product_code": movie.get("product_code", movie.get("id", "")),
    }
