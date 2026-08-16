"""
RSS feed generator for Paper Pulse.

Copyright (C) 2024-2026 Paper Pulse Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path


def _date_to_rfc822(date_str: str) -> str:
    """Convert YYYY-MM-DD date string to RFC 822 format for RSS."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    except (ValueError, TypeError):
        return format_datetime(datetime.now(timezone.utc))


def generate_rss_feed(
    papers: list,
    output_path: Path,
    site_url: str = "",
    title: str = "安全推理论文脉搏",
    description: str = "IACR ePrint 全部新论文与 arXiv 宽召回论文；宽召回后 AI 判不相关的论文提供元数据翻译，相关论文提供中文导读",
    max_items: int = 50,
):
    """Generate an RSS 2.0 feed XML file from papers.

    Args:
        papers: List of paper dicts, assumed already sorted by date descending.
        output_path: Path to write the feed.xml file.
        site_url: Base URL of the site (e.g. "https://user.github.io/paper-pulse").
        title: Feed title.
        description: Feed description.
        max_items: Maximum number of items to include in the feed.
    """
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "link").text = site_url or "https://github.com"
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )

    if site_url:
        feed_url = site_url.rstrip("/") + "/feed.xml"
        ET.SubElement(
            channel,
            "{http://www.w3.org/2005/Atom}link",
            href=feed_url,
            rel="self",
            type="application/rss+xml",
        )

    for paper in papers[:max_items]:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = paper.get("title_zh") or paper.get(
            "title", "Untitled"
        )
        ET.SubElement(item, "link").text = paper.get("url", "")
        ET.SubElement(item, "guid", isPermaLink="true").text = paper.get("url", "")

        # Prefer a Chinese guide/full-text selection or faithful abstract translation.
        desc = (
            paper.get("summary_zh")
            or paper.get("abstract_zh")
            or paper.get("summary")
            or paper.get("abstract", "")
        )
        if paper.get("title_zh") and paper.get("title"):
            desc = f"英文原题：{paper['title']}\n\n{desc}"
        ET.SubElement(item, "description").text = desc

        pub_date = paper.get("published", "")
        if pub_date:
            ET.SubElement(item, "pubDate").text = _date_to_rfc822(pub_date)

        # Source as category
        source = paper.get("source")
        if source:
            ET.SubElement(item, "category").text = source

        # Keywords as categories
        for kw in paper.get("keywords", [])[:5]:
            ET.SubElement(item, "category").text = kw

    # Write XML with declaration
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    with open(output_path, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)

    print(f"Generated RSS feed at {output_path} ({min(len(papers), max_items)} items)")
