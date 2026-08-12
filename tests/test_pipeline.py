import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import main
from ai import AIClient, _chunks
from fetchers.arxiv import ArxivFetcher
from fetchers.iacr import IACRFetcher


class PipelineTests(unittest.TestCase):
    def test_arxiv_fetcher_has_no_silent_result_cap(self):
        fetcher = ArxivFetcher(["cs.CR"], delay=0, batch_size=100, timeout=10)
        self.assertFalse(hasattr(fetcher, "max_results"))

    def test_first_run_and_resume_window(self):
        config = {"general": {"first_run_hours": 24, "fetch_overlap_hours": 2}}
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        self.assertEqual(main.fetch_since({}, config, now).hour, 0)
        state = {"last_delivered_at": "2026-08-11T12:00:00Z"}
        self.assertEqual(
            main.fetch_since(state, config, now),
            datetime(2026, 8, 11, 10, tzinfo=timezone.utc),
        )
        anchored = {
            "last_delivered_at": None,
            "backfill_started_at": "2026-08-01T03:00:00Z",
        }
        self.assertEqual(
            main.fetch_since(anchored, config, now),
            datetime(2026, 8, 1, 3, tzinfo=timezone.utc),
        )

    def test_interest_matching_normalizes_hyphens(self):
        paper = {"title": "Privacy-Preserving Inference", "abstract": "Uses DPFs."}
        matched = main.matched_terms(
            paper, ["privacy-preserving inference", "DPF", "transformer"]
        )
        self.assertEqual(matched, ["privacy-preserving inference"])

    def test_digest_keeps_english_title_and_chinese_summary(self):
        paper = {
            "title": "Secure Transformer Inference",
            "source": "IACR",
            "published": "2026-08-12",
            "authors": ["Alice"],
            "url": "https://example.test/paper",
            "pdf_link": "https://example.test/paper.pdf",
            "relevance_level": "secure_inference",
            "relevance_reason_zh": "核心问题是安全推理。",
            "summary_zh": "这是中文全文总结。",
        }
        digest = main.build_digest(
            [paper], datetime(2026, 8, 12, tzinfo=timezone.utc), "https://site.test"
        )
        self.assertIn("Secure Transformer Inference", digest)
        self.assertIn("这是中文全文总结", digest)
        self.assertIn("安全推理·全文精选", digest)

    def test_digest_explains_arxiv_selection_funnel(self):
        digest = main.build_digest(
            [],
            datetime(2026, 8, 12, tzinfo=timezone.utc),
            "",
            {
                "iacr_new": 0,
                "arxiv_new": 57,
                "arxiv_recalled": 5,
                "arxiv_related": 2,
                "arxiv_metadata": 3,
            },
        )
        self.assertIn(
            "arXiv 新论文 57 篇 → 关键词宽召回并收录 5 篇，"
            "其中 AI 判定相关并生成导读 2 篇，其余 3 篇仅列元数据",
            digest,
        )

    def test_arxiv_wide_recall_metadata_is_delivered_without_summary(self):
        def arxiv_paper(paper_id, title, abstract, author):
            return {
                "id": paper_id,
                "arxiv_id": paper_id.removeprefix("arxiv_"),
                "title": title,
                "authors": [author],
                "abstract": abstract,
                "published": "2026-08-12",
                "published_at": "2026-08-12T00:30:00Z",
                "source": "arXiv",
                "url": f"https://arxiv.org/abs/{paper_id.removeprefix('arxiv_')}",
                "pdf_link": f"https://arxiv.org/pdf/{paper_id.removeprefix('arxiv_')}",
                "categories": ["cs.AI"],
            }

        broad = arxiv_paper(
            "arxiv_2608.00001",
            "A Transformer for Crop Detection",
            "We compare transformer detectors on crop images.",
            "Broad Author",
        )
        related = arxiv_paper(
            "arxiv_2608.00002",
            "MPC Inference for Neural Networks",
            "We protect private inference with multi-party computation.",
            "Related Author",
        )
        unmatched = arxiv_paper(
            "arxiv_2608.00003",
            "A Study of Euclidean Geometry",
            "We prove a result about triangles.",
            "Unmatched Author",
        )

        classified = []
        summarized = []

        class FakeAI:
            def __init__(self, *args, **kwargs):
                self.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

            def classify(self, papers, *args, **kwargs):
                classified.extend(paper["id"] for paper in papers)
                return {
                    paper["id"]: {
                        "relevant": paper["id"] == related["id"],
                        "secure_inference": False,
                        "reason_zh": "相关"
                        if paper["id"] == related["id"]
                        else "仅宽召回",
                        "topics": [],
                    }
                    for paper in papers
                }

            def summarize_abstract(self, paper):
                summarized.append(paper["id"])
                return f"导读：{paper['id']}"

        class ArxivFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def fetch_papers(self, since):
                return [broad, related, unmatched]

        class EmptyIacrFetcher:
            complete = True

            def __init__(self, *args, **kwargs):
                pass

            def fetch_papers(self, since):
                return []

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "config.toml").write_text(
                (ROOT / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
            )
            for name, value in {
                "papers.json": {"papers": []},
                "retry.json": {"items": []},
                "state.json": {"last_delivered_at": "2026-08-11T00:00:00Z"},
            }.items():
                (root / "data" / name).write_text(json.dumps(value), encoding="utf-8")

            with (
                patch.object(main, "ROOT", root),
                patch.object(main, "AIClient", FakeAI),
                patch.object(main, "ArxivFetcher", ArxivFetcher),
                patch.object(main, "IACRFetcher", EmptyIacrFetcher),
                patch.object(main, "send_digest") as send,
            ):
                main.run(now=datetime(2026, 8, 12, 1, tzinfo=timezone.utc))

            self.assertEqual(set(classified), {broad["id"], related["id"]})
            self.assertEqual(summarized, [related["id"]])
            send.assert_called_once()
            digest = send.call_args.args[0]
            self.assertIn("arXiv 宽召回·仅元数据", digest)
            self.assertIn(broad["title"], digest)
            self.assertIn(broad["authors"][0], digest)
            self.assertIn(broad["abstract"], digest)
            self.assertIn(f"导读：{related['id']}", digest)
            self.assertNotIn(unmatched["title"], digest)
            self.assertIn(
                "arXiv 新论文 3 篇 → 关键词宽召回并收录 2 篇，"
                "其中 AI 判定相关并生成导读 1 篇，其余 1 篇仅列元数据",
                digest,
            )

            stored = json.loads(
                (root / "data" / "papers.json").read_text(encoding="utf-8")
            )["papers"]
            self.assertEqual(
                {paper["id"] for paper in stored}, {broad["id"], related["id"]}
            )
            broad_stored = next(paper for paper in stored if paper["id"] == broad["id"])
            self.assertEqual(broad_stored["summary_type"], "metadata")
            self.assertEqual(broad_stored["summary_status"], "not_requested")
            self.assertEqual(broad_stored["summary_zh"], "")
            self.assertEqual(broad_stored["summary_en"], broad["abstract"])

    def test_summary_prompts_require_detailed_evidence_bound_method(self):
        client = AIClient(
            "test", {"base_url": "https://example.test/v1", "model": "test"}
        )
        paper = {
            "title": "Secure Inference",
            "source": "arXiv",
            "abstract": "An abstract.",
        }
        with patch.object(client, "_text_chat", return_value="summary") as chat:
            client.summarize_abstract(paper)
        prompt = chat.call_args.args[1]
        self.assertIn("方法与技术路线", prompt)
        self.assertIn("关键步骤或模块", prompt)
        self.assertIn("摘要未说明", prompt)
        self.assertIn("不要为凑长度补造细节", prompt)

    def test_no_new_items_does_not_send_or_advance_cursor(self):
        class FakeAI:
            def __init__(self, *args, **kwargs):
                self.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

            def classify(self, *args, **kwargs):
                return {}

        class FakeFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def fetch_papers(self, since):
                return []

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "config.toml").write_text(
                (ROOT / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
            )
            for name, value in {
                "papers.json": {"papers": []},
                "retry.json": {"items": []},
                "state.json": {"last_delivered_at": None},
            }.items():
                (root / "data" / name).write_text(json.dumps(value), encoding="utf-8")
            with (
                patch.object(main, "ROOT", root),
                patch.object(main, "AIClient", FakeAI),
                patch.object(main, "ArxivFetcher", FakeFetcher),
                patch.object(main, "IACRFetcher", FakeFetcher),
                patch.object(main, "send_digest") as send,
            ):
                main.run(now=datetime(2026, 8, 12, tzinfo=timezone.utc))
            send.assert_not_called()
            state = json.loads((root / "data" / "state.json").read_text())
            self.assertIsNone(state["last_delivered_at"])

    def test_long_text_is_chunked_without_loss(self):
        text = "a" * 25
        self.assertEqual("".join(_chunks(text, 10)), text)

    def test_iacr_fallback_requires_exact_arxiv_title(self):
        atom = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2608.12345v2</id>
            <title>Secure   Transformer: Inference!</title>
          </entry>
        </feed>"""

        class Response:
            content = atom

            def raise_for_status(self):
                return None

        client = AIClient(
            "test", {"base_url": "https://example.test/v1", "model": "test"}
        )
        with patch.object(client.session, "get", return_value=Response()):
            self.assertEqual(
                client._find_exact_arxiv_pdf("Secure Transformer — Inference"),
                "https://arxiv.org/pdf/2608.12345",
            )
            self.assertIsNone(client._find_exact_arxiv_pdf("Different Paper"))

    def test_iacr_oai_uses_earliest_dc_date_and_skips_revisions(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <record xmlns="http://www.openarchives.org/OAI/2.0/"
                xmlns:dc="http://purl.org/dc/elements/1.1/">
          <header>
            <identifier>oai:eprint.iacr.org:2026/123</identifier>
            <datestamp>2026-08-12T09:00:00Z</datestamp>
          </header>
          <metadata><dc:dc>
            <dc:title>A revised paper</dc:title>
            <dc:creator>Alice</dc:creator>
            <dc:description>Abstract</dc:description>
            <dc:date>2026-08-10T08:00:00Z</dc:date>
            <dc:date>2026-08-12T09:00:00Z</dc:date>
          </dc:dc></metadata>
        </record>"""
        import xml.etree.ElementTree as ET

        fetcher = IACRFetcher(delay=0)
        record = ET.fromstring(xml)
        self.assertIsNone(
            fetcher._parse_oai_record(
                record, datetime(2026, 8, 11, tzinfo=timezone.utc)
            )
        )
        paper = fetcher._parse_oai_record(
            record, datetime(2026, 8, 9, tzinfo=timezone.utc)
        )
        self.assertEqual(paper["published_at"], "2026-08-10T08:00:00Z")
        self.assertEqual(paper["updated_at"], "2026-08-12T09:00:00Z")

    def test_public_retry_error_does_not_expose_message(self):
        error = RuntimeError("https://secret.example/v1?api_key=do-not-publish")
        self.assertEqual(main._public_error(error), "RuntimeError")

    def test_successful_digest_advances_cursor_and_writes_site_data(self):
        paper = {
            "id": "iacr_2026/1",
            "iacr_id": "2026/1",
            "title": "Metadata-only paper",
            "authors": ["Alice"],
            "abstract": "No matching terms.",
            "published": "2026-08-12",
            "published_at": "2026-08-12T00:30:00Z",
            "source": "IACR",
            "url": "https://eprint.iacr.org/2026/1",
            "pdf_link": "https://eprint.iacr.org/2026/1.pdf",
            "categories": ["Cryptography"],
        }

        class FakeAI:
            def __init__(self, *args, **kwargs):
                self.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

            def classify(self, *args, **kwargs):
                return {}

        class EmptyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def fetch_papers(self, since):
                return []

        class IacrFetcher(EmptyFetcher):
            complete = True

            def fetch_papers(self, since):
                return [paper]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "config.toml").write_text(
                (ROOT / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
            )
            for name, value in {
                "papers.json": {"papers": []},
                "retry.json": {"items": []},
                "state.json": {"last_delivered_at": None},
            }.items():
                (root / "data" / name).write_text(json.dumps(value), encoding="utf-8")
            with (
                patch.object(main, "ROOT", root),
                patch.object(main, "AIClient", FakeAI),
                patch.object(main, "ArxivFetcher", EmptyFetcher),
                patch.object(main, "IACRFetcher", IacrFetcher),
                patch.object(main, "send_digest") as send,
            ):
                main.run(now=datetime(2026, 8, 12, 1, tzinfo=timezone.utc))

            send.assert_called_once()
            state = json.loads((root / "data" / "state.json").read_text())
            self.assertEqual(state, {"last_delivered_at": "2026-08-12T01:00:00Z"})
            stored = json.loads(
                (root / "data" / "papers.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored["papers"][0]["id"], paper["id"])
            self.assertTrue((root / "feed.xml").exists())

    def test_incomplete_iacr_fallback_retains_delivery_cursor(self):
        now = datetime(2026, 8, 12, 1, tzinfo=timezone.utc)
        state = {"last_delivered_at": "2026-08-11T01:00:00Z"}

        class FakeAI:
            def __init__(self, *args, **kwargs):
                self.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }

            def classify(self, *args, **kwargs):
                return {}

        class EmptyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def fetch_papers(self, since):
                return []

        paper = {
            "id": "iacr_2026/2",
            "iacr_id": "2026/2",
            "title": "Fallback metadata",
            "authors": [],
            "abstract": "",
            "published": "2026-08-12",
            "published_at": "2026-08-12T00:00:00Z",
            "source": "IACR",
            "url": "https://eprint.iacr.org/2026/2",
            "pdf_link": "https://eprint.iacr.org/2026/2.pdf",
            "categories": [],
        }

        class IncompleteIacr(EmptyFetcher):
            complete = False

            def fetch_papers(self, since):
                return [paper]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "config.toml").write_text(
                (ROOT / "config.toml").read_text(encoding="utf-8"), encoding="utf-8"
            )
            for name, value in {
                "papers.json": {"papers": []},
                "retry.json": {"items": []},
                "state.json": state,
            }.items():
                (root / "data" / name).write_text(json.dumps(value), encoding="utf-8")
            with (
                patch.object(main, "ROOT", root),
                patch.object(main, "AIClient", FakeAI),
                patch.object(main, "ArxivFetcher", EmptyFetcher),
                patch.object(main, "IACRFetcher", IncompleteIacr),
                patch.object(main, "send_digest"),
            ):
                main.run(now=now)

            saved = json.loads((root / "data" / "state.json").read_text())
            self.assertEqual(saved["last_delivered_at"], state["last_delivered_at"])


if __name__ == "__main__":
    unittest.main()
