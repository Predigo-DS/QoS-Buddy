#!/usr/bin/env python3
"""
SDN/Network RAG Data Preparation Pipeline — LLM Quality Pass
=============================================================
Processes raw scraped documents through:
  Phase 1: Data Validation & Preprocessing
  Phase 2: LLM Quality Pass (Gatekeeper)
  Phase 3: Metadata Enrichment & Classification

Does NOT include chunking, embedding, or vector storage.

Uses LangGraph for workflow orchestration and OpenAI-compatible API.
"""

import json
import asyncio
import logging
import os
import re
import hashlib
import time
import threading
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Literal
from enum import Enum

from tqdm import tqdm
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from datasketch import MinHash, MinHashLSH

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# LangGraph imports
from langgraph.graph import StateGraph, START, END

# Configure logging — file by default, console only with --verbose
LOG_FILE = "preparer.log"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# File handler — always active
_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler.setFormatter(_file_fmt)
logger.addHandler(_file_handler)

# Console handler — only for --verbose (INFO level)
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_fmt = logging.Formatter("[%(levelname)s] %(message)s")
_console_handler.setFormatter(_console_fmt)
logger.addHandler(_console_handler)


class ProgressTracker:
    """Thread-safe progress tracker for concurrent document processing."""
    
    def __init__(self, total: int, desc: str = "Processing"):
        self.total = total
        self.desc = desc
        self.lock = threading.Lock()
        self.done = 0
        self.kept = 0
        self.failed = 0
        self.skipped = 0
        self.start_time = time.perf_counter()
        self.node_times: Dict[str, float] = {}
        self.node_start: Optional[float] = None
        
    def set_node_start(self, node_name: str):
        with self.lock:
            self.node_start = time.perf_counter()
            self.node_times[node_name] = 0  # placeholder
        
    def record_complete(self, status: str = "kept"):
        with self.lock:
            self.done += 1
            if status == "kept":
                self.kept += 1
            elif status == "failed":
                self.failed += 1
            elif status == "skipped":
                self.skipped += 1
    
    def record_node_end(self, node_name: str):
        with self.lock:
            if self.node_start is not None:
                self.node_times[node_name] = time.perf_counter() - self.node_start
                self.node_start = None
    
    def get_eta(self) -> str:
        with self.lock:
            elapsed = time.perf_counter() - self.start_time
            if self.done == 0:
                return "--:--"
            remaining = self.total - self.done
            rate = self.done / elapsed if elapsed > 0 else 0
            eta_secs = remaining / rate if rate > 0 else 0
            return self._format_secs(eta_secs)
    
    def get_elapsed(self) -> str:
        elapsed = time.perf_counter() - self.start_time
        return self._format_secs(elapsed)
    
    def get_total_time(self) -> str:
        with self.lock:
            elapsed = time.perf_counter() - self.start_time
            return self._format_secs(elapsed)
    
    @staticmethod
    def _format_secs(secs: float) -> str:
        if secs < 60:
            return f"{secs:.0f}s"
        mins = int(secs // 60)
        secs = secs % 60
        return f"{mins:02d}:{secs:05.2f}"
    
    def bar(self) -> tqdm:
        return tqdm(
            total=self.total,
            desc=self.desc,
            file=sys.stdout,
            leave=True,
            bar_format="{l_bar}{bar:30}{r_bar}",
            dynamic_ncols=True,
        )
    
    def update(self, pbar: tqdm, status: str = "kept"):
        with self.lock:
            self.done += 1
            if status == "kept":
                self.kept += 1
            elif status == "failed":
                self.failed += 1
            elif status == "skipped":
                self.skipped += 1
        
        elapsed = self.get_elapsed()
        eta = self.get_eta()
        kept = self.kept
        failed = self.failed
        skipped = self.skipped
        
        pbar.update(1)
        pbar.set_postfix_str(
            f"✅{kept} ❌{failed} ⏭️{skipped} | {elapsed} elapsed | ETA {eta}"
        )


def make_phase_bar(total: int, desc: str) -> tqdm:
    """Create a consistent tqdm bar for phase-level progress."""
    return tqdm(
        total=total,
        desc=desc,
        file=sys.stdout,
        leave=True,
        bar_format="{l_bar}{bar:30}{r_bar}",
        dynamic_ncols=True,
    )


# ──────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────
class PipelineConfig:
    """Pipeline configuration with defaults and environment variable overrides."""
    
    # LLM Settings
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "sk-xxx")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_TEMPERATURE: float = 0.1  # Low for consistent evaluation
    
    # Processing Settings
    MIN_QUALITY_SCORE: int = 4
    MAX_CONCURRENT_LLM_CALLS: int = 8
    LLM_REQUEST_TIMEOUT: int = 120
    LLM_RETRY_ATTEMPTS: int = 3
    MAX_TEXT_LENGTH: int = 6000  # Truncate text for LLM to avoid token limits
    NEAR_DUPLICATE_THRESHOLD: float = 0.85
    
    # Output Settings
    OUTPUT_FILE: str = "network_docs_prepared.json"
    FAILED_FILE: str = "network_docs_failed.json"
    
    # Tag normalization mapping
    TAG_NORMALIZATION: Dict[str, str] = {
        "sdn": "SDN",
        "software-defined-network": "SDN",
        "software-defined-networking": "SDN",
        "openflow": "OpenFlow",
        "qos": "QoS",
        "mininet": "Mininet",
        "bandwidth": "Bandwidth",
        "ryu": "Ryu",
        "ovs": "OVS",
        "open-vswitch": "OVS",
        "cisco": "Cisco",
        "juniper": "Juniper",
        "arista": "Arista",
        "opendaylight": "OpenDaylight",
        "onos": "ONOS",
        "bgp": "BGP",
        "ospf": "OSPF",
        "vlan": "VLAN",
        "vxlan": "VXLAN",
        "mpls": "MPLS",
        "dscp": "DSCP",
        "latency": "Latency",
        "jitter": "Jitter",
        "throughput": "Throughput",
        "policing": "Policing",
        "shaping": "Shaping",
    }
    
    # Source type taxonomy mapping
    SOURCE_TYPE_TAXONOMY: Dict[str, str] = {
        "stackexchange_qa": "stackexchange",
        "documentation": "official_doc",
        "tutorial": "tutorial",
        "api_reference": "api_reference",
        "resource_hub": "resource_hub",
    }


# ──────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS FOR STRUCTURED LLM OUTPUT
# ──────────────────────────────────────────────────────────
class QualityAction(str, Enum):
    """Actions the LLM can take on a document."""
    KEEP = "KEEP"
    ENRICH = "ENRICH"
    SKIP = "SKIP"


class QualityEvaluation(BaseModel):
    """LLM output schema for quality evaluation."""
    quality_score: int = Field(
        ge=1, le=10,
        description="Technical quality score 1-10. 1=Hallucination/Wrong, 5=Vague/Outdated, 10=Perfect."
    )
    action: QualityAction = Field(
        description="KEEP if score 7-10, ENRICH if score 4-6, SKIP if score < 4"
    )
    reason: str = Field(
        description="Brief explanation of the score and action (1-2 sentences)"
    )
    version_tag: Optional[str] = Field(
        default=None,
        description="Detected version info (e.g., 'OpenFlow 1.3', 'Mininet 2.x', 'IOS 15.x')"
    )
    enriched_text: Optional[str] = Field(
        default=None,
        description="If ENRICH: improved text with context. DO NOT rewrite code blocks unless broken."
    )


class MetadataExtraction(BaseModel):
    """LLM output schema for metadata extraction."""
    content_type: Literal["troubleshooting", "reference", "theory", "configuration", "tutorial"] = Field(
        description="Primary content classification based on purpose"
    )
    vendor: Optional[str] = Field(
        default=None,
        description="Detected vendor (Cisco, Juniper, Arista, etc.) if applicable"
    )
    technology: List[str] = Field(
        default_factory=list,
        description="List of technologies mentioned (SDN, OpenFlow, QoS, BGP, etc.)"
    )
    problem_summary: Optional[str] = Field(
        default=None,
        description="For troubleshooting: brief summary of the problem being solved"
    )
    context_summary: Optional[str] = Field(
        default=None,
        description="1-2 sentence summary of the document's topic and scope for retrieval context"
    )
    code_block: Optional[str] = Field(
        default=None,
        description="Extract the primary code/config block if present, verbatim"
    )
    has_syntax_errors: bool = Field(
        default=False,
        description="True if code blocks contain obvious syntax errors"
    )


class QaReformulation(BaseModel):
    """LLM output schema for Q&A reformulation."""
    reformulated_text: str = Field(
        description="Rewritten technical summary as cohesive prose. "
                    "Remove conversational filler, keep all technical content and code blocks verbatim. "
                    "Write in clear professional prose suitable for a RAG knowledge base."
    )


# ──────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS FOR BATCH LLM OUTPUT
# ──────────────────────────────────────────────────────────
class BatchQualityResult(BaseModel):
    """Single result within a batch quality evaluation."""
    doc_id: str = Field(
        description="Unique document identifier (content_hash)"
    )
    quality_score: int = Field(
        ge=1, le=10,
        description="Technical quality score 1-10. 1=Hallucination/Wrong, 5=Vague/Outdated, 10=Perfect."
    )
    action: QualityAction = Field(
        description="KEEP if score 7-10, ENRICH if score 4-6, SKIP if score < 4"
    )
    reason: str = Field(
        description="Brief explanation of the score and action (1-2 sentences)"
    )
    version_tag: Optional[str] = Field(
        default=None,
        description="Detected version info (e.g., 'OpenFlow 1.3', 'Mininet 2.x', 'IOS 15.x')"
    )
    enriched_text: Optional[str] = Field(
        default=None,
        description="If ENRICH: improved text with context. DO NOT rewrite code blocks unless broken."
    )


class BatchQualityEvaluation(BaseModel):
    """Batch quality evaluation output — a list of per-document results."""
    results: List[BatchQualityResult] = Field(
        description="List of quality evaluation results, one per input document, in order"
    )


class BatchMetadataResult(BaseModel):
    """Single result within a batch metadata extraction."""
    doc_id: str = Field(
        description="Unique document identifier (content_hash)"
    )
    content_type: Literal["troubleshooting", "reference", "theory", "configuration", "tutorial"] = Field(
        description="Primary content classification based on purpose"
    )
    vendor: Optional[str] = Field(
        default=None,
        description="Detected vendor (Cisco, Juniper, Arista, etc.) if applicable"
    )
    technology: List[str] = Field(
        default_factory=list,
        description="List of technologies mentioned (SDN, OpenFlow, QoS, BGP, etc.)"
    )
    problem_summary: Optional[str] = Field(
        default=None,
        description="For troubleshooting: brief summary of the problem being solved"
    )
    context_summary: Optional[str] = Field(
        default=None,
        description="1-2 sentence summary of the document's topic and scope for retrieval context"
    )
    code_block: Optional[str] = Field(
        default=None,
        description="Extract the primary code/config block if present, verbatim"
    )
    has_syntax_errors: bool = Field(
        default=False,
        description="True if code blocks contain obvious syntax errors"
    )


class BatchMetadataExtraction(BaseModel):
    """Batch metadata extraction output — a list of per-document results."""
    results: List[BatchMetadataResult] = Field(
        description="List of metadata extraction results, one per input document, in order"
    )


class BatchReformulationResult(BaseModel):
    """Single result within a batch Q&A reformulation."""
    doc_id: str = Field(
        description="Unique document identifier (content_hash)"
    )
    reformulated_text: str = Field(
        description="Rewritten technical summary as cohesive prose."
    )


class BatchQaReformulation(BaseModel):
    """Batch Q&A reformulation output — a list of per-document results."""
    results: List[BatchReformulationResult] = Field(
        description="List of reformulated texts, one per input document, in order"
    )


# ──────────────────────────────────────────────────────────
# STATE DEFINITIONS
# ──────────────────────────────────────────────────────────
class PipelineState(TypedDict):
    """Global state for the entire pipeline."""
    documents: List[Dict[str, Any]]
    processed_docs: List[Dict[str, Any]]
    failed_docs: List[Dict[str, Any]]
    stats: Dict[str, Any]
    config: Dict[str, Any]
    _node_times: Dict[str, float]


# ──────────────────────────────────────────────────────────
# PROMPT TEMPLATES
# ──────────────────────────────────────────────────────────
QUALITY_SYSTEM_PROMPT = """You are a Senior Network Engineer reviewing technical documentation for an RAG database. 
Focus areas: SDN, Mininet, Ryu, Cisco, QoS, OpenFlow, OVS, and network automation.

TASK:
1. Evaluate the technical accuracy of the text.
2. Check if commands/versions are outdated (e.g., OpenFlow 1.0 vs 1.3, Mininet 1.x vs 2.x).
3. If the text is vague but has potential, add brief context or examples in enriched_text.
4. DO NOT rewrite code blocks unless they are syntactically broken.
5. Identify version-specific information if mentioned.

SCORING GUIDE:
- 10: Perfect technical accuracy, current versions, clear code examples
- 8-9: Accurate with minor issues (slight ambiguity, could use more context)
- 6-7: Generally correct but outdated versions or missing context
- 4-5: Vague, potentially misleading, or significantly outdated
- 1-3: Hallucination, fundamentally wrong, or spam

ACTION GUIDE:
- KEEP: Score 7-10 (good quality as-is)
- ENRICH: Score 4-6 (has potential, provide improved text)
- SKIP: Score 1-3 (reject this document)

OUTPUT: Valid JSON matching the required schema only, no other text."""


METADATA_SYSTEM_PROMPT = """You are a technical metadata extractor for network engineering documentation.

TASK:
1. Classify the content type based on primary purpose.
2. Extract vendor information if mentioned (Cisco, Juniper, Arista, etc.).
3. List all networking technologies referenced.
4. For Q&A/troubleshooting content: summarize the core problem being solved.
5. Extract the primary code or configuration block if present (verbatim, no modifications).
6. Check for obvious syntax errors in any code blocks.

CONTENT TYPE DEFINITIONS:
- troubleshooting: Q&A about solving specific problems, debugging, error resolution
- reference: API docs, command references, specification details, parameter lists
- theory: Conceptual explanations, definitions, architecture overviews, comparisons
- configuration: Setup guides, config file examples, deployment steps without deep explanation
- tutorial: Step-by-step learning guides with explanations and examples

OUTPUT: Valid JSON matching the required schema only, no other text."""

REFORMULATION_SYSTEM_PROMPT = """You are a technical documentation writer for a network engineering RAG knowledge base.

TASK: Convert the provided Q&A content into a single, cohesive technical summary.

RULES:
1. Remove conversational filler ("I want to know", "Can anyone explain", "Thanks in advance", etc.)
2. Keep ALL technical content: concepts, commands, configurations, code blocks, references
3. Preserve code blocks verbatim — do NOT modify them
4. Write in clear, professional prose suitable for an RAG knowledge base
5. If multiple answers are provided, merge the best/most accurate parts into one summary
6. If the question is "What is X?" or "Explain Y", make the answer read like an encyclopedia entry
7. If the question is about troubleshooting, structure it as: Problem → Solution → Explanation

OUTPUT: Valid JSON matching the required schema only, no other text."""


# ──────────────────────────────────────────────────────────
# BATCH PROMPT TEMPLATES
# ──────────────────────────────────────────────────────────
BATCH_QUALITY_SYSTEM_PROMPT = """You are a Senior Network Engineer reviewing technical documentation for an RAG database. 
Focus areas: SDN, Mininet, Ryu, Cisco, QoS, OpenFlow, OVS, and network automation.

TASK: Evaluate the technical accuracy of each document provided below.
For each document:
1. Score its technical accuracy (1-10).
2. Check if commands/versions are outdated.
3. If the text is vague but has potential, add brief context in enriched_text.
4. DO NOT rewrite code blocks unless they are syntactically broken.
5. Identify version-specific information if mentioned.

SCORING GUIDE:
- 10: Perfect technical accuracy, current versions, clear code examples
- 8-9: Accurate with minor issues
- 6-7: Generally correct but outdated versions or missing context
- 4-5: Vague, potentially misleading, or significantly outdated
- 1-3: Hallucination, fundamentally wrong, or spam

ACTION GUIDE:
- KEEP: Score 7-10
- ENRICH: Score 4-6
- SKIP: Score 1-3

INPUT FORMAT:
Documents are separated by "===DOC===DELIMITER==="
Each document has title, source, type, tags, and technical score.

OUTPUT: A JSON object with a "results" key containing a list of objects. Each object has:
- doc_id: the document's content_hash
- quality_score: integer 1-10
- action: "KEEP", "ENRICH", or "SKIP"
- reason: brief explanation (1-2 sentences)
- version_tag: detected version or null
- enriched_text: improved text if ENRICH, or null

Return ONLY the JSON object, no other text."""

BATCH_METADATA_SYSTEM_PROMPT = """You are a technical metadata extractor for network engineering documentation.

TASK: For each document provided, extract structured metadata.
1. Classify the content type.
2. Extract vendor information if mentioned.
3. List all networking technologies referenced.
4. For troubleshooting content: summarize the core problem.
5. Provide a 1-2 sentence topic/scope summary for retrieval context.
6. Extract the primary code/config block if present (verbatim).
7. Check for obvious syntax errors in code blocks.

CONTENT TYPE DEFINITIONS:
- troubleshooting: Q&A about solving specific problems, debugging
- reference: API docs, command references, specification details
- theory: Conceptual explanations, definitions, architecture overviews
- configuration: Setup guides, config file examples, deployment steps
- tutorial: Step-by-step learning guides with explanations

INPUT FORMAT:
Documents are separated by "===DOC===DELIMITER==="
Each document contains raw text.

OUTPUT: A JSON object with a "results" key containing a list of objects. Each object has:
- doc_id: the document's content_hash
- content_type: one of the defined types above
- vendor: vendor name or null
- technology: list of technology names
- problem_summary: summary or null
- context_summary: 1-2 sentence topic/scope summary or null
- code_block: code block text or null
- has_syntax_errors: boolean

Return ONLY the JSON object, no other text."""

BATCH_REFORMULATION_SYSTEM_PROMPT = """You are a technical documentation writer for a network engineering RAG knowledge base.

TASK: For each Q&A document provided, convert it into a single, cohesive technical summary.

RULES:
1. Remove conversational filler
2. Keep ALL technical content: concepts, commands, configurations, code blocks
3. Preserve code blocks verbatim
4. Write in clear, professional prose suitable for a RAG knowledge base
5. If multiple answers are provided, merge the best/most accurate parts
6. If the question is "What is X?" or "Explain Y", make the answer read like an encyclopedia entry
7. If troubleshooting, structure as: Problem → Solution → Explanation

INPUT FORMAT:
Documents are separated by "===DOC===DELIMITER==="
Each document contains raw Q&A text.

OUTPUT: A JSON object with a "results" key containing a list of objects. Each object has:
- doc_id: the document's content_hash
- reformulated_text: the rewritten technical summary

Return ONLY the JSON object, no other text."""


# ──────────────────────────────────────────────────────────
# LLM CLIENT FACTORY
# ──────────────────────────────────────────────────────────
def create_llm(
    api_key: str = None,
    base_url: str = None,
    model: str = None,
    temperature: float = None,
    timeout: int = None
) -> ChatOpenAI:
    """Create a configured OpenAI-compatible LLM client."""
    return ChatOpenAI(
        api_key=api_key or PipelineConfig.OPENAI_API_KEY,
        base_url=base_url or PipelineConfig.OPENAI_BASE_URL,
        model=model or PipelineConfig.LLM_MODEL,
        temperature=temperature or PipelineConfig.LLM_TEMPERATURE,
        timeout=timeout or PipelineConfig.LLM_REQUEST_TIMEOUT,
        max_retries=PipelineConfig.LLM_RETRY_ATTEMPTS,
    )


# ──────────────────────────────────────────────────────────
# PHASE 0: Q&A PAIR SPLITTING
# ──────────────────────────────────────────────────────────
def split_qa_pairs(doc: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Split a StackExchange Q&A thread into individual Q&A pair documents.

    Each answer becomes a separate document paired with its question.
    Questions with no answers or only low-quality answers are filtered out.
    """
    metadata = doc.get("metadata", {})
    
    if metadata.get("source_type") != "stackexchange_qa":
        return [doc]
    
    text = doc.get("text", "")
    if "### Top Answers:" not in text:
        return []
    
    qa_parts = text.split("### Top Answers:", 1)
    question = qa_parts[0].replace("Q: ", "").strip()
    answers_text = qa_parts[1]
    
    answer_blocks = re.split(r'\n\n---\n\n', answers_text)
    
    result = []
    for block in answer_blocks:
        block = block.strip()
        if not block:
            continue
        
        score_match = re.search(r'\[Score:(-?\d+)\]', block)
        score = int(score_match.group(1)) if score_match else 0
        
        answer_text = re.sub(r'^[#✓\d]+\s*\[Score:\d+\]\s*', '', block)
        
        if not answer_text.strip():
            continue
        
        qa_text = f"Q: {question}\n\n[Score:{score}] {answer_text}"
        qa_doc = {
            "text": qa_text,
            "metadata": metadata.copy(),
        }
        qa_doc["metadata"]["content_hash"] = hashlib.sha256(qa_text.encode()).hexdigest()[:8]
        qa_doc["metadata"]["se_answer_score"] = score
        qa_doc["metadata"]["qa_pair_index"] = len(result)
        
        result.append(qa_doc)
    
    return result if result else []


# ──────────────────────────────────────────────────────────
# PHASE 1: VALIDATION & PREPROCESSING
# ──────────────────────────────────────────────────────────
def validate_document_integrity(doc: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate document has actual usable content, not just questions or spam.
    Returns (passed: bool, reason: str).
    """
    text = doc.get("text", "")
    metadata = doc.get("metadata", {})
    
    # Check minimum content length
    if not text or len(text.strip()) < 50:
        return False, "Text too short (< 50 chars)"
    
    # For StackExchange Q&A: strict filtering
    if metadata.get("source_type") == "stackexchange_qa":
        answer_count = metadata.get("se_answer_count", 0)
        
        # Check for answer indicators in text (works for both raw threads and split Q&A pairs)
        answer_indicators = [
            "### Top Answers:", "✓", "#1 [Score:", "#2 [Score:", "#3 [Score:",
            "Accepted Answer", "Best Answer", "---\n\n",
            "Answer:", "answer is", "solution is", "you should", "[Score:"
        ]
        has_answer = any(indicator in text for indicator in answer_indicators)
        
        # Skip if no answers at all
        if answer_count == 0:
            return False, "StackExchange Q&A with no answers"
        
        # Skip if no answer content found in text
        if not has_answer:
            return False, "StackExchange Q&A with no answer content"
        
        # Skip question-only docs with minimal content
        question_starters = ["How do I", "How to", "Why does", "What is", "Is it possible", "Can I"]
        starts_with_question = any(text.strip().startswith(q) for q in question_starters)
        
        if starts_with_question and len(text) < 300 and not has_answer:
            return False, "Appears to be question-only with no answer resolution"
    
    # Check for spam/cookie/newsletter patterns
    spam_patterns = [
        r"(click here|subscribe to our newsletter)",
        r"(buy now|discount code|special offer|promotion)",
        r"(Lorem ipsum|test content|placeholder text)",
        r"(accept.*cookies|privacy policy.*accept)",
        r"(premium content|subscribe to read|paywall)",
    ]
    for pattern in spam_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return False, f"Spam/unwanted pattern detected: {pattern[:50]}"
    
    return True, "Valid"


def find_near_duplicate_indices(docs: List[Dict], threshold: float = 0.85) -> set:
    """Find indices of near-duplicate documents using MinHash LSH."""
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    
    # Build MinHash for each doc and insert into LSH index
    for i, doc in enumerate(docs):
        m = MinHash(num_perm=128)
        for word in doc.get("text", "").split():
            m.update(word.encode())
        lsh.insert(f"doc_{i}", m)
    
    # Query each doc against the index to find neighbors
    duplicates = set()
    for i in range(len(docs)):
        m = MinHash(num_perm=128)
        for word in docs[i].get("text", "").split():
            m.update(word.encode())
        neighbors = lsh.query(m)
        for neighbor_key in neighbors:
            try:
                neighbor_idx = int(neighbor_key.split("_")[1])
                if neighbor_idx != i:
                    duplicates.add(neighbor_idx)
            except (ValueError, IndexError):
                continue
    
    return duplicates


def normalize_metadata(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize metadata fields to standardized taxonomy."""
    metadata = doc.get("metadata", {}).copy()
    
    # Normalize source_type with taxonomy
    original_source = metadata.get("source_type", "unknown")
    metadata["source_type_original"] = original_source
    metadata["source_type"] = PipelineConfig.SOURCE_TYPE_TAXONOMY.get(
        original_source, original_source
    )
    
    # Normalize tags
    original_tags = metadata.get("tags", [])
    normalized_tags = []
    seen_tags = set()
    
    for tag in original_tags:
        tag_lower = tag.lower()
        normalized = PipelineConfig.TAG_NORMALIZATION.get(tag_lower, tag)
        
        if normalized not in seen_tags:
            normalized_tags.append(normalized)
            seen_tags.add(normalized)
    
    metadata["tags_original"] = original_tags
    metadata["tags"] = normalized_tags
    
    # Ensure required metadata fields
    metadata.setdefault("content_hash", hashlib.sha256(
        doc.get("text", "").encode()
    ).hexdigest()[:16])
    metadata.setdefault("processed_at", datetime.now(timezone.utc).isoformat())
    metadata.setdefault("pipeline_version", "1.0")
    
    return metadata


# ──────────────────────────────────────────────────────────
# PHASE 2: LLM QUALITY PASS
# ──────────────────────────────────────────────────────────
async def run_quality_evaluation(
    llm: ChatOpenAI,
    doc: Dict[str, Any],
    max_length: int = None
) -> Optional[QualityEvaluation]:
    """Run LLM quality pass on a document."""
    text = doc.get("text", "")[:max_length or PipelineConfig.MAX_TEXT_LENGTH]
    metadata = doc.get("metadata", {})
    
    prompt = f"""INPUT DATA:
---
Title: {metadata.get('title', 'N/A')}
Source: {metadata.get('source', 'N/A')}
Type: {metadata.get('source_type', 'N/A')}
Tags: {metadata.get('tags', [])}
Technical Score (heuristic): {metadata.get('technical_score', 'N/A')}
---

TEXT:
{text}

---

Evaluate this document and return your assessment as JSON."""

    try:
        chain = (
            ChatPromptTemplate.from_messages([
                ("system", QUALITY_SYSTEM_PROMPT),
                ("human", "{input}")
            ])
            | llm.with_structured_output(QualityEvaluation)
        )
        
        result = await chain.ainvoke({"input": prompt})
        return result
        
    except Exception as e:
        logger.warning(f"Quality evaluation LLM error: {e}")
        return None


# ──────────────────────────────────────────────────────────
# PHASE 3: METADATA ENRICHMENT
# ──────────────────────────────────────────────────────────
async def run_metadata_extraction(
    llm: ChatOpenAI,
    doc: Dict[str, Any],
    max_length: int = None
) -> Optional[MetadataExtraction]:
    """Run LLM metadata extraction on a document."""
    text = doc.get("text", "")[:max_length or PipelineConfig.MAX_TEXT_LENGTH]
    
    prompt = f"""Extract structured metadata from this document:

TEXT:
{text}

Return metadata extraction as JSON."""

    try:
        chain = (
            ChatPromptTemplate.from_messages([
                ("system", METADATA_SYSTEM_PROMPT),
                ("human", "{input}")
            ])
            | llm.with_structured_output(MetadataExtraction)
        )
        
        result = await chain.ainvoke({"input": prompt})
        return result
        
    except Exception as e:
        logger.warning(f"Metadata extraction LLM error: {e}")
        return None


# ──────────────────────────────────────────────────────────
# PHASE 3.5: Q&A REFORMULATION
# ──────────────────────────────────────────────────────────
async def run_qa_reformulation(
    llm: ChatOpenAI,
    doc: Dict[str, Any],
    max_length: int = None
) -> Optional[str]:
    """Reformulate a Q&A pair into a cohesive technical summary."""
    text = doc.get("text", "")[:max_length or PipelineConfig.MAX_TEXT_LENGTH]
    
    prompt = f"""INPUT Q&A:
---
{text}
---

Reformulate this into a cohesive technical summary."""

    try:
        chain = (
            ChatPromptTemplate.from_messages([
                ("system", REFORMULATION_SYSTEM_PROMPT),
                ("human", "{input}")
            ])
            | llm.with_structured_output(QaReformulation)
        )
        
        result = await chain.ainvoke({"input": prompt})
        return result.reformulated_text
        
    except Exception as e:
        logger.warning(f"QA reformulation LLM error: {e}")
        return None


# ──────────────────────────────────────────────────────────
# BATCH LLM FUNCTIONS (Phase-based parallel processing)
# ──────────────────────────────────────────────────────────
async def _parse_batch_quality(raw: Any) -> List[BatchQualityResult]:
    """Parse and validate a batch quality evaluation response."""
    if isinstance(raw, BatchQualityEvaluation):
        return raw.results
    if isinstance(raw, dict) and "results" in raw:
        return [BatchQualityResult(**r) for r in raw["results"]]
    if isinstance(raw, list):
        return [BatchQualityResult(**r) for r in raw]
    raise ValueError(f"Unexpected response type: {type(raw)}")


async def _parse_batch_metadata(raw: Any) -> List[BatchMetadataResult]:
    """Parse and validate a batch metadata extraction response."""
    if isinstance(raw, BatchMetadataExtraction):
        return raw.results
    if isinstance(raw, dict) and "results" in raw:
        return [BatchMetadataResult(**r) for r in raw["results"]]
    if isinstance(raw, list):
        return [BatchMetadataResult(**r) for r in raw]
    raise ValueError(f"Unexpected response type: {type(raw)}")


async def _parse_batch_reformulation(raw: Any) -> List[BatchReformulationResult]:
    """Parse and validate a batch Q&A reformulation response."""
    if isinstance(raw, BatchQaReformulation):
        return raw.results
    if isinstance(raw, dict) and "results" in raw:
        return [BatchReformulationResult(**r) for r in raw["results"]]
    if isinstance(raw, list):
        return [BatchReformulationResult(**r) for r in raw]
    raise ValueError(f"Unexpected response type: {type(raw)}")


async def run_batch_quality_evaluation(
    llm: ChatOpenAI,
    docs: List[Dict[str, Any]],
    batch_size: int = 15,
    max_concurrent: int = 8,
) -> List[QualityEvaluation]:
    """
    Evaluate quality of multiple documents in batches.
    
    Chunks docs into batches, sends each batch to LLM in parallel,
    then returns flat list of QualityEvaluation results.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    all_results: List[Optional[QualityEvaluation]] = []
    batches = [docs[i:i + batch_size] for i in range(0, len(docs), batch_size)]
    batch_bar = make_phase_bar(
        len(batches),
        f"Phase 2/4: Quality batches ({len(docs)} docs)",
    )
    
    async def process_batch(batch: List[Dict[str, Any]]) -> List[BatchQualityResult]:
        async with semaphore:
            try:
                # Build batch prompt with delimited documents
                doc_parts = []
                for i, doc in enumerate(batch):
                    meta = doc.get("metadata", {})
                    text = doc.get("text", "")[:PipelineConfig.MAX_TEXT_LENGTH]
                    doc_parts.append(
                        f"===DOC===DELIMITER===\n"
                        f"[DOC {i}]\n"
                        f"doc_id: {meta.get('content_hash', 'unknown')}\n"
                        f"Title: {meta.get('title', 'N/A')}\n"
                        f"Source: {meta.get('source', 'N/A')}\n"
                        f"Type: {meta.get('source_type', 'N/A')}\n"
                        f"Tags: {meta.get('tags', [])}\n"
                        f"Technical Score (heuristic): {meta.get('technical_score', 'N/A')}\n"
                        f"---\n"
                        f"TEXT:\n{text}\n"
                    )
                
                batch_prompt = "\n".join(doc_parts)
                
                chain = (
                    ChatPromptTemplate.from_messages([
                        ("system", BATCH_QUALITY_SYSTEM_PROMPT),
                        ("human", "{input}")
                    ])
                    | llm.with_structured_output(BatchQualityEvaluation)
                )
                result = await chain.ainvoke({"input": batch_prompt})
                return await _parse_batch_quality(result)
            except Exception as e:
                logger.warning(f"Batch quality evaluation failed (batch of {len(batch)}): {e}")
                # Fallback: return None for all docs in this batch
                return [BatchQualityResult(
                    doc_id=doc.get("metadata", {}).get("content_hash", f"unknown_{i}"),
                    quality_score=1,
                    action=QualityAction.SKIP,
                    reason=f"Batch evaluation failed: {e}",
                ) for i, doc in enumerate(batch)]
            finally:
                batch_bar.update(1)
    
    logger.info(f"  Quality phase: {len(docs)} docs → {len(batches)} batches (batch_size={batch_size}, concurrency={max_concurrent})")
    
    try:
        # Process all batches in parallel
        batch_results = await asyncio.gather(*[process_batch(b) for b in batches])
    finally:
        batch_bar.close()
    
    # Flatten results and map back to QualityEvaluation
    for batch_result in batch_results:
        for br in batch_result:
            all_results.append(QualityEvaluation(
                quality_score=br.quality_score,
                action=br.action,
                reason=br.reason,
                version_tag=br.version_tag,
                enriched_text=br.enriched_text,
            ))
    
    return all_results


async def run_batch_metadata_extraction(
    llm: ChatOpenAI,
    docs: List[Dict[str, Any]],
    batch_size: int = 12,
    max_concurrent: int = 8,
) -> List[MetadataExtraction]:
    """
    Extract metadata from multiple documents in batches.
    
    Chunks docs into batches, sends each batch to LLM in parallel,
    then returns flat list of MetadataExtraction results.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    batches = [docs[i:i + batch_size] for i in range(0, len(docs), batch_size)]
    batch_bar = make_phase_bar(
        len(batches),
        f"Phase 3/4: Metadata batches ({len(docs)} docs)",
    )
    
    async def process_batch(batch: List[Dict[str, Any]]) -> List[BatchMetadataResult]:
        async with semaphore:
            try:
                doc_parts = []
                for i, doc in enumerate(batch):
                    meta = doc.get("metadata", {})
                    text = doc.get("text", "")[:PipelineConfig.MAX_TEXT_LENGTH]
                    doc_parts.append(
                        f"===DOC===DELIMITER===\n"
                        f"[DOC {i}]\n"
                        f"doc_id: {meta.get('content_hash', 'unknown')}\n"
                        f"TEXT:\n{text}\n"
                    )
                
                batch_prompt = "\n".join(doc_parts)
                
                chain = (
                    ChatPromptTemplate.from_messages([
                        ("system", BATCH_METADATA_SYSTEM_PROMPT),
                        ("human", "{input}")
                    ])
                    | llm.with_structured_output(BatchMetadataExtraction)
                )
                result = await chain.ainvoke({"input": batch_prompt})
                return await _parse_batch_metadata(result)
            except Exception as e:
                logger.warning(f"Batch metadata extraction failed (batch of {len(batch)}): {e}")
                return [BatchMetadataResult(
                    doc_id=doc.get("metadata", {}).get("content_hash", f"unknown_{i}"),
                    content_type="theory",
                    vendor=None,
                    technology=[],
                    problem_summary=None,
                    context_summary=None,
                    code_block=None,
                    has_syntax_errors=False,
                ) for i, doc in enumerate(batch)]
            finally:
                batch_bar.update(1)
    
    logger.info(f"  Metadata phase: {len(docs)} docs → {len(batches)} batches (batch_size={batch_size}, concurrency={max_concurrent})")
    
    try:
        batch_results = await asyncio.gather(*[process_batch(b) for b in batches])
    finally:
        batch_bar.close()
    
    all_results = []
    for batch_result in batch_results:
        for br in batch_result:
            all_results.append(MetadataExtraction(
                content_type=br.content_type,
                vendor=br.vendor,
                technology=br.technology,
                problem_summary=br.problem_summary,
                context_summary=br.context_summary,
                code_block=br.code_block,
                has_syntax_errors=br.has_syntax_errors,
            ))
    
    return all_results


async def run_batch_qa_reformulation(
    llm: ChatOpenAI,
    docs: List[Dict[str, Any]],
    batch_size: int = 5,
    max_concurrent: int = 8,
) -> List[Optional[str]]:
    """
    Reformulate Q&A content for multiple documents in batches.
    
    Chunks docs into batches, sends each batch to LLM in parallel,
    then returns flat list of reformulated texts.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    batches = [docs[i:i + batch_size] for i in range(0, len(docs), batch_size)]
    batch_bar = make_phase_bar(
        len(batches),
        f"Phase 4/4: Reformulation batches ({len(docs)} docs)",
    )
    
    async def process_batch(batch: List[Dict[str, Any]]) -> List[BatchReformulationResult]:
        async with semaphore:
            try:
                doc_parts = []
                for i, doc in enumerate(batch):
                    meta = doc.get("metadata", {})
                    text = doc.get("text", "")[:PipelineConfig.MAX_TEXT_LENGTH]
                    doc_parts.append(
                        f"===DOC===DELIMITER===\n"
                        f"[DOC {i}]\n"
                        f"doc_id: {meta.get('content_hash', 'unknown')}\n"
                        f"TEXT:\n{text}\n"
                    )
                
                batch_prompt = "\n".join(doc_parts)
                
                chain = (
                    ChatPromptTemplate.from_messages([
                        ("system", BATCH_REFORMULATION_SYSTEM_PROMPT),
                        ("human", "{input}")
                    ])
                    | llm.with_structured_output(BatchQaReformulation)
                )
                result = await chain.ainvoke({"input": batch_prompt})
                return await _parse_batch_reformulation(result)
            except Exception as e:
                logger.warning(f"Batch reformulation failed (batch of {len(batch)}): {e}")
                return [BatchReformulationResult(
                    doc_id=doc.get("metadata", {}).get("content_hash", f"unknown_{i}"),
                    reformulated_text=None,
                ) for i, doc in enumerate(batch)]
            finally:
                batch_bar.update(1)
    
    logger.info(f"  Reformulation phase: {len(docs)} docs → {len(batches)} batches (batch_size={batch_size}, concurrency={max_concurrent})")
    
    try:
        batch_results = await asyncio.gather(*[process_batch(b) for b in batches])
    finally:
        batch_bar.close()
    
    results = []
    for batch_result in batch_results:
        for br in batch_result:
            results.append(br.reformulated_text)
    
    return results


# ──────────────────────────────────────────────────────────
# SINGLE DOCUMENT PROCESSOR
# ──────────────────────────────────────────────────────────
async def process_single_document(
    doc: Dict[str, Any],
    llm: ChatOpenAI,
    doc_idx: int,
    min_quality_score: int = None
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Process a single document through the full pipeline.
    Returns (final_doc_or_None, failed_doc_or_None).
    """
    min_score = min_quality_score or PipelineConfig.MIN_QUALITY_SCORE
    
    # Get document identifier for logging
    doc_title = doc.get("metadata", {}).get("title", "Unknown")
    doc_hash = doc.get("metadata", {}).get("content_hash", f"doc_{doc_idx}")
    
    logger.debug(f"📝 [{doc_idx}] Processing: {doc_title[:55]}...")
    
    # ══════════════════════════════════════════════════════
    # PHASE 1: VALIDATION
    # ══════════════════════════════════════════════════════
    passed, reason = validate_document_integrity(doc)
    if not passed:
        logger.debug(f"  ❌ Validation failed: {reason}")
        failed = {
            "original_doc": doc,
            "processing_error": f"VALIDATION: {reason}",
            "phase": "validation",
            "content_hash": doc_hash,
        }
        return None, failed
    
    # ══════════════════════════════════════════════════════
    # PHASE 1.5: METADATA NORMALIZATION
    # ══════════════════════════════════════════════════════
    normalized_metadata = normalize_metadata(doc)
    normalized_doc = {
        "text": doc.get("text", ""),
        "metadata": normalized_metadata,
    }
    
    # Preserve existing enriched fields if present
    if "code_snippets" in doc:
        normalized_doc["code_snippets"] = doc["code_snippets"]
    if "text_enriched" in doc:
        normalized_doc["text_enriched"] = doc["text_enriched"]
    
    # ══════════════════════════════════════════════════════
    # PHASE 2: LLM QUALITY PASS
    # ══════════════════════════════════════════════════════
    quality = await run_quality_evaluation(llm, normalized_doc)
    
    if quality is None:
        logger.warning(f"  ⚠️ Quality evaluation failed (LLM error), skipping")
        failed = {
            "original_doc": normalized_doc,
            "processing_error": "LLM quality evaluation returned None/error",
            "phase": "quality_evaluation",
            "content_hash": doc_hash,
        }
        return None, failed
    
    logger.debug(
        f"  📊 Quality: {quality.quality_score}/10 | "
        f"Action: {quality.action.value} | "
        f"{quality.reason[:50]}..."
    )
    
    # Handle SKIP action
    if quality.action == QualityAction.SKIP:
        failed = {
            "original_doc": normalized_doc,
            "processing_error": f"QUALITY_SKIP: {quality.reason}",
            "phase": "quality_evaluation",
            "llm_evaluation": quality.model_dump(),
            "content_hash": doc_hash,
        }
        return None, failed
    
    # Hard filter by minimum score (even if LLM said KEEP)
    if quality.quality_score < min_score:
        logger.info(f"  ⏭️ Below minimum score threshold ({min_score})")
        failed = {
            "original_doc": normalized_doc,
            "processing_error": f"BELOW_THRESHOLD: Score {quality.quality_score} < {min_score}",
            "phase": "quality_threshold",
            "llm_evaluation": quality.model_dump(),
            "content_hash": doc_hash,
        }
        return None, failed
    
    # ══════════════════════════════════════════════════════
    # PHASE 3: METADATA EXTRACTION & ENRICHMENT
    # ══════════════════════════════════════════════════════
    meta_extract = await run_metadata_extraction(llm, normalized_doc)
    
    if meta_extract is None:
        logger.warning(f"  ⚠️ Metadata extraction failed, using safe defaults")
        meta_extract = MetadataExtraction(
            content_type="theory",
            vendor=None,
            technology=[],
            problem_summary=None,
            code_block=None,
            has_syntax_errors=False
        )
    
    logger.debug(
        f"  🏷️ Type: {meta_extract.content_type} | "
        f"Vendor: {meta_extract.vendor or 'N/A'} | "
        f"Tech: {', '.join(meta_extract.technology[:3]) or 'N/A'}"
    )
    
    # ══════════════════════════════════════════════════════
    # PHASE 3.5: Q&A REFORMULATION (StackExchange only)
    # ══════════════════════════════════════════════════════
    final_metadata = normalized_metadata.copy()
    final_text = normalized_doc.get("text_enriched") or normalized_doc.get("text")
    
    if normalized_metadata.get("source_type") == "stackexchange":
        logger.debug("  🔧 Running Q&A reformulation...")
        reformulated = await run_qa_reformulation(llm, normalized_doc)
        
        if reformulated:
            logger.debug("  ✅ Q&A reformulated successfully")
            final_text = reformulated
            final_metadata["text_was_reformulated"] = True
        else:
            logger.debug("  ⚠️ Reformulation failed, using original text")
            final_metadata["text_was_reformulated"] = False
    else:
        # Non-SE docs: use enriched or original text
        if quality.action == QualityAction.ENRICH and quality.enriched_text:
            final_text = quality.enriched_text
            final_metadata["text_was_enriched"] = True
        else:
            final_metadata["text_was_enriched"] = False
    
    # Generate context summary if not already present
    if not meta_extract.context_summary:
        doc_title = doc.get("metadata", {}).get("title", "Unknown")
        summary_prompt = f"""Summarize this document's topic and scope in exactly 1-2 sentences for retrieval context.
Title: {doc_title}
Content type: {meta_extract.content_type}
Tags: {', '.join(meta_extract.technology[:5])}

Return only the summary text, nothing else."""
        try:
            summary_chain = (
                ChatPromptTemplate.from_messages([
                    ("system", "You are a technical documentation assistant. Provide concise summaries."),
                    ("human", summary_prompt)
                ])
                | llm
            )
            summary_result = await summary_chain.ainvoke({"input": ""})
            meta_extract.context_summary = summary_result.content.strip()
        except Exception:
            meta_extract.context_summary = f"Document about {meta_extract.content_type} content"
    
      # ══════════════════════════════════════════════════════
    # BUILD FINAL DOCUMENT
    # ══════════════════════════════════════════════════════
    # Add LLM evaluation metadata
    final_metadata.update({
        "llm_quality_score": quality.quality_score,
        "llm_action": quality.action.value,
        "llm_evaluation_reason": quality.reason,
        "llm_verified": quality.action == QualityAction.KEEP,
    })
    
    # Add extracted metadata
    final_metadata.update({
        "content_type": meta_extract.content_type,
        "vendor": meta_extract.vendor,
        "technology": meta_extract.technology,
        "problem_summary": meta_extract.problem_summary,
        "context_summary": meta_extract.context_summary,
        "has_syntax_errors": meta_extract.has_syntax_errors,
        "status": "needs_review" if quality.action == QualityAction.ENRICH else "verified",
    })
    
    # Add version tag if detected
    if quality.version_tag:
        final_metadata["version_tag"] = quality.version_tag
    
    # Add code block if extracted
    if meta_extract.code_block:
        final_metadata["code_block"] = meta_extract.code_block
    
    # Construct final document
    final_doc = {
        "text": final_text,
        "metadata": final_metadata,
    }
    
    # Preserve code snippets from scraper if present
    if normalized_doc.get("code_snippets"):
        final_doc["code_snippets"] = normalized_doc["code_snippets"]
    
    logger.debug(f"  ✅ Finalized: status={final_metadata['status']}")
    return final_doc, None


# ──────────────────────────────────────────────────────────
# LANGGRAPH PIPELINE DEFINITION
# ──────────────────────────────────────────────────────────
def build_pipeline_graph(config: Dict[str, Any]) -> StateGraph:
    """
    Build the LangGraph state graph for the data preparation pipeline.
    
    Graph structure:
        START → load_documents → process_documents → compute_statistics 
             → save_results → log_summary → END
    """
    
    graph = StateGraph(PipelineState)
    
    # ── Node: Load Documents ──
    async def load_documents(state: PipelineState) -> PipelineState:
        """Load raw documents from the input JSON file."""
        input_file = config.get("input_file", "network_docs_raw.json")
        
        logger.info(f"📂 Loading documents from: {input_file}")
        
        if not os.path.exists(input_file):
            logger.error(f"❌ Input file not found: {input_file}")
            state["documents"] = []
            return state
        
        file_size = os.path.getsize(input_file)
        size_str = f"{file_size / (1024*1024):.1f} MB" if file_size > 1024*1024 else f"{file_size / 1024:.1f} KB"
        
        with open(input_file, "r", encoding="utf-8") as f:
            docs = json.load(f)
        
        logger.info(f"   ✓ Loaded {len(docs)} raw documents ({size_str})")
        state["documents"] = docs
        state["stats"]["input_count"] = len(docs)
        state["stats"]["input_file"] = input_file
        
        return state
    
    # ── Node: Process All Documents ──
    async def process_documents(state: PipelineState) -> PipelineState:
        """Process all documents through phase-based parallel pipeline:
        quality → metadata → reformulation → final build."""
        docs = state["documents"]
        
        if not docs:
            logger.warning("No documents to process")
            return state
        
        processed = []
        failed = []
        
        # Create LLM client
        llm = create_llm(
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            model=config.get("model"),
            temperature=config.get("temperature", 0.1),
            timeout=config.get("timeout"),
        )
        
        # Phase-based concurrency (higher than per-doc since batches are more efficient)
        max_concurrent = config.get("max_concurrent", PipelineConfig.MAX_CONCURRENT_LLM_CALLS)
        min_quality_score = config.get("min_quality_score") or PipelineConfig.MIN_QUALITY_SCORE
        
        # ── Pre-processing: Q&A splitting & dedup (no LLM) ──
        logger.info("📂 Splitting StackExchange Q&A threads into Q&A pairs...")
        expanded_docs = []
        qa_split_count = 0
        qa_filtered_count = 0
        preprocess_bar = make_phase_bar(len(docs), "Preprocessing: Q&A split")
        try:
            for doc in docs:
                metadata = doc.get("metadata", {})
                if metadata.get("source_type") == "stackexchange_qa":
                    pairs = split_qa_pairs(doc)
                    if pairs:
                        expanded_docs.extend(pairs)
                        qa_split_count += len(pairs)
                        if len(pairs) == 1 and pairs[0] is doc:
                            pass
                        else:
                            qa_filtered_count += 1
                    else:
                        qa_filtered_count += 1
                else:
                    expanded_docs.append(doc)
                preprocess_bar.update(1)
        finally:
            preprocess_bar.close()
        
        if qa_split_count > 0 or qa_filtered_count > 0:
            logger.info(f"  Q&A split: {qa_split_count} pairs created, {qa_filtered_count} Q&A docs filtered/expanded")
            logger.info(f"  Total docs: {len(docs)} → {len(expanded_docs)}")
        docs = expanded_docs
        
        logger.info(f"🔍 Running near-duplicate detection (MinHash LSH, threshold={PipelineConfig.NEAR_DUPLICATE_THRESHOLD})...")
        dup_indices = find_near_duplicate_indices(docs, threshold=PipelineConfig.NEAR_DUPLICATE_THRESHOLD)
        if dup_indices:
            logger.info(f"🗑️ Removing {len(dup_indices)} near-duplicate documents")
            docs = [doc for i, doc in enumerate(docs) if i not in dup_indices]
            logger.info(f"📄 {len(docs)} documents remaining after dedup")
        
        # ── Phase 1: Validation + Normalization (no LLM, sequential) ──
        logger.info(f"📋 Phase 1/4: Validating & normalizing {len(docs)} documents...")
        validated_docs = []
        validation_bar = make_phase_bar(len(docs), "Phase 1/4: Validation")
        try:
            for idx, doc in enumerate(docs):
                passed, reason = validate_document_integrity(doc)
                if not passed:
                    failed.append({
                        "original_doc": doc,
                        "processing_error": f"VALIDATION: {reason}",
                        "phase": "validation",
                        "content_hash": doc.get("metadata", {}).get("content_hash", f"doc_{idx}"),
                    })
                    validation_bar.update(1)
                    continue
                normalized_metadata = normalize_metadata(doc)
                normalized_doc = {
                    "text": doc.get("text", ""),
                    "metadata": normalized_metadata,
                }
                if "code_snippets" in doc:
                    normalized_doc["code_snippets"] = doc["code_snippets"]
                if "text_enriched" in doc:
                    normalized_doc["text_enriched"] = doc["text_enriched"]
                validated_docs.append(normalized_doc)
                validation_bar.update(1)
        finally:
            validation_bar.close()
        
        logger.info(f"  ✅ {len(validated_docs)}/{len(docs)} documents passed validation")
        
        if not validated_docs:
            logger.warning("No documents passed validation")
            state["processed_docs"] = []
            state["failed_docs"] = failed
            state["stats"]["processed_count"] = 0
            state["stats"]["failed_count"] = len(failed)
            return state
        
        # ── Phase 2: Batch Quality Evaluation ──
        logger.info(f"⚡ Phase 2/4: Quality evaluation ({len(validated_docs)} docs)...")
        quality_results = await run_batch_quality_evaluation(
            llm, validated_docs,
            batch_size=15,
            max_concurrent=max_concurrent,
        )
        
        # Filter by quality results
        quality_passed = []
        quality_skipped = 0
        quality_below_threshold = 0
        quality_failed = 0
        
        for idx, (doc, quality) in enumerate(zip(validated_docs, quality_results)):
            if quality is None:
                quality_failed += 1
                failed.append({
                    "original_doc": doc,
                    "processing_error": "LLM quality evaluation returned None",
                    "phase": "quality_evaluation",
                    "content_hash": doc.get("metadata", {}).get("content_hash", f"doc_{idx}"),
                })
                continue
            
            if quality.action == QualityAction.SKIP:
                quality_skipped += 1
                failed.append({
                    "original_doc": doc,
                    "processing_error": f"QUALITY_SKIP: {quality.reason}",
                    "phase": "quality_evaluation",
                    "llm_evaluation": quality.model_dump(),
                    "content_hash": doc.get("metadata", {}).get("content_hash", f"doc_{idx}"),
                })
                continue
            
            if quality.quality_score < min_quality_score:
                quality_below_threshold += 1
                failed.append({
                    "original_doc": doc,
                    "processing_error": f"BELOW_THRESHOLD: Score {quality.quality_score} < {min_quality_score}",
                    "phase": "quality_threshold",
                    "llm_evaluation": quality.model_dump(),
                    "content_hash": doc.get("metadata", {}).get("content_hash", f"doc_{idx}"),
                })
                continue
            
            quality_passed.append((doc, quality))
        
        logger.info(
            f"  Quality results: ✅ {len(quality_passed)} kept | "
            f"⏭️ {quality_skipped} skipped | "
            f"⬇️ {quality_below_threshold} below threshold | "
            f"❌ {quality_failed} failed"
        )
        
        if not quality_passed:
            logger.warning("No documents passed quality threshold")
            state["processed_docs"] = []
            state["failed_docs"] = failed
            state["stats"]["processed_count"] = 0
            state["stats"]["failed_count"] = len(failed)
            return state
        
        # ── Phase 3: Batch Metadata Extraction ──
        logger.info(f"🏷️ Phase 3/4: Metadata extraction ({len(quality_passed)} docs)...")
        meta_docs = [doc for doc, _ in quality_passed]
        meta_results = await run_batch_metadata_extraction(
            llm, meta_docs,
            batch_size=12,
            max_concurrent=max_concurrent,
        )
        
        # Separate SE docs for reformulation
        se_docs = []
        se_indices = []
        for idx, (doc, quality) in enumerate(quality_passed):
            if doc.get("metadata", {}).get("source_type") == "stackexchange":
                se_docs.append(doc)
                se_indices.append(idx)
        
        logger.info(f"  StackExchange docs for reformulation: {len(se_docs)}")
        
        # ── Phase 4: Batch Q&A Reformulation (SE docs only) ──
        reform_texts = {}  # idx -> reformulated_text
        if se_docs:
            logger.info(f"🔧 Phase 4/4: Q&A reformulation ({len(se_docs)} docs)...")
            reform_results = await run_batch_qa_reformulation(
                llm, se_docs,
                batch_size=5,
                max_concurrent=max_concurrent,
            )
            for idx, reform_text in zip(se_indices, reform_results):
                reform_texts[idx] = reform_text
        
        # ── Build Final Documents ──
        logger.info(f"📦 Building {len(quality_passed)} final documents...")
        tracker = ProgressTracker(len(quality_passed), desc="Building")
        pbar = tracker.bar()
        
        for idx, (doc, quality) in enumerate(quality_passed):
            if idx < len(meta_results):
                meta_extract = meta_results[idx]
            else:
                logger.warning(
                    f"Metadata batch returned {len(meta_results)} results for {len(quality_passed)} docs; "
                    f"using defaults for doc index {idx}"
                )
                meta_extract = MetadataExtraction(
                    content_type="theory",
                    vendor=None,
                    technology=[],
                    problem_summary=None,
                    context_summary=None,
                    code_block=None,
                    has_syntax_errors=False,
                )
            if meta_extract is None:
                meta_extract = MetadataExtraction(
                    content_type="theory", vendor=None, technology=[],
                    problem_summary=None, code_block=None, has_syntax_errors=False,
                )
            
            normalized_metadata = doc["metadata"]
            final_metadata = normalized_metadata.copy()
            final_text = doc.get("text_enriched") or doc.get("text")
            
            # Apply reformulation for SE docs
            if idx in reform_texts and reform_texts[idx]:
                final_text = reform_texts[idx]
                final_metadata["text_was_reformulated"] = True
            elif doc.get("metadata", {}).get("source_type") == "stackexchange":
                final_metadata["text_was_reformulated"] = False
            
            # Apply enriched text if available
            if quality.action == QualityAction.ENRICH and quality.enriched_text:
                if doc.get("metadata", {}).get("source_type") != "stackexchange":
                    final_text = quality.enriched_text
                    final_metadata["text_was_enriched"] = True
                else:
                    final_metadata["text_was_enriched"] = False
            else:
                if doc.get("metadata", {}).get("source_type") != "stackexchange":
                    final_metadata["text_was_enriched"] = False
            
            # Generate context summary if needed
            if not meta_extract.context_summary:
                doc_title = doc.get("metadata", {}).get("title", "Unknown")
                summary_prompt = f"""Summarize this document's topic and scope in exactly 1-2 sentences for retrieval context.
Title: {doc_title}
Content type: {meta_extract.content_type}
Tags: {', '.join(meta_extract.technology[:5])}

Return only the summary text, nothing else."""
                try:
                    summary_chain = (
                        ChatPromptTemplate.from_messages([
                            ("system", "You are a technical documentation assistant. Provide concise summaries."),
                            ("human", summary_prompt)
                        ])
                        | llm
                    )
                    summary_result = await summary_chain.ainvoke({"input": ""})
                    meta_extract.context_summary = summary_result.content.strip()
                except Exception:
                    meta_extract.context_summary = f"Document about {meta_extract.content_type} content"
            
            # Build metadata
            final_metadata.update({
                "llm_quality_score": quality.quality_score,
                "llm_action": quality.action.value,
                "llm_evaluation_reason": quality.reason,
                "llm_verified": quality.action == QualityAction.KEEP,
                "content_type": meta_extract.content_type,
                "vendor": meta_extract.vendor,
                "technology": meta_extract.technology,
                "problem_summary": meta_extract.problem_summary,
                "context_summary": meta_extract.context_summary,
                "has_syntax_errors": meta_extract.has_syntax_errors,
                "status": "needs_review" if quality.action == QualityAction.ENRICH else "verified",
            })
            
            if quality.version_tag:
                final_metadata["version_tag"] = quality.version_tag
            if meta_extract.code_block:
                final_metadata["code_block"] = meta_extract.code_block
            
            final_doc = {
                "text": final_text,
                "metadata": final_metadata,
            }
            if doc.get("code_snippets"):
                final_doc["code_snippets"] = doc["code_snippets"]
            
            processed.append(final_doc)
            tracker.update(pbar, "kept")
        
        pbar.close()
        
        state["processed_docs"] = processed
        state["failed_docs"] = failed
        state["stats"]["processed_count"] = len(processed)
        state["stats"]["failed_count"] = len(failed)
        
        return state
    
    # ── Node: Compute Statistics ──
    async def compute_statistics(state: PipelineState) -> PipelineState:
        """Compute comprehensive statistics on processed documents."""
        processed = state["processed_docs"]
        failed = state["failed_docs"]
        stats = state["stats"]
        
        if not processed:
            stats["avg_quality_score"] = 0
            return state
        
        # Quality score distribution
        scores = [d["metadata"]["llm_quality_score"] for d in processed]
        stats["avg_quality_score"] = round(sum(scores) / len(scores), 2)
        stats["min_quality_score"] = min(scores)
        stats["max_quality_score"] = max(scores)
        
        # Score distribution histogram
        score_dist = {}
        for s in scores:
            bucket = f"{s}"
            score_dist[bucket] = score_dist.get(bucket, 0) + 1
        stats["quality_score_distribution"] = score_dist
        
        # Content type distribution
        content_types = {}
        for d in processed:
            ct = d["metadata"].get("content_type", "unknown")
            content_types[ct] = content_types.get(ct, 0) + 1
        stats["content_type_distribution"] = content_types
        
        # Source distribution
        sources = {}
        for d in processed:
            src = d["metadata"].get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
        stats["source_distribution"] = sources
        
        # Vendor distribution
        vendors = {}
        for d in processed:
            v = d["metadata"].get("vendor") or "unspecified"
            vendors[v] = vendors.get(v, 0) + 1
        stats["vendor_distribution"] = vendors
        
        # Technology frequency
        techs = {}
        for d in processed:
            for t in d["metadata"].get("technology", []):
                techs[t] = techs.get(t, 0) + 1
        stats["technology_distribution"] = dict(
            sorted(techs.items(), key=lambda x: -x[1])
        )
        
        # Status distribution
        statuses = {}
        for d in processed:
            s = d["metadata"].get("status", "unknown")
            statuses[s] = statuses.get(s, 0) + 1
        stats["status_distribution"] = statuses
        
        # Documents with syntax errors
        syntax_errors = sum(
            1 for d in processed 
            if d["metadata"].get("has_syntax_errors", False)
        )
        stats["docs_with_syntax_errors"] = syntax_errors
        
        # Documents with code blocks extracted
        with_code = sum(
            1 for d in processed 
            if d["metadata"].get("code_block")
        )
        stats["docs_with_code_blocks"] = with_code
        
        # Failure analysis
        failure_phases = {}
        for f in failed:
            phase = f.get("phase", "unknown")
            failure_phases[phase] = failure_phases.get(phase, 0) + 1
        stats["failure_by_phase"] = failure_phases
        
        return state
    
    # ── Node: Save Results ──
    async def save_results(state: PipelineState) -> PipelineState:
        """Save processed documents, failed docs, and stats to JSON files."""
        output_file = config.get("output_file", PipelineConfig.OUTPUT_FILE)
        failed_file = config.get("failed_file", PipelineConfig.FAILED_FILE)
        
        # Store output paths in stats
        state["stats"]["output_file"] = output_file
        state["stats"]["failed_file"] = failed_file
        
        # Ensure output directory exists
        out_dir = os.path.dirname(output_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        
        # Save processed documents
        logger.info(f"💾 Saving {len(state['processed_docs'])} processed docs...")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(state["processed_docs"], f, indent=2, ensure_ascii=False)
        out_size = os.path.getsize(output_file)
        out_size_str = f"{out_size / (1024*1024):.1f} MB" if out_size > 1024*1024 else f"{out_size / 1024:.1f} KB"
        logger.info(f"   ✓ {output_file} ({out_size_str})")
        
        # Save failed documents (if any)
        if state["failed_docs"]:
            logger.info(f"📋 Saving {len(state['failed_docs'])} failed docs...")
            with open(failed_file, "w", encoding="utf-8") as f:
                json.dump(state["failed_docs"], f, indent=2, ensure_ascii=False)
            fail_size = os.path.getsize(failed_file)
            fail_size_str = f"{fail_size / (1024*1024):.1f} MB" if fail_size > 1024*1024 else f"{fail_size / 1024:.1f} KB"
            logger.info(f"   ✓ {failed_file} ({fail_size_str})")
        
        # Save statistics separately
        stats_file = output_file.replace(".json", "_stats.json")
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(state["stats"], f, indent=2)
        stats_size = os.path.getsize(stats_file)
        logger.info(f"📊 Saved stats → {stats_file} ({stats_size} bytes)")
        
        return state
    
    # ── Node: Log Summary ──
    async def log_summary(state: PipelineState) -> PipelineState:
        """Print a formatted box summary of pipeline results."""
        stats = state["stats"]
        input_count = stats.get("input_count", 0)
        processed_count = stats.get("processed_count", 0)
        failed_count = stats.get("failed_count", 0)
        filtered_count = input_count - processed_count - failed_count
        
        # Calculate durations
        node_times = state.get("_node_times", {})
        total_time = sum(node_times.values()) if node_times else 0
        
        # Build bar chart helper
        def bar_pct(pct, width=20):
            filled = int(pct / 100 * width)
            return "█" * filled + "░" * (width - filled)
        
        # Build the box
        lines = []
        lines.append("")
        lines.append("╔" + "═" * 63 + "╗")
        lines.append("║" + " PIPELINE COMPLETE ".center(63) + "║")
        lines.append("╠" + "═" * 63 + "╣")
        
        # Timing
        lines.append("║" + f" Duration: {ProgressTracker._format_secs(total_time):<53} ║")
        if node_times:
            timing_parts = [f"{k}: {ProgressTracker._format_secs(v)}" for k, v in node_times.items()]
            timing_str = " | ".join(timing_parts)
            lines.append("║" + f" Nodes: {timing_str:<50} ║")
        
        lines.append("╠" + "═" * 63 + "╣")
        
        # Counts with bar chart
        pass_rate = (processed_count / input_count * 100) if input_count > 0 else 0
        fail_rate = (failed_count / input_count * 100) if input_count > 0 else 0
        filter_rate = (filtered_count / input_count * 100) if input_count > 0 else 0
        
        lines.append(f"║" + f" Input:  {input_count:>5} docs".ljust(63) + "║")
        lines.append(f"║" + f" Kept:   {processed_count:>5} ({pass_rate:5.1f}%)  {bar_pct(pass_rate)}".ljust(63) + "║")
        lines.append(f"║" + f" Failed: {failed_count:>5} ({fail_rate:5.1f}%)  {bar_pct(fail_rate)}".ljust(63) + "║")
        lines.append(f"║" + f" Skipped:  {filtered_count:>4} ({filter_rate:5.1f}%) {bar_pct(filter_rate)}".ljust(63) + "║")
        
        lines.append("╠" + "═" * 63 + "╣")
        
        # Quality scores
        avg_score = stats.get("avg_quality_score", 0)
        min_score = stats.get("min_quality_score", 0)
        max_score = stats.get("max_quality_score", 0)
        lines.append(f"║" + f" Avg Quality: {avg_score:.2f} / 10".ljust(63) + "║")
        lines.append(f"║" + f" Score Range: {min_score} – {max_score}".ljust(63) + "║")
        
        # Content types
        ct_dist = stats.get("content_type_distribution", {})
        if ct_dist:
            lines.append("╠" + "═" * 63 + "╣")
            lines.append("║" + " Content Types:".ljust(63) + "║")
            for ct, count in sorted(ct_dist.items(), key=lambda x: -x[1]):
                pct = (count / max(processed_count, 1)) * 100
                ct_label = ct[:16].ljust(16)
                bar = bar_pct(pct, 14)
                lines.append(f"║  {ct_label} {count:>4} ({pct:5.1f}%) {bar}".ljust(63) + "║")
        
        # Top technologies
        tech_dist = stats.get("technology_distribution", {})
        if tech_dist:
            lines.append("╠" + "═" * 63 + "╣")
            lines.append("║" + " Top Technologies:".ljust(63) + "║")
            for tech, count in list(tech_dist.items())[:8]:
                lines.append(f"║  {tech[:25].ljust(25)} {count:>4}".ljust(63) + "║")
        
        # Vendors
        vendor_dist = stats.get("vendor_distribution", {})
        if vendor_dist:
            lines.append("╠" + "═" * 63 + "╣")
            lines.append("║" + " Vendors:".ljust(63) + "║")
            for vendor, count in vendor_dist.items():
                if vendor != "unspecified":
                    lines.append(f"║  {vendor[:25].ljust(25)} {count:>4}".ljust(63) + "║")
        
        # Code blocks & syntax errors
        lines.append("╠" + "═" * 63 + "╣")
        lines.append(f"║" + f" ⚠️ Syntax errors: {stats.get('docs_with_syntax_errors', 0):>3} docs".ljust(63) + "║")
        lines.append(f"║" + f" 💻 Code blocks:   {stats.get('docs_with_code_blocks', 0):>3} docs".ljust(63) + "║")
        
        # Failures by phase
        failure_phases = stats.get("failure_by_phase", {})
        if failure_phases:
            lines.append("╠" + "═" * 63 + "╣")
            lines.append("║" + " Failures by Phase:".ljust(63) + "║")
            for phase, count in sorted(failure_phases.items(), key=lambda x: -x[1]):
                lines.append(f"║  {phase[:25].ljust(25)} {count:>4}".ljust(63) + "║")
        
        lines.append("╚" + "═" * 63 + "╝")
        lines.append("")
        
        for line in lines:
            logger.info(line)
        
        return state
    
    # ══════════════════════════════════════════════════════
    # ASSEMBLE GRAPH
    # ══════════════════════════════════════════════════════
    
    def with_timing(node_name, node_func):
        async def timed_node(state):
            t0 = time.perf_counter()
            result = await node_func(state)
            elapsed = time.perf_counter() - t0
            result["_node_times"][node_name] = elapsed
            return result
        return timed_node
    
    graph.add_node("load_documents", with_timing("load_documents", load_documents))
    graph.add_node("process_documents", with_timing("process_documents", process_documents))
    graph.add_node("compute_statistics", with_timing("compute_statistics", compute_statistics))
    graph.add_node("save_results", with_timing("save_results", save_results))
    graph.add_node("log_summary", with_timing("log_summary", log_summary))
    
    # Define edges
    graph.add_edge(START, "load_documents")
    graph.add_edge("load_documents", "process_documents")
    graph.add_edge("process_documents", "compute_statistics")
    graph.add_edge("compute_statistics", "save_results")
    graph.add_edge("save_results", "log_summary")
    graph.add_edge("log_summary", END)
    
    return graph.compile()


# ──────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────
async def run_pipeline(config: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the full data preparation pipeline with timing."""
    
    node_times: Dict[str, float] = {}
    start_all = time.perf_counter()
    
    initial_state: PipelineState = {
        "documents": [],
        "processed_docs": [],
        "failed_docs": [],
        "stats": {},
        "config": config,
        "_node_times": node_times,
    }
    
    graph = build_pipeline_graph(config)
    final_state = await graph.ainvoke(initial_state)
    
    node_times["total"] = time.perf_counter() - start_all
    final_state["_node_times"] = node_times
    
    return final_state


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="SDN/Network RAG Data Preparation — LLM Quality Pass",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using OpenAI API
  python prepare_data.py --input network_docs_raw.json

  # Using Ollama (local)
  python prepare_data.py \\
    --input network_docs_raw.json \\
    --base-url http://localhost:11434/v1 \\
    --api-key ollama \\
    --model llama3.1:8b

  # Using Together AI
  python prepare_data.py \\
    --input network_docs_raw.json \\
    --base-url https://api.together.xyz/v1 \\
    --api-key $TOGETHER_API_KEY \\
    --model meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo

  # Using vLLM local server
  python prepare_data.py \\
    --input network_docs_raw.json \\
    --base-url http://localhost:8000/v1 \\
    --api-key token-abc123 \\
    --model Qwen/Qwen2.5-7B-Instruct

  # Strict quality threshold
  python prepare_data.py \\
    --input network_docs_raw.json \\
    --min-quality-score 6
        """
    )
    
    # I/O Arguments
    parser.add_argument(
        "--input", "-i",
        default="network_docs_raw.json",
        help="Input JSON file from scraper (default: network_docs_raw.json)"
    )
    parser.add_argument(
        "--output", "-o",
        default="network_docs_prepared.json",
        help="Output JSON file for processed documents"
    )
    parser.add_argument(
        "--failed",
        default="network_docs_failed.json",
        help="Output JSON file for failed/filtered documents"
    )
    
    # LLM Arguments
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI-compatible API base URL"
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENAI_API_KEY", "sk-xxx"),
        help="API key for the LLM service"
    )
    parser.add_argument(
        "--model", "-m",
        default=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        help="LLM model name (e.g., gpt-4o-mini, llama3.1:8b)"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="LLM temperature (lower = more consistent, default: 0.1)"
    )
    
    # Processing Arguments
    parser.add_argument(
        "--min-quality-score",
        type=int,
        default=4,
        help="Minimum LLM quality score to keep (1-10, default: 4)"
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Maximum concurrent LLM API calls (default: 5)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="LLM request timeout in seconds (default: 120)"
    )
    parser.add_argument(
        "--max-text-length",
        type=int,
        default=4000,
        help="Max characters of text to send to LLM (default: 4000)"
    )
    
    # Utility Arguments
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print configuration and exit without processing"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging"
    )
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.verbose:
        _console_handler.setLevel(logging.INFO)
    else:
        _console_handler.setLevel(logging.CRITICAL + 1)  # disable console output
    
    # Build configuration dict
    config = {
        "input_file": args.input,
        "output_file": args.output,
        "failed_file": args.failed,
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "temperature": args.temperature,
        "min_quality_score": args.min_quality_score,
        "max_concurrent": args.max_concurrent,
        "timeout": args.timeout,
        "max_text_length": args.max_text_length,
    }
    
    # Dry run mode
    if args.dry_run:
        print("\n" + "=" * 65)
        print("DRY RUN — Configuration Preview")
        print("=" * 65)
        print(f"  Input file:          {config['input_file']}")
        print(f"  Output file:         {config['output_file']}")
        print(f"  Failed file:         {config['failed_file']}")
        print(f"  Base URL:            {config['base_url']}")
        print(f"  Model:               {config['model']}")
        print(f"  Temperature:         {config['temperature']}")
        print(f"  Min Quality Score:   {config['min_quality_score']}")
        print(f"  Max Concurrent:      {config['max_concurrent']}")
        print(f"  Timeout:             {config['timeout']}s")
        print(f"  Max Text Length:     {config['max_text_length']} chars")
        print("=" * 65)
        
        if os.path.exists(config["input_file"]):
            with open(config["input_file"]) as f:
                docs = json.load(f)
            print(f"\n  ✓ Input file found: {len(docs)} documents")
            
            # Show sample doc structure
            if docs:
                sample = docs[0]
                print(f"\n  Sample document structure:")
                print(f"    Keys: {list(sample.keys())}")
                if "metadata" in sample:
                    print(f"    Metadata keys: {list(sample['metadata'].keys())}")
        else:
            print(f"\n  ⚠️  Input file NOT found: {config['input_file']}")
        
        print()
        return
    
    # Update global config
    PipelineConfig.OPENAI_API_KEY = args.api_key
    PipelineConfig.OPENAI_BASE_URL = args.base_url
    PipelineConfig.LLM_MODEL = args.model
    PipelineConfig.LLM_TEMPERATURE = args.temperature
    PipelineConfig.MIN_QUALITY_SCORE = args.min_quality_score
    PipelineConfig.MAX_CONCURRENT_LLM_CALLS = args.max_concurrent
    PipelineConfig.LLM_REQUEST_TIMEOUT = args.timeout
    PipelineConfig.MAX_TEXT_LENGTH = args.max_text_length
    PipelineConfig.OUTPUT_FILE = args.output
    PipelineConfig.FAILED_FILE = args.failed
    
    # Run pipeline
    logger.info("🚀 Starting SDN/Network RAG Data Preparation Pipeline")
    logger.info(f"   Model: {args.model}")
    logger.info(f"   Endpoint: {args.base_url}")
    logger.info(f"   Min Quality Score: {args.min_quality_score}")
    logger.info("")
    
    result = asyncio.run(run_pipeline(config))
    
    # Exit with error if no documents were processed
    processed_count = result["stats"].get("processed_count", 0)
    if processed_count == 0:
        logger.error("❌ No documents were successfully processed!")
        logger.error("   Check the failed documents file for details.")
        exit(1)
    
    logger.info("")
    logger.info("✅ Pipeline completed successfully!")
    logger.info(f"   Ready for chunking/embedding: {result['stats']['output_file']}")


if __name__ == "__main__":
    main()