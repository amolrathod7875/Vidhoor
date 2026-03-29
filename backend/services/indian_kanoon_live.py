from __future__ import annotations

from datetime import datetime
import logging
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

INDIAN_KANOON_BASE_URL = "https://indiankanoon.org"
INDIAN_KANOON_SEARCH_URL = f"{INDIAN_KANOON_BASE_URL}/search/"

GENERIC_QUERY_TERMS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "case",
    "cases",
    "for",
    "from",
    "give",
    "in",
    "india",
    "indian",
    "is",
    "law",
    "legal",
    "latest",
    "me",
    "new",
    "of",
    "on",
    "or",
    "recent",
    "related",
    "some",
    "the",
    "this",
    "to",
    "year",
}


def _normalize_query(query: str) -> str:
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    text = re.sub(r"\[PII_[A-Z0-9_]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize_for_relevance(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _extract_query_keywords(query: str) -> list[str]:
    tokens = _tokenize_for_relevance(query)
    keywords: list[str] = []
    for token in tokens:
        if len(token) < 3:
            continue
        if token in GENERIC_QUERY_TERMS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords


def _query_mentions_recent(query: str) -> bool:
    normalized = str(query or "").lower()
    return any(term in normalized for term in ("recent", "latest", "this year", "current", "new"))


def _extract_year_hint(text: str) -> int | None:
    years = re.findall(r"\b((?:19|20)\d{2})\b", str(text or ""))
    if not years:
        return None
    try:
        return max(int(item) for item in years)
    except ValueError:
        return None


def _compute_relevance_score(
    *,
    query: str,
    query_keywords: list[str],
    title: str,
    metadata_text: str,
    snippet_text: str,
) -> float:
    haystack_text = " ".join([title, metadata_text, snippet_text]).strip().lower()
    haystack_tokens = set(_tokenize_for_relevance(haystack_text))

    if not query_keywords:
        base_score = 1.0
    else:
        keyword_matches = sum(1 for keyword in query_keywords if keyword in haystack_tokens)
        base_score = float(keyword_matches)

    phrase_bonus = 0.0
    for phrase in ("section", "article", "vs", "v.", "high court", "supreme court"):
        if phrase in haystack_text:
            phrase_bonus += 0.1

    recent_bonus = 0.0
    if _query_mentions_recent(query):
        year_hint = _extract_year_hint(haystack_text)
        current_year = datetime.now().year
        if year_hint == current_year:
            recent_bonus += 1.0
        elif year_hint == current_year - 1:
            recent_bonus += 0.6
        elif year_hint is not None and year_hint >= current_year - 3:
            recent_bonus += 0.3

    title_keyword_bonus = 0.0
    title_tokens = set(_tokenize_for_relevance(title))
    for keyword in query_keywords:
        if keyword in title_tokens:
            title_keyword_bonus += 0.4

    return base_score + phrase_bonus + recent_bonus + title_keyword_bonus


def fetch_indian_kanoon_case_links(query: str, max_links: int = 3, timeout_seconds: int = 8) -> list[dict[str, str]]:
    """Fetch recent Indian Kanoon case links for a legal query.

    Returns a list of dictionaries with keys: title, url, court, date.
    This function does not persist any scraped data.
    """
    normalized_query = _normalize_query(query)
    if not normalized_query:
        return []

    capped_limit = max(1, min(int(max_links or 3), 5))
    params = {
        "formInput": normalized_query,
        "sortby": "mostrecent",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            INDIAN_KANOON_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Indian Kanoon request failed: %s", exc)
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    query_keywords = _extract_query_keywords(normalized_query)
    links: list[dict[str, str]] = []
    ranked_candidates: list[tuple[float, dict[str, str]]] = []
    seen_urls: set[str] = set()
    seen_title_keys: set[str] = set()
    query_wants_recent = _query_mentions_recent(normalized_query)

    def _push_result(
        item_title: str,
        item_url: str,
        *,
        metadata_text: str = "",
        snippet_text: str = "",
        item_court: str = "",
        item_date: str = "",
    ) -> None:
        cleaned_url = str(item_url or "").strip()
        if not cleaned_url:
            return
        absolute_url = urljoin(INDIAN_KANOON_BASE_URL, cleaned_url)
        if absolute_url in seen_urls:
            return

        title_text = re.sub(r"\s+", " ", str(item_title or "")).strip()
        if not title_text:
            title_text = "Indian Kanoon Case"

        title_key = re.sub(r"[^a-z0-9]+", " ", title_text.lower()).strip()
        if title_key in seen_title_keys:
            return

        if query_wants_recent:
            year_hint = _extract_year_hint(f"{title_text} {item_date}")
            current_year = datetime.now().year
            if year_hint is not None and year_hint < current_year - 5:
                return

        score = _compute_relevance_score(
            query=normalized_query,
            query_keywords=query_keywords,
            title=title_text,
            metadata_text=metadata_text,
            snippet_text=snippet_text,
        )
        if query_keywords and score <= 0:
            return

        seen_urls.add(absolute_url)
        seen_title_keys.add(title_key)
        candidate = {
            "title": title_text,
            "url": absolute_url,
            "court": re.sub(r"\s+", " ", str(item_court or "")).strip(),
            "date": re.sub(r"\s+", " ", str(item_date or "")).strip(),
        }
        links.append(candidate)
        ranked_candidates.append((score, candidate))

    for container in soup.select("article.result"):
        title_anchor = container.select_one("h4.result_title a") or container.select_one("a[href*='/docfragment/']")
        if not title_anchor:
            continue

        full_doc_anchor = container.select_one("div.hlbottom a[href*='/doc/']")
        href = full_doc_anchor.get("href") if full_doc_anchor else title_anchor.get("href")
        title = title_anchor.get_text(" ", strip=True)

        metadata_text = container.select_one("div.hlbottom").get_text(" ", strip=True) if container.select_one("div.hlbottom") else ""
        if not metadata_text:
            metadata_text = container.get_text(" ", strip=True)

        date_match = re.search(r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}-\d{1,2}-\d{4}|\d{4})\b", metadata_text)
        if not date_match:
            title_date_match = re.search(r"\bon\s+(\d{1,2}\s+[A-Za-z]+,?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", title, flags=re.IGNORECASE)
            item_date = title_date_match.group(1) if title_date_match else ""
        else:
            item_date = date_match.group(1)

        court_node = container.select_one("div.hlbottom span.docsource")
        item_court = court_node.get_text(" ", strip=True) if court_node else ""
        if not item_court:
            court_match = re.search(r"\b(Supreme Court(?: of India)?|[A-Za-z\- ]+High Court|District Court[^,.;]*|Tribunal[^,.;]*)\b", metadata_text, flags=re.IGNORECASE)
            item_court = court_match.group(1) if court_match else ""

        snippet_node = container.select_one("div.headline") or container.select_one("div.snippet") or container.select_one("div.result_snippet") or container.select_one("pre")
        snippet_text = snippet_node.get_text(" ", strip=True) if snippet_node else ""

        _push_result(
            title,
            str(href or ""),
            metadata_text=metadata_text,
            snippet_text=snippet_text,
            item_court=item_court,
            item_date=item_date,
        )

    if len(links) < max(capped_limit * 2, 6):
        for anchor in soup.select("a[href*='/doc/']"):
            href = anchor.get("href")
            if anchor.get_text(" ", strip=True).strip().lower() == "full document":
                container = anchor.find_parent("article")
                title_anchor = container.select_one("h4.result_title a") if container else None
                title = title_anchor.get_text(" ", strip=True) if title_anchor else ""
                metadata_text = container.select_one("div.hlbottom").get_text(" ", strip=True) if container and container.select_one("div.hlbottom") else ""
                snippet_text = container.select_one("div.headline").get_text(" ", strip=True) if container and container.select_one("div.headline") else ""
                court_node = container.select_one("div.hlbottom span.docsource") if container else None
                item_court = court_node.get_text(" ", strip=True) if court_node else ""
                title_date_match = re.search(r"\bon\s+(\d{1,2}\s+[A-Za-z]+,?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\b", title, flags=re.IGNORECASE)
                item_date = title_date_match.group(1) if title_date_match else ""
            else:
                title = anchor.get_text(" ", strip=True)
                metadata_text = title
                snippet_text = ""
                item_court = ""
                item_date = ""

            _push_result(
                title,
                str(href or ""),
                metadata_text=metadata_text,
                snippet_text=snippet_text,
                item_court=item_court,
                item_date=item_date,
            )
            if len(links) >= max(capped_limit * 2, 10):
                break

    ranked_candidates.sort(key=lambda item: item[0], reverse=True)
    top_ranked = [candidate for _, candidate in ranked_candidates[:capped_limit]]

    if top_ranked:
        return top_ranked

    return links[:capped_limit]
