"""IACR ePrint metadata fetcher, derived from Paper Pulse (GPL-3.0)."""

import calendar
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import ClassVar

import feedparser
import requests


class IACRFetcher:
    """Fetch first-publication metadata from IACR's official OAI-PMH feed."""

    OAI_URL = "https://eprint.iacr.org/oai"
    # The recent RSS is intentionally only a fallback: it is capped at 100 items.
    RSS_URL = "https://eprint.iacr.org/rss/rss.xml?order=recent"
    NAMESPACES: ClassVar[dict[str, str]] = {
        "oai": "http://www.openarchives.org/OAI/2.0/",
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    def __init__(self, delay=1.0, timeout=45):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "PaperDigest/1.0 (personal research aggregator)",
                "Accept": "application/xml, text/xml, application/rss+xml, */*",
            }
        )
        self.complete = True

    def fetch_papers(self, since):
        since = since.astimezone(timezone.utc)
        try:
            papers = self._fetch_oai(since)
            self.complete = True
            print(f"IACR OAI: fetched {len(papers)} first-publication records")
            return papers
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            # OAI is the only source that can fully backfill. RSS keeps a daily run
            # useful during a brief OAI outage, without pretending it is complete.
            self.complete = False
            print(f"IACR OAI unavailable ({type(exc).__name__}); using recent RSS")
            papers = self._fetch_rss(since)
            print(f"IACR RSS fallback: fetched {len(papers)} records (100-item cap)")
            return papers

    def _fetch_oai(self, since):
        params = {
            "verb": "ListRecords",
            "metadataPrefix": "oai_dc",
            # The server treats this as a date boundary. Exact filtering happens
            # below using the earliest dc:date (the first publication timestamp).
            "from": since.strftime("%Y-%m-%d"),
        }
        papers = {}

        while True:
            response = self.session.get(
                self.OAI_URL,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            error = root.find("oai:error", self.NAMESPACES)
            if error is not None:
                if error.get("code") == "noRecordsMatch":
                    return []
                raise ValueError(f"IACR OAI error: {error.get('code', 'unknown')}")

            records_node = root.find("oai:ListRecords", self.NAMESPACES)
            if records_node is None:
                raise ValueError("IACR OAI response missing ListRecords")

            for record in records_node.findall("oai:record", self.NAMESPACES):
                paper = self._parse_oai_record(record, since)
                if paper:
                    papers[paper["id"]] = paper

            token_node = records_node.find("oai:resumptionToken", self.NAMESPACES)
            token = (token_node.text or "").strip() if token_node is not None else ""
            if not token:
                break
            params = {"verb": "ListRecords", "resumptionToken": token}
            time.sleep(self.delay)

        return list(papers.values())

    def _parse_oai_record(self, record, since):
        header = record.find("oai:header", self.NAMESPACES)
        if header is None or header.get("status") == "deleted":
            return None

        identifier = header.findtext("oai:identifier", "", self.NAMESPACES)
        match = re.search(r"(\d{4}/\d+)$", identifier)
        if not match:
            return None
        paper_id = match.group(1)

        dates = []
        for node in record.findall(".//dc:date", self.NAMESPACES):
            parsed = _parse_date(node.text)
            if parsed:
                dates.append(parsed)
        if not dates:
            return None
        published_at = min(dates)
        if published_at < since:
            return None

        title = record.findtext(".//dc:title", "", self.NAMESPACES)
        authors = [
            _clean(node.text)
            for node in record.findall(".//dc:creator", self.NAMESPACES)
            if _clean(node.text)
        ]
        categories = [
            _clean(node.text)
            for node in record.findall(".//dc:subject", self.NAMESPACES)
            if _clean(node.text)
        ]
        descriptions = [
            _clean(node.text)
            for node in record.findall(".//dc:description", self.NAMESPACES)
            if _clean(node.text)
        ]
        updated_at = _parse_date(header.findtext("oai:datestamp", "", self.NAMESPACES))
        return _paper(
            paper_id,
            title,
            authors,
            " ".join(descriptions),
            categories,
            published_at,
            updated_at,
        )

    def _fetch_rss(self, since):
        response = self.session.get(self.RSS_URL, timeout=self.timeout)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ValueError(f"IACR RSS parse failed: {feed.bozo_exception}")

        papers = []
        for entry in feed.entries:
            parsed = entry.get("published_parsed")
            if not parsed:
                continue
            published_at = datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
            if published_at < since:
                continue

            match = re.search(r"/(\d{4}/\d+)(?:\.pdf)?$", entry.get("link", ""))
            if not match:
                continue
            paper_id = match.group(1)
            authors = [a.get("name", "").strip() for a in entry.get("authors", [])]
            authors = [author for author in authors if author]
            if not authors and entry.get("author"):
                authors = [entry.author.strip()]
            categories = [
                tag.get("term") for tag in entry.get("tags", []) if tag.get("term")
            ]
            papers.append(
                _paper(
                    paper_id,
                    entry.get("title", ""),
                    authors,
                    entry.get("summary", entry.get("description", "")),
                    categories,
                    published_at,
                )
            )
        time.sleep(self.delay)
        return papers


def _paper(
    paper_id,
    title,
    authors,
    abstract,
    categories,
    published_at,
    updated_at=None,
):
    result = {
        "id": f"iacr_{paper_id}",
        "iacr_id": paper_id,
        "title": _clean(title),
        "authors": authors,
        "abstract": _clean(abstract),
        "published": published_at.strftime("%Y-%m-%d"),
        "published_at": _iso(published_at),
        "source": "IACR",
        "url": f"https://eprint.iacr.org/{paper_id}",
        "pdf_link": f"https://eprint.iacr.org/{paper_id}.pdf",
        "categories": categories or ["Cryptography"],
    }
    if updated_at:
        result["updated_at"] = _iso(updated_at)
    return result


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
