#!/usr/bin/env python3
"""
Network QoS Scraper for RAG Pipeline — Crawl4AI Edition + SE Answers
- Stack Exchange API: Fetches questions + TOP ANSWERS (high-signal technical content)
- Documentation sites: Crawl4AI for clean markdown extraction + JS support
- Score-based filtering: Keep technically valuable content, ignore fluff
- Code+context extraction: RAG-ready snippets with surrounding explanation
- Ethical by design: robots.txt, rate limits, transparent UA, caching
"""

import asyncio
import requests
import json
import time
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set, Tuple
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# Crawl4AI imports (optional fallback if not installed)
try:
    from crawl4ai import WebCrawler, CrawlerRunConfig
    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False
    print("⚠️  crawl4ai not installed. Install with: pip install crawl4ai")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# CONFIGURATION: Technical scoring & extraction settings
# ──────────────────────────────────────────────────────────
class Config:
    TECHNICAL_SCORE_THRESHOLD = 0.35
    MIN_WORD_COUNT = 100
    MAX_ANSWERS_PER_QUESTION = 3
    
    TECHNICAL_KEYWORDS = {
        "sudo": 0.1, "apt-get": 0.1, "pip install": 0.1, "git clone": 0.1,
        "mininet>": 0.25, "ovs-vsctl": 0.25, "ryu-manager": 0.25, "mn --test": 0.2,
        "def ": 0.15, "class ": 0.15, "import ryu": 0.25, "from mininet": 0.25,
        "async def": 0.15, "@controller": 0.2,
        "openflow": 0.3, "flow_mod": 0.35, "packet_in": 0.3, "ofpt_": 0.25,
        "flow_entry": 0.25, "flow_table": 0.25, "match_fields": 0.2,
        "qos": 0.25, "policing": 0.25, "shaping": 0.25, "dscp": 0.2, "tos": 0.15,
        "bandwidth": 0.15, "latency": 0.15, "jitter": 0.15, "throughput": 0.15,
        "sla": 0.2, "qoe": 0.15,
        "controller = ": 0.2, "[app]": 0.15, "ofp_header": 0.25,
        "OFPFC_ADD": 0.3, "OFPIT_OUTPUT": 0.3,
    }
    
    NEGATIVE_PHRASES = {
        "this website uses cookies": -0.2,
        "privacy & cookies policy": -0.2,
        "accept read more": -0.15,
        "we'll assume you're ok": -0.15,
        "subscribe to our newsletter": -0.25,
        "advertise with us": -0.3,
        "premium content": -0.2,
    }
    
    CODE_CONTEXT_LINES = 3
    MAX_CRAWL_DEPTH = 2
    REQUEST_DELAY = 1.5
    TIMEOUT_SECONDS = 30


class NetworkDocScraper:
    def __init__(
        self,
        output_file: str = "network_docs_raw.json",
        se_api_key: Optional[str] = None,
        contact_url: str = "https://github.com/Predigo-DS",
        use_crawl4ai: bool = True,
        technical_threshold: float = None
    ):
        self.output_file = output_file
        self.se_api_key = se_api_key
        self.use_crawl4ai = use_crawl4ai and CRAWL4AI_AVAILABLE
        self.technical_threshold = technical_threshold or Config.TECHNICAL_SCORE_THRESHOLD
        
        self.documents: List[Dict] = []
        self._seen_se_ids: Set[int] = set()
        self._robots_cache: Dict[str, Tuple[RobotFileParser, float]] = {}
        self._robots_cache_ttl = 86400
        
        self.session = self._setup_session(contact_url)
        self._crawler: Optional[WebCrawler] = None
        
        os.makedirs(".cache", exist_ok=True)
        
        if not self.use_crawl4ai:
            logger.info("📦 Using fallback scraper (trafilatura + BeautifulSoup)")
        else:
            logger.info("🚀 Using Crawl4AI for documentation extraction")

    def _setup_session(self, contact_url: str) -> requests.Session:
        session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"]
        )
        session.mount("https://", HTTPAdapter(max_retries=retries))
        session.mount("http://", HTTPAdapter(max_retries=retries))
        session.headers.update({
            "User-Agent": f"QoSentry-Scraper/1.0 (+{contact_url})",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive"
        })
        return session

    async def _init_crawler(self):
        if self._crawler is None and self.use_crawl4ai:
            self._crawler = WebCrawler()
            await self._crawler.awarmup()
            logger.debug("✅ Crawl4AI crawler initialized")

    def _check_robots(self, url: str) -> bool:
        domain = urlparse(url).netloc
        now = time.time()
        user_agent = self.session.headers["User-Agent"]

        if domain in self._robots_cache:
            rp, cached_at = self._robots_cache[domain]
            if now - cached_at < self._robots_cache_ttl:
                return rp.can_fetch(user_agent, url)

        robots_url = f"{urlparse(url).scheme}://{domain}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            resp = self.session.get(robots_url, timeout=10)
            if resp.status_code >= 400:
                logger.debug(f"No robots.txt at {domain} (HTTP {resp.status_code})")
                return True
            rp.parse(resp.text.splitlines())
            self._robots_cache[domain] = (rp, now)
            return rp.can_fetch(user_agent, url)
        except Exception as e:
            logger.warning(f"Could not fetch robots.txt for {domain}: {e}")
            return True

    def calculate_technical_score(self, doc: Dict) -> float:
        text = doc["text"].lower()
        score = 0.0
        
        for phrase, penalty in Config.NEGATIVE_PHRASES.items():
            if phrase in text:
                score += penalty
        
        for keyword, weight in Config.TECHNICAL_KEYWORDS.items():
            if keyword.lower() in text:
                score += weight
        
        if "```" in doc["text"] or "<code>" in doc["text"].lower() or "<pre>" in doc["text"].lower():
            score += 0.25
        
        if doc["metadata"].get("source_type") == "stackexchange_qa":
            score += 0.35
        
        if re.search(r'(mininet>|ovs-vsctl|ryu-manager|\w+\s*[:=]\s*\w+)', text):
            score += 0.15
        
        return max(0.0, min(1.0, score))

    def extract_code_with_context(self, text: str) -> List[Dict]:
        results = []
        lines = text.split('\n')
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
                
            is_code_start = (
                re.match(r'^\s*(def|class|async\s+def)\s+\w+', line) or
                re.match(r'^(mininet>|sudo|ovs-vsctl|ryu-manager)\s+', line) or
                re.match(r'^[\w\.\-]+\s*[:=]\s*[\w\.\-\'\"]+', line) or
                ('openflow' in stripped.lower() and any(k in stripped for k in ['flow', 'packet', 'match', 'action']))
            )
            
            if is_code_start:
                start = max(0, i - Config.CODE_CONTEXT_LINES)
                context_before = '\n'.join(lines[start:i]).strip()
                
                code_lines = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    if (next_line.strip() == '' or 
                        next_line.startswith(' ') or next_line.startswith('\t') or
                        re.match(r'^[\s]*(def|class|[\w\.\-]+\s*[:=])', next_line) or
                        (j - i < 10 and any(k in next_line.lower() for k in Config.TECHNICAL_KEYWORDS))):
                        if next_line.strip():
                            code_lines.append(next_line)
                        j += 1
                    else:
                        break
                
                end = min(len(lines), j + Config.CODE_CONTEXT_LINES)
                context_after = '\n'.join(lines[j:end]).strip()
                
                full_context = f"{context_before}\n\n{' '.join(code_lines)}\n\n{context_after}".strip()
                
                if len(full_context) > 50:
                    results.append({
                        "code": '\n'.join(code_lines).strip(),
                        "context_before": context_before,
                        "context_after": context_after,
                        "full_context": full_context,
                        "line_start": i,
                        "line_end": j
                    })
        
        return results

    def _clean_content_fallback(self, raw_html: str, url: str) -> str:
        import io, contextlib
        import trafilatura
        
        html_lower = raw_html.strip().lower()
        if not (html_lower.startswith("<!doctype") or html_lower.startswith("<html")):
            raw_html = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{raw_html}</body></html>"
        
        with contextlib.redirect_stderr(io.StringIO()):
            cleaned = trafilatura.extract(
                raw_html, include_comments=False, include_tables=True, favor_precision=True
            )
        
        if cleaned and len(cleaned.strip()) > Config.MIN_WORD_COUNT:
            return cleaned.strip()
        
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "noscript"]):
            tag.decompose()
        
        if "readthedocs.io" in url:
            main = soup.select_one("div[role='main']") or soup.select_one("article")
            if main:
                return main.get_text(separator="\n", strip=True)
        
        for selector in ["article", ".content", "#content", "main", ".post-body"]:
            elem = soup.select_one(selector)
            if elem:
                return elem.get_text(separator="\n", strip=True)
        
        return soup.get_text(separator="\n", strip=True)

    def _fetch_se_answers(self, question_id: int) -> List[Dict]:
        """Fetch top answers for a Stack Exchange question with caching."""
        cache_file = f".cache/se_answers_{question_id}.json"
        
        # Try cache first (valid for 24h)
        if os.path.exists(cache_file):
            try:
                with open(cache_file) as f:
                    cached = json.load(f)
                    if time.time() - cached.get("cached_at", 0) < 86400:
                        return cached["answers"]
            except:
                pass
        
        api_url = f"https://api.stackexchange.com/2.3/questions/{question_id}/answers"
        params = {
            "site": "networkengineering",
            "filter": "withbody",
            "sort": "votes",
            "order": "desc",
            "pagesize": Config.MAX_ANSWERS_PER_QUESTION
        }
        if self.se_api_key:
            params["key"] = self.se_api_key
        
        try:
            resp = self.session.get(api_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            answers = []
            for ans in data.get("items", []):
                body = self._clean_content_fallback(ans.get("body", ""), "")
                if len(body.strip()) > 50:
                    answers.append({
                        "score": ans.get("score", 0),
                        "is_accepted": ans.get("is_accepted", False),
                        "body": body,
                        "answer_id": ans.get("answer_id")
                    })
            
            # Save to cache
            with open(cache_file, "w") as f:
                json.dump({"cached_at": time.time(), "answers": answers}, f)
            
            return answers
        except Exception as e:
            logger.debug(f"  Failed to fetch answers for Q{question_id}: {e}")
            return []

    async def _scrape_with_crawl4ai(self, url: str) -> Optional[Dict]:
        await self._init_crawler()
        
        try:
            config = CrawlerRunConfig(
                wait_for="body",
                exclude_external_links=True,
                remove_overlay_elements=True,
                process_iframes=False,
                extraction_strategy=JsonCssExtractionStrategy(
                    schema={
                        "name": "technical_doc",
                        "baseSelector": "article, .content, main, [role='main'], .document",
                        "fields": [
                            {"name": "title", "selector": "h1", "type": "text"},
                            {"name": "content", "selector": "p, pre, code, .highlight, .document", "type": "text", "multiple": True}
                        ]
                    }
                ) if CRAWL4AI_AVAILABLE else None
            )
            
            result = await self._crawler.arun(url=url, config=config)
            
            if not result.success:
                logger.warning(f"❌ Crawl4AI failed for {url}: {result.error_message}")
                return None
            
            title = result.metadata.get("title", "") or url.split("/")[-1]
            content = result.markdown or result.cleaned_html or ""
            
            if len(content.strip()) < Config.MIN_WORD_COUNT:
                logger.debug(f"⏭️ Low-content from Crawl4AI: {url}")
                return None
            
            return {
                "text": content,
                "title": title,
                "url": url,
                "metadata": {
                    "word_count": len(content.split()),
                    "char_count": len(content),
                    "crawl4ai_success": True
                }
            }
            
        except Exception as e:
            logger.warning(f"⚠️ Crawl4AI exception for {url}: {e}")
            return None

    def _scrape_with_fallback(self, url: str) -> Optional[Dict]:
        try:
            resp = self.session.get(url, timeout=Config.TIMEOUT_SECONDS)
            resp.raise_for_status()
            
            soup = BeautifulSoup(resp.text, "html.parser")
            title = (soup.title.string.strip() if soup.title and soup.title.string 
                    else url.rstrip("/").split("/")[-1] or urlparse(url).netloc)
            
            cleaned = self._clean_content_fallback(resp.text, url)
            
            if len(cleaned.strip()) < Config.MIN_WORD_COUNT:
                logger.debug(f"⏭️ Low-content fallback: {url}")
                return None
            
            return {
                "text": cleaned,
                "title": title,
                "url": url,
                "metadata": {
                    "word_count": len(cleaned.split()),
                    "char_count": len(cleaned),
                    "status_code": resp.status_code
                }
            }
        except Exception as e:
            logger.warning(f"⚠️ Fallback scrape failed for {url}: {e}")
            return None

    async def scrape_url_async(self, url: str, source_type: str = "documentation") -> Optional[Dict]:
        if not self._check_robots(url):
            logger.info(f"⏭️ Skipped {url} (disallowed by robots.txt)")
            return None
        
        logger.info(f"📄 Fetching: {url}")
        
        if self.use_crawl4ai:
            result = await self._scrape_with_crawl4ai(url)
            if result:
                return result
        
        result = self._scrape_with_fallback(url)
        if result:
            result["metadata"]["used_fallback"] = True
        
        return result

    def _build_metadata(
        self,
        url: str,
        title: str,
        content: str,
        source_type: str,
        tags: List[str] = None,
        extra: Dict = None
    ) -> Dict:
        meta = {
            "source": urlparse(url).netloc,
            "url": url,
            "title": title,
            "source_type": source_type,
            "tags": tags or [],
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "word_count": len(content.split()),
            "char_count": len(content),
            "content_hash": hashlib.sha256(content.encode()).hexdigest()[:16],
        }
        if extra:
            meta.update(extra)
        return meta

    def _normalize_doc(self, doc: Dict, source_type: str = None) -> Dict:
        if "url" in doc and "metadata" in doc and "url" not in doc["metadata"]:
            doc["metadata"]["url"] = doc.pop("url")
        if "title" in doc and "metadata" in doc and "title" not in doc["metadata"]:
            doc["metadata"]["title"] = doc.pop("title")
        
        meta = doc.setdefault("metadata", {})
        meta.setdefault("source", urlparse(meta.get("url", "")).netloc)
        meta.setdefault("source_type", source_type or meta.get("source_type", "unknown"))
        meta.setdefault("tags", [])
        meta.setdefault("scraped_at", datetime.now(timezone.utc).isoformat())
        
        if "content_hash" not in meta and doc.get("text"):
            meta["content_hash"] = hashlib.sha256(doc["text"].encode()).hexdigest()[:16]
        
        if "text" not in doc and "text_enriched" in doc:
            doc["text"] = doc["text_enriched"]
        
        return doc

    def fetch_stackexchange(
        self,
        tags: List[str],
        min_votes: int = 0,
        max_pages: int = 5
    ) -> List[Dict]:
        """Fetch Q&A from Network Engineering Stack Exchange WITH TOP ANSWERS."""
        api_url = "https://api.stackexchange.com/2.3/questions"
        collected: List[Dict] = []

        for single_tag in tags:
            logger.info(f"🔍 SE tag: [{single_tag}]")

            for page in range(1, max_pages + 1):
                params = {
                    "site": "networkengineering",
                    "tagged": single_tag,
                    "sort": "votes",
                    "order": "desc",
                    "page": page,
                    "pagesize": 30,
                    "filter": "withbody"
                }
                if self.se_api_key:
                    params["key"] = self.se_api_key

                try:
                    resp = self.session.get(api_url, params=params, timeout=15)
                    resp.raise_for_status()
                    data = resp.json()

                    items = data.get("items", [])
                    logger.debug(f"  Page {page}: {len(items)} questions for [{single_tag}]")

                    for item in items:
                        qid = item.get("question_id")
                        if qid in self._seen_se_ids:
                            continue
                        self._seen_se_ids.add(qid)

                        if item.get("score", 0) < min_votes:
                            continue

                        title = item.get("title", "")
                        raw_html = item.get("body", "")
                        question_cleaned = self._clean_content_fallback(raw_html, item.get("link", ""))

                        # ✅ FETCH TOP ANSWERS
                        answers = self._fetch_se_answers(qid)
                        
                        # ✅ COMBINE QUESTION + ANSWERS
                        if answers:
                            answer_texts = []
                            for i, ans in enumerate(answers, 1):
                                prefix = "✓ " if ans["is_accepted"] else f"#{i} "
                                answer_texts.append(f"{prefix}[Score:{ans['score']}] {ans['body']}")
                            full_content = f"Q: {question_cleaned}\n\n### Top Answers:\n" + "\n\n---\n\n".join(answer_texts)
                        else:
                            full_content = question_cleaned

                        if len(full_content.strip()) < 50:
                            continue

                        meta = self._build_metadata(
                            url=item.get("link", ""),
                            title=title,
                            content=full_content,
                            source_type="stackexchange_qa",
                            tags=item.get("tags", []),
                            extra={
                                "se_score": item.get("score", 0),
                                "se_is_answered": item.get("is_answered", False),
                                "se_creation_date": item.get("creation_date", 0),
                                "se_view_count": item.get("view_count", 0),
                                "se_question_id": qid,
                                "se_answer_count": len(answers)
                            }
                        )
                        collected.append({"metadata": meta, "text": full_content})

                    time.sleep(1.0 if self.se_api_key else 2.0)
                    if not data.get("has_more"):
                        break

                except Exception as e:
                    logger.error(f"  API error for [{single_tag}] page {page}: {e}")
                    break

        logger.info(f"✅ SE: Collected {len(collected)} unique Q&A entries")
        return collected

    async def scrape_documentation_async(
        self,
        urls: List[Tuple[str, str]],
        max_depth: int = None
    ) -> List[Dict]:
        results = []
        max_depth = max_depth or Config.MAX_CRAWL_DEPTH
        visited: Set[str] = set()
        
        async def _crawl(url: str, source_type: str, depth: int):
            if url in visited or depth > max_depth:
                return
            visited.add(url)
            
            doc = await self.scrape_url_async(url, source_type)
            if doc:
                results.append(doc)
            
            if depth < max_depth and source_type in ["documentation", "api_reference"]:
                try:
                    resp = self.session.get(url, timeout=15)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    domain = urlparse(url).netloc
                    
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        full_url = urljoin(url, href)
                        
                        if (urlparse(full_url).netloc == domain and 
                            full_url not in visited and
                            any(kw in full_url.lower() for kw in ["api", "guide", "tutorial", "reference", "config", "example"])):
                            
                            time.sleep(Config.REQUEST_DELAY)
                            await _crawl(full_url, source_type, depth + 1)
                            
                except Exception as e:
                    logger.debug(f"  Link extraction failed for {url}: {e}")
        
        for url, source_type in urls:
            time.sleep(Config.REQUEST_DELAY)
            await _crawl(url, source_type, depth=0)
        
        return results

    async def run_async(
        self,
        se_tags: List[str] = None,
        doc_urls: List[Tuple[str, str]] = None,
        min_votes: int = 0
    ) -> List[Dict]:
        if se_tags is None:
            se_tags = ["sdn", "openflow", "qos", "mininet", "bandwidth", "software-defined-network"]
        
        if doc_urls is None:
            doc_urls = [
                ("https://ryu.readthedocs.io/en/latest/", "documentation"),
                ("http://mininet.org/", "documentation"),
                ("http://mininet.org/walkthrough/", "tutorial"),
                ("http://mininet.org/api/annotated.html", "api_reference"),
                ("https://docs.opendaylight.org/en/latest/", "documentation"),
                ("https://opennetworking.org/sdn-resources/", "resource_hub"),
                ("https://www.geeksforgeeks.org/mininet-emulator-in-software-defined-networks/", "tutorial"),
            ]

        logger.info("🌐 Fetching StackExchange Q&A...")
        se_docs = self.fetch_stackexchange(se_tags, min_votes=min_votes, max_pages=5)
        for se_doc in se_docs:
            normalized = self._normalize_doc(se_doc, source_type="stackexchange_qa")
            self.documents.append(normalized)

        logger.info("📄 Scraping documentation with Crawl4AI...")
        doc_docs = await self.scrape_documentation_async(doc_urls)
        for doc in doc_docs:
            normalized = self._normalize_doc(doc, source_type=doc.get("metadata", {}).get("source_type", "documentation"))
            self.documents.append(normalized)

        logger.info("🎯 Filtering & enriching documents...")
        enriched = []
        for doc in self.documents:
            score = self.calculate_technical_score(doc)
            doc["metadata"]["technical_score"] = round(score, 3)
            
            if score < self.technical_threshold:
                url = doc["metadata"].get("url", "unknown")[:60]
                logger.debug(f"⏭️ Filtered (score {score:.2f}): {url}")
                continue
            
            code_snippets = self.extract_code_with_context(doc["text"])
            if code_snippets:
                doc["code_snippets"] = code_snippets
                doc["metadata"]["has_structured_code"] = True
                contexts = [s["full_context"] for s in code_snippets[:5]]
                doc["text_enriched"] = doc["text"] + "\n\n" + "\n\n---\n\n".join(contexts)
            else:
                doc["text_enriched"] = doc["text"]
            
            enriched.append(doc)
        
        logger.info("🔍 Deduplicating...")
        seen_hashes = set()
        unique_docs = []
        for doc in enriched:
            h = doc["metadata"].get("content_hash")
            if not h and doc.get("text"):
                h = hashlib.sha256(doc["text"].encode()).hexdigest()[:16]
            if h and h not in seen_hashes:
                seen_hashes.add(h)
                unique_docs.append(doc)
        
        out_dir = os.path.dirname(self.output_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(unique_docs, f, indent=2, ensure_ascii=False)
        
        logger.info(f"💾 Saved {len(unique_docs)} documents to {self.output_file}")
        
        sources = {}
        for d in unique_docs:
            src = d["metadata"]["source"]
            sources[src] = sources.get(src, 0) + 1
        logger.info(f"📊 Sources: {sources}")
        
        avg_score = sum(d["metadata"]["technical_score"] for d in unique_docs) / len(unique_docs) if unique_docs else 0
        logger.info(f"📈 Avg technical score: {avg_score:.2f}")
        
        return unique_docs

    def run(self, **kwargs):
        return asyncio.run(self.run_async(**kwargs))


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Scrape network QoS/SDN docs for RAG")
    parser.add_argument("--output", default="network_docs_raw.json", help="Output JSON file")
    parser.add_argument("--se-key", default=None, help="Stack Exchange API key")
    parser.add_argument("--contact", default="https://github.com/Predigo-DS", help="Contact URL for User-Agent")
    parser.add_argument("--min-votes", type=int, default=0, help="Minimum SE question score")
    parser.add_argument("--threshold", type=float, default=None, help="Technical score threshold")
    parser.add_argument("--no-crawl4ai", action="store_true", help="Disable Crawl4AI")
    parser.add_argument("--dry-run", action="store_true", help="Print config and exit")
    
    args = parser.parse_args()
    
    if args.dry_run:
        print(f"Output: {args.output}")
        print(f"SE API Key: {'✓' if args.se_key else '✗'}")
        print(f"Contact: {args.contact}")
        print(f"Crawl4AI: {'✗ disabled' if args.no_crawl4ai else '✓ enabled'}")
        print(f"Technical threshold: {args.threshold or Config.TECHNICAL_SCORE_THRESHOLD}")
        return
    
    scraper = NetworkDocScraper(
        output_file=args.output,
        se_api_key=args.se_key,
        contact_url=args.contact,
        use_crawl4ai=not args.no_crawl4ai,
        technical_threshold=args.threshold
    )
    
    scraper.run(se_tags=None, doc_urls=None, min_votes=args.min_votes)


if __name__ == "__main__":
    main()