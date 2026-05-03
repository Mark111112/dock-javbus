"""FC2 list search provider using JAVten (fc2hub.com) aggregator.

Provides keyword-based list search for FC2 content, returning multiple results
with pagination support, compatible with the standard JavBus keyword results UX.

Design goals:
- Isolated module (does not modify JavBus scraper)
- Returns results in the same structure as JavBus search
- Graceful degradation: if the aggregator is down, returns empty results
"""

import logging
import re
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class FC2ListProvider:
    """FC2 list search via JAVten/fc2hub aggregator.

    Provides multi-result keyword search for FC2 content.  This is the
    "route-2" provider – it returns a list of matching items with
    pagination, as opposed to the exact-ID detail scraper in fc2_scraper.py.
    """

    # JAVten (fc2hub.com) is a public FC2 index/aggregator.
    SEARCH_BASE = "https://javten.com/search"
    DEFAULT_PER_PAGE = 30  # JAVten returns ~30 items per page

    # Regex to extract FC2 numeric IDs from card titles like "FC2-PPV-1234567"
    _FC2_ID_RE = re.compile(r"FC2[-_ ]*PPV[-_ ]*(\d+)", re.I)

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        keyword: str,
        *,
        page: int = 1,
    ) -> Dict:
        """Search FC2 content by keyword, returning a standard result dict.

        Query widening strategy:
        - `FC2-PPV` is surprisingly narrow on JAVten and returns very few rows
        - `FC2` or `PPV` return significantly more rows
        So for broad FC2 intents we fan out several upstream queries and merge results.
        """
        result: Dict = {"movies": [], "pagination": {}, "source": "fc2:javten"}
        if not keyword or not keyword.strip():
            return result

        kw = keyword.strip()
        plans = self._build_search_plans(kw)

        all_movies: List[Dict] = []
        merged_pages = set()
        has_next = False
        seen_ids = set()

        for query_kw in plans:
            url = self._build_search_url(query_kw, page)
            logger.info("[FC2-List] Searching keyword=%r page=%s url=%s", query_kw, page, url)

            resp = self._fetch(url)
            if resp is None:
                logger.warning("[FC2-List] Failed to fetch search page for %r", query_kw)
                continue

            movies = self._parse_movie_cards(resp.text)
            pagination = self._parse_pagination(resp.text, page)
            has_next = has_next or pagination.get("hasNextPage", False)
            merged_pages.update(pagination.get("pages", []) or [])

            for movie in movies:
                movie_id = movie.get("id")
                if not movie_id or movie_id in seen_ids:
                    continue
                seen_ids.add(movie_id)
                all_movies.append(movie)

        result["movies"] = all_movies
        result["pagination"] = {
            "currentPage": page,
            "pages": sorted(merged_pages) if merged_pages else [page],
            "hasNextPage": has_next,
            "nextPage": page + 1 if has_next else page,
        }
        logger.info("[FC2-List] Found %d merged results for keyword=%r page=%s (plans=%s)", len(all_movies), kw, page, plans)
        return result


    def _build_search_plans(self, keyword: str) -> List[str]:
        """Expand broad FC2 intents to wider upstream queries."""
        q = (keyword or '').strip()
        ql = q.lower().replace(' ', '').replace('_', '-')

        # Exact IDs should stay exact; no expansion.
        if self._FC2_ID_RE.search(q):
            return [q]

        plans: List[str] = []

        # Broad FC2 intent queries: widen aggressively.
        if ql in {'fc2-ppv', 'fc2ppv', 'fc2', 'ppv'} or ('fc2' in ql and 'ppv' in ql):
            plans.extend(['FC2', 'PPV', 'FC2-PPV'])
        elif ql.startswith('fc2'):
            plans.extend([q, 'FC2'])
        elif ql.startswith('ppv'):
            plans.extend([q, 'PPV', 'FC2'])
        else:
            plans.append(q)

        # Deduplicate while preserving order.
        deduped: List[str] = []
        seen = set()
        for item in plans:
            key = item.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    # ------------------------------------------------------------------
    # URL builders
    # ------------------------------------------------------------------

    def _build_search_url(self, keyword: str, page: int) -> str:
        params = f"kw={quote_plus(keyword)}"
        if page > 1:
            params += f"&page={page}"
        return f"{self.SEARCH_BASE}?{params}"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _fetch(self, url: str) -> Optional[requests.Response]:
        for verify in (True, False):
            try:
                resp = self.session.get(url, timeout=20, verify=verify)
                if resp.status_code == 200 and len(resp.text) > 500:
                    return resp
                logger.warning(
                    "[FC2-List] HTTP %s len=%d for %s",
                    resp.status_code, len(resp.text), url,
                )
            except Exception as exc:
                logger.warning("[FC2-List] Request error for %s: %s", url, exc)
        return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_movie_cards(self, html: str) -> List[Dict]:
        """Parse movie cards from JAVten search results HTML.

        Expected card structure:
            <div class="card shadow">
              <div class="b-image"><img src="..." ...></div>
              ...
              <div class="card-body">
                <h4 class="card-title">FC2-PPV-1234567</h4>
                <p class="card-text">Description...</p>
                <a href=".../id1234567/..." ...>...</a>
              </div>
            </div>
        """
        movies: List[Dict] = []
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("div.card.shadow")

        for card in cards:
            try:
                movie = self._parse_single_card(card)
                if movie:
                    movies.append(movie)
            except Exception as exc:
                logger.debug("[FC2-List] Failed to parse card: %s", exc)

        return movies

    def _parse_single_card(self, card) -> Optional[Dict]:
        """Extract movie data from a single card element."""
        # Title / FC2 ID from <h4 class="card-title">
        h4 = card.select_one("h4.card-title")
        if not h4:
            return None
        raw_title = h4.get_text(strip=True)
        id_match = self._FC2_ID_RE.search(raw_title)
        if not id_match:
            # Not an FC2 card, skip
            return None
        numeric_id = id_match.group(1)
        canonical_id = f"FC2-PPV-{numeric_id}"

        # Description from <p class="card-text">
        desc_el = card.select_one("p.card-text")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # Image from <img> inside .b-image
        img_url = ""
        img_container = card.select_one(".b-image")
        if img_container:
            img_tag = img_container.find("img")
            if img_tag:
                for attr in ("data-src", "data-original", "src"):
                    candidate = (img_tag.get(attr) or "").strip()
                    if candidate and "loading" not in candidate:
                        img_url = candidate
                        break

        # Link for detail page
        detail_url = ""
        link = card.select_one("a.stretched-link") or card.select_one("a[href*='/id']")
        if link:
            detail_url = link.get("href", "").strip()

        # Date – not always present in card; leave empty
        date = ""

        # Build a movie dict compatible with the standard keyword results list
        movie: Dict = {
            "id": canonical_id,
            "title": description or canonical_id,
            "img": img_url,
            "date": date,
            "tags": [],
            "data_source": "fc2:javten",
            "product_code": canonical_id,
            # Extra fields useful for detail enrichment
            "original_title": raw_title,
            "description": description,
            "detail_url": detail_url,
            "series": {"id": "fc2", "name": "FC2"},
            "genres": [],
            "stars": [],
            "magnets": [],
            "samples": [],
        }
        return movie

    def _parse_pagination(self, html: str, current_page: int) -> Dict:
        """Parse pagination from JAVten search results.

        Returns a dict compatible with InternalJavbusClient._build_pagination().
        """
        soup = BeautifulSoup(html, "html.parser")

        pages: List[int] = [current_page]
        has_next = False

        try:
            pagination_ul = soup.find("ul", class_="pagination")
            if not pagination_ul:
                return {
                    "currentPage": current_page,
                    "pages": pages,
                    "hasNextPage": False,
                    "nextPage": current_page,
                }

            # Collect all page numbers from links
            for link in pagination_ul.find_all("a", href=True):
                href = link.get("href", "")
                page_match = re.search(r"page=(\d+)", href)
                if page_match:
                    pn = int(page_match.group(1))
                    if pn not in pages:
                        pages.append(pn)

            pages.sort()
            max_page = max(pages) if pages else current_page
            has_next = current_page < max_page

            # Check for "next" / right-arrow link
            next_links = pagination_ul.find_all("a", string=re.compile(r"›|Next|›"))
            if not next_links:
                # Check rel or aria-label
                next_links = pagination_ul.find_all("a", attrs={"rel": "next"})
            if next_links:
                has_next = True

        except Exception as exc:
            logger.debug("[FC2-List] Pagination parse error: %s", exc)

        return {
            "currentPage": current_page,
            "pages": pages,
            "hasNextPage": has_next,
            "nextPage": current_page + 1 if has_next else current_page,
        }
