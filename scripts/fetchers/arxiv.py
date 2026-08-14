"""arXiv metadata fetcher, derived from Paper Pulse (GPL-3.0)."""

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests


class ArxivFetcher:
    BASE_URL = "https://export.arxiv.org/api/query"
    RATE_LIMIT_RETRIES = 5
    RATE_LIMIT_FALLBACK_SECONDS = 30.0
    NETWORK_RETRIES = 5
    NETWORK_RETRY_BASE_SECONDS = 5.0

    def __init__(
        self,
        categories,
        delay=3.0,
        batch_size=100,
        timeout=45,
    ):
        self.categories = categories
        self.delay = delay
        self.batch_size = batch_size
        self.timeout = timeout

    def fetch_papers(self, since):
        since = since.astimezone(timezone.utc)
        if not self.categories:
            return []
        search_query = " OR ".join(f"cat:{category}" for category in self.categories)
        if len(self.categories) > 1:
            search_query = f"({search_query})"
        print(f"arXiv: fetching {', '.join(self.categories)}")
        by_id = {paper["id"]: paper for paper in self._fetch_query(search_query, since)}
        papers = list(by_id.values())
        print(f"arXiv: fetched {len(papers)} first-publication records")
        return papers

    def _fetch_category(self, category, since):
        return self._fetch_query(f"cat:{category}", since)

    def _fetch_query(self, search_query, since):
        papers = []
        start = 0
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        # Continue until the requested first-publication cutoff is reached.
        # A fixed result cap would silently lose papers after a long outage.
        while True:
            response = self._get(
                {
                    "search_query": search_query,
                    "start": start,
                    "max_results": self.batch_size,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                }
            )
            response.raise_for_status()
            entries = ET.fromstring(response.content).findall("atom:entry", ns)
            if not entries:
                break

            reached_cutoff = False
            for entry in entries:
                published_text = _text(entry, "atom:published", ns)
                raw_id = _text(entry, "atom:id", ns)
                title = _text(entry, "atom:title", ns)
                abstract = _text(entry, "atom:summary", ns)
                if not all((published_text, raw_id, title, abstract)):
                    continue

                published_at = datetime.fromisoformat(
                    published_text.replace("Z", "+00:00")
                )
                if published_at < since:
                    reached_cutoff = True
                    continue

                # arXiv revisions may expose a vN suffix. Removing it makes the
                # first paper ID stable and prevents revisions becoming new mail items.
                arxiv_id = re.sub(r"v\d+$", "", raw_id.rsplit("/abs/", 1)[-1])
                authors = [
                    _text(author, "atom:name", ns)
                    for author in entry.findall("atom:author", ns)
                ]
                authors = [author for author in authors if author]
                categories = []
                primary = entry.find("arxiv:primary_category", ns)
                if primary is not None and primary.get("term"):
                    categories.append(primary.get("term"))
                for item in entry.findall("atom:category", ns):
                    term = item.get("term")
                    if term and term not in categories:
                        categories.append(term)

                papers.append(
                    {
                        "id": f"arxiv_{arxiv_id}",
                        "arxiv_id": arxiv_id,
                        "title": _clean(title),
                        "authors": authors,
                        "abstract": _clean(abstract),
                        "published": published_at.strftime("%Y-%m-%d"),
                        "published_at": _iso(published_at),
                        "source": "arXiv",
                        "url": f"https://arxiv.org/abs/{arxiv_id}",
                        "pdf_link": f"https://arxiv.org/pdf/{arxiv_id}",
                        "categories": categories,
                    }
                )

            if reached_cutoff or len(entries) < self.batch_size:
                break
            start += self.batch_size
            time.sleep(self.delay)

        return papers

    def _get(self, params):
        network_retries = 0
        rate_limit_retries = 0
        while True:
            try:
                response = requests.get(
                    self.BASE_URL,
                    params=params,
                    headers={
                        "User-Agent": "PaperDigest/1.0 (personal research aggregator)"
                    },
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if network_retries >= self.NETWORK_RETRIES:
                    raise
                wait = self.NETWORK_RETRY_BASE_SECONDS * (2**network_retries)
                network_retries += 1
                print(
                    f"arXiv {type(exc).__name__}; retrying in {wait:g}s "
                    f"({network_retries}/{self.NETWORK_RETRIES})"
                )
                time.sleep(wait)
                continue

            if response.status_code != 429:
                return response

            if rate_limit_retries >= self.RATE_LIMIT_RETRIES:
                return response
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                wait = self.RATE_LIMIT_FALLBACK_SECONDS * (
                    2**rate_limit_retries
                )
            rate_limit_retries += 1
            print(
                f"arXiv rate limited; retrying in {wait:g}s "
                f"({rate_limit_retries}/{self.RATE_LIMIT_RETRIES})"
            )
            time.sleep(wait)


def _text(element, path, namespace):
    child = element.find(path, namespace)
    return child.text if child is not None else ""


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
