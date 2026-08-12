#!/usr/bin/env python3
"""Daily paper digest pipeline, derived from Paper Pulse (GPL-3.0)."""

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11 is used in Actions
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai import AIClient
from fetchers.arxiv import ArxivFetcher
from fetchers.iacr import IACRFetcher
from rss import generate_rss_feed

INTEREST_PROFILE = """重点关注安全推理（最高优先级），以及隐私计算、密码学协议与大模型安全。
典型技术包括 MPC、FSS、DPF、function secret sharing、lookup table、garbled circuit、
homomorphic encryption、secret sharing、TEE，以及保护 Transformer/GPT/LLM 推理的协议。
普通 Transformer/GPT 架构、训练技巧或纯推理加速不应仅凭词面被判为相关。"""


def load_config():
    with (ROOT / "config.toml").open("rb") as handle:
        return tomllib.load(handle)


def load_json(path, default):
    if not path.exists():
        return deepcopy(default)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_since(state, config, now):
    general = config["general"]
    delivered = state.get("last_delivered_at")
    if delivered:
        return parse_utc(delivered) - timedelta(
            hours=general.get("fetch_overlap_hours", 2)
        )
    if state.get("backfill_started_at"):
        return parse_utc(state["backfill_started_at"])
    return now - timedelta(hours=general.get("first_run_hours", 24))


def matched_terms(paper, terms):
    text = _normalize(f"{paper.get('title', '')} {paper.get('abstract', '')}")
    padded = f" {text} "
    return [term for term in terms if f" {_normalize(term)} " in padded]


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def retry_item(paper_id, task, error, attempts=1):
    return {
        "paper_id": paper_id,
        "task": task,
        "attempts": attempts,
        "last_error": _public_error(error),
    }


def _public_error(error):
    # retry.json is committed to a public repository. Keep only a stable class
    # and coarse status/code so URLs, query strings, and tokens are not persisted.
    parts = [type(error).__name__]
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        parts.append(f"HTTP {status}")
    if isinstance(error, ValueError):
        text = str(error).casefold()
        for code in ("invalid_json", "invalid_response", "missing_setting"):
            if code in text:
                parts.append(code)
                break
    return ": ".join(parts)


def process_retries(papers, items, ai):
    by_id = {paper["id"]: paper for paper in papers}
    remaining = []
    changed = False
    for item in items:
        paper = by_id.get(item.get("paper_id"))
        task = item.get("task")
        if not paper or task not in {"abstract", "fulltext"}:
            continue
        try:
            if task == "abstract":
                if paper.get("summary_type") == "fulltext":
                    continue
                paper["summary_zh"] = ai.summarize_abstract(paper)
                paper["summary"] = paper["summary_zh"]
                paper["summary_status"] = "success"
                paper["summary_type"] = "abstract"
            else:
                paper["summary_zh"] = ai.summarize_fulltext(paper)
                paper["summary"] = paper["summary_zh"]
                paper["summary_status"] = "success"
                paper["summary_type"] = "fulltext"
                paper["fulltext_status"] = "success"
            changed = True
            print(f"retry succeeded: {paper['id']} {task}")
        # A paper-level failure must not abort the daily digest. Record any
        # library/API/PDF error and retry the same task on a later run.
        except Exception as exc:  # noqa: BLE001
            remaining.append(
                retry_item(
                    paper["id"],
                    task,
                    exc,
                    int(item.get("attempts", 0)) + 1,
                )
            )
            print(f"retry failed: {paper['id']} {task}: {_public_error(exc)}")
    return remaining, changed


def prepare_new_paper(paper, decision, ai):
    paper = deepcopy(paper)
    paper["summary_en"] = paper.get("abstract", "")
    paper["keywords"] = paper.get("matched_terms", [])
    paper["topics"] = decision.get("topics", []) if decision else []
    paper["relevance_reason_zh"] = (
        decision.get("reason_zh", "")
        if decision
        else "未进入兴趣宽召回，仅保留 IACR 元数据。"
    )
    is_relevant = bool(decision and decision["relevant"])
    is_secure = bool(decision and decision["secure_inference"])
    paper["relevance_level"] = (
        "secure_inference" if is_secure else "related" if is_relevant else "unrelated"
    )
    paper["summary_type"] = "metadata"
    paper["summary_status"] = "not_requested"
    pending = []

    if not is_relevant:
        paper["summary_zh"] = ""
        paper["summary"] = ""
        return paper, pending

    try:
        paper["summary_zh"] = ai.summarize_abstract(paper)
        paper["summary"] = paper["summary_zh"]
        paper["summary_type"] = "abstract"
        paper["summary_status"] = "success"
    except Exception as exc:  # noqa: BLE001 - deliberate paper-level downgrade
        paper["summary_zh"] = (
            "⚠️ 摘要导读生成失败，已加入自动重试队列。可先切换到英文原始摘要阅读。"
        )
        paper["summary"] = paper["summary_zh"]
        paper["summary_type"] = "abstract"
        paper["summary_status"] = "failed"
        pending.append(retry_item(paper["id"], "abstract", exc))
        print(f"abstract summary downgraded: {paper['id']}: {_public_error(exc)}")

    if is_secure:
        try:
            paper["abstract_guide_zh"] = paper["summary_zh"]
            paper["summary_zh"] = ai.summarize_fulltext(paper)
            paper["summary"] = paper["summary_zh"]
            paper["summary_type"] = "fulltext"
            paper["summary_status"] = "success"
            paper["fulltext_status"] = "success"
            pending = [item for item in pending if item["task"] != "abstract"]
        except Exception as exc:  # noqa: BLE001 - deliberate PDF downgrade
            paper["fulltext_status"] = "failed"
            pending.append(retry_item(paper["id"], "fulltext", exc))
            print(f"full-text summary downgraded: {paper['id']}: {_public_error(exc)}")
    return paper, pending


def build_digest(papers, now, site_url):
    china_time = now.astimezone(ZoneInfo("Asia/Shanghai"))
    lines = [
        f"论文每日简报｜{china_time:%Y-%m-%d}",
        "",
        (
            f"本期共 {len(papers)} 篇：IACR "
            f"{sum(p['source'] == 'IACR' for p in papers)} 篇，"
            f"arXiv {sum(p['source'] == 'arXiv' for p in papers)} 篇。"
        ),
        "IACR 收录投递窗口内全部首次发布论文；arXiv 仅收录经 AI 判定相关的论文。",
        "",
    ]
    for index, paper in enumerate(papers, 1):
        level = {
            "secure_inference": "安全推理·全文精选",
            "related": "兴趣相关·摘要导读",
            "unrelated": "IACR 元数据",
        }.get(paper.get("relevance_level"), "元数据")
        authors = ", ".join(paper.get("authors", [])) or "未知"
        lines.extend(
            [
                "=" * 72,
                f"[{index}] {paper['title']}",
                f"来源：{paper['source']}｜首次发布：{paper['published']}｜{level}",
                f"作者：{authors}",
                f"论文：{paper['url']}",
            ]
        )
        if paper.get("pdf_link"):
            lines.append(f"PDF：{paper['pdf_link']}")
        if paper.get("relevance_level") != "unrelated":
            lines.extend(
                [
                    f"相关性：{paper.get('relevance_reason_zh', '')}",
                    "",
                    paper.get("summary_zh", ""),
                ]
            )
            if paper.get("fulltext_status") == "failed":
                lines.append(
                    "\n⚠️ PDF 全文处理失败，本期已降级为摘要导读；系统将在后续运行中重试。"
                )
        lines.append("")
    if site_url:
        lines.extend(["=" * 72, f"网页与 RSS：{site_url}"])
    return "\n".join(lines).strip() + "\n"


def send_digest(body, now):
    server = os.getenv("SMTP_SERVER", "").strip()
    port = int(os.getenv("SMTP_PORT") or "465")
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", "").strip() or username
    recipients = [
        item.strip() for item in os.getenv("EMAIL_TO", "").split(",") if item.strip()
    ]
    missing = [
        name
        for name, value in {
            "SMTP_SERVER": server,
            "SMTP_USERNAME": username,
            "SMTP_PASSWORD": password,
            "EMAIL_TO": recipients,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"missing SMTP settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = (
        f"安全推理论文简报｜{now.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d}"
    )
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(server, port, context=context, timeout=60) as smtp:
            smtp.login(username, password)
            smtp.send_message(message, from_addr=sender, to_addrs=recipients)
    else:
        with smtplib.SMTP(server, port, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(message, from_addr=sender, to_addrs=recipients)


def site_url(config):
    configured = config["general"].get("site_url", "").strip().rstrip("/")
    if configured:
        return configured
    repository = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repository:
        return ""
    owner, name = repository.split("/", 1)
    if name.casefold() == f"{owner}.github.io".casefold():
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{name}"


def prune(papers, now, days):
    cutoff = now - timedelta(days=days)
    kept = []
    for paper in papers:
        value = (
            paper.get("published_at")
            or f"{paper.get('published', '1970-01-01')}T00:00:00Z"
        )
        try:
            if parse_utc(value) >= cutoff:
                kept.append(paper)
        except ValueError:
            kept.append(paper)
    return kept


def write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    _write_atomic(path, content)


def _write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def set_outputs(values):
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())


def run(dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    config = load_config()
    data_dir = ROOT / config["general"].get("data_dir", "data")
    papers_path = data_dir / "papers.json"
    retry_path = data_dir / "retry.json"
    state_path = data_dir / "state.json"
    current = load_json(papers_path, {"papers": []})
    retries = load_json(retry_path, {"items": []})
    state = load_json(state_path, {"last_delivered_at": None})
    if not state.get("last_delivered_at") and not state.get("backfill_started_at"):
        state["backfill_started_at"] = iso_utc(
            now - timedelta(hours=config["general"].get("first_run_hours", 24))
        )
        # This is a fixed recovery anchor, not a delivery cursor. Persist it
        # before network work so repeated first-run failures cannot slide the
        # requested 24-hour window forward and silently lose papers.
        if not dry_run:
            write_json_atomic(state_path, state)
    papers = deepcopy(current.get("papers", []))

    ai_config = dict(config["ai"])
    ai_config["base_url"] = os.getenv("AI_BASE_URL") or ai_config.get("base_url")
    ai_config["model"] = os.getenv("AI_MODEL") or ai_config.get("model")
    ai = AIClient(os.getenv("AI_API_KEY", ""), ai_config)

    retry_items, retries_changed = process_retries(papers, retries.get("items", []), ai)
    since = fetch_since(state, config, now)
    print(f"delivery window starts at {iso_utc(since)}")

    arxiv_cfg = config["fetchers"]["arxiv"]
    iacr_cfg = config["fetchers"]["iacr"]
    arxiv = ArxivFetcher(**arxiv_cfg).fetch_papers(since)
    iacr_fetcher = IACRFetcher(**iacr_cfg)
    iacr = iacr_fetcher.fetch_papers(since)
    known = {paper["id"] for paper in papers}
    fresh_arxiv = [paper for paper in arxiv if paper["id"] not in known]
    fresh_iacr = [paper for paper in iacr if paper["id"] not in known]

    terms = config["interest"]["terms"]
    candidates = []
    for paper in fresh_arxiv + fresh_iacr:
        paper["matched_terms"] = matched_terms(paper, terms)
        if paper["matched_terms"]:
            candidates.append(paper)
    decisions = ai.classify(
        candidates,
        INTEREST_PROFILE,
        int(config["interest"].get("semantic_batch_size", 10)),
    )

    selected = list(fresh_iacr)
    selected.extend(
        paper for paper in fresh_arxiv if decisions.get(paper["id"], {}).get("relevant")
    )
    selected.sort(key=lambda item: item.get("published_at", ""), reverse=True)

    new_papers = []
    for paper in selected:
        prepared, pending = prepare_new_paper(paper, decisions.get(paper["id"]), ai)
        new_papers.append(prepared)
        retry_items.extend(pending)

    # One task per paper/type is enough even if a previous run produced duplicates.
    retry_items = list(
        {(item["paper_id"], item["task"]): item for item in retry_items}.values()
    )
    merged = {paper["id"]: paper for paper in papers}
    merged.update({paper["id"]: paper for paper in new_papers})
    all_papers = prune(
        list(merged.values()), now, int(config["general"].get("retention_days", 30))
    )
    all_papers.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    valid_ids = {paper["id"] for paper in all_papers}
    retry_items = [item for item in retry_items if item["paper_id"] in valid_ids]

    url = site_url(config)
    runtime_dir = ROOT / ".runtime"
    runtime_dir.mkdir(exist_ok=True)
    if new_papers:
        report = build_digest(new_papers, now, url)
        (runtime_dir / "email_report.txt").write_text(report, encoding="utf-8")
        if dry_run:
            print(report)
        else:
            send_digest(report, now)
            # A capped RSS fallback can deliver what it saw, but must not close
            # the backfill window. OAI recovery will fill the gap and ID dedupe
            # prevents already delivered RSS records from appearing again.
            if iacr_fetcher.complete:
                state["last_delivered_at"] = iso_utc(now)
                state.pop("backfill_started_at", None)
            else:
                print("IACR fallback was incomplete; delivery cursor retained")
            print(f"email delivered: {len(new_papers)} papers")
    else:
        print("no new digest items; email skipped")

    if dry_run:
        set_outputs(
            {"has_digest": str(bool(new_papers)).lower(), "new_papers": len(new_papers)}
        )
        return

    # Retention is evaluated every run, even when there is no new email item.
    previous_ids = [paper.get("id") for paper in papers]
    current_ids = [paper.get("id") for paper in all_papers]
    changed = (
        bool(new_papers)
        or retries_changed
        or retry_items != retries.get("items", [])
        or current_ids != previous_ids
    )
    if changed:
        updated_at = iso_utc(now)
        write_json_atomic(
            papers_path,
            {
                "papers": all_papers,
                "last_updated": updated_at,
                "total_count": len(all_papers),
            },
        )
        write_json_atomic(
            retry_path, {"items": retry_items, "last_updated": updated_at}
        )
        write_json_atomic(state_path, state)
        generate_rss_feed(
            all_papers,
            ROOT / "feed.xml",
            site_url=url,
            max_items=int(config["rss"].get("max_items", 100)),
        )

    _write_atomic(
        ROOT / "config.js",
        f"const CONFIG = {{ papersPerPage: {int(config['frontend'].get('papers_per_page', 12))} }};\n",
    )
    set_outputs(
        {
            "has_digest": str(bool(new_papers)).lower(),
            "new_papers": len(new_papers),
            "total_papers": len(all_papers),
            "retry_tasks": len(retry_items),
            **ai.usage,
        }
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and generate a report without sending email or changing committed state",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
