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
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Literal
from enum import Enum

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

# LangChain imports
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# LangGraph imports
from langgraph.graph import StateGraph, START, END

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


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
    MAX_CONCURRENT_LLM_CALLS: int = 5
    LLM_REQUEST_TIMEOUT: int = 60
    LLM_RETRY_ATTEMPTS: int = 3
    MAX_TEXT_LENGTH: int = 6000  # Truncate text for LLM to avoid token limits
    
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
    code_block: Optional[str] = Field(
        default=None,
        description="Extract the primary code/config block if present, verbatim"
    )
    has_syntax_errors: bool = Field(
        default=False,
        description="True if code blocks contain obvious syntax errors"
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
        #temperature=temperature or PipelineConfig.LLM_TEMPERATURE,
        timeout=timeout or PipelineConfig.LLM_REQUEST_TIMEOUT,
        max_retries=PipelineConfig.LLM_RETRY_ATTEMPTS,
    )


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
    
    # For StackExchange Q&A: verify answer content is present
    if metadata.get("source_type") == "stackexchange_qa":
        is_answered = metadata.get("se_is_answered", False)
        answer_count = metadata.get("se_answer_count", 0)
        
        # Check for answer indicators in text
        answer_indicators = [
            "### Top Answers:", "✓", "#1 [Score:", "#2 [Score:", "#3 [Score:",
            "Accepted Answer", "Best Answer", "---\n\n",
            "Answer:", "answer is", "solution is", "you should"
        ]
        has_answer = any(indicator in text for indicator in answer_indicators)
        
        # Flag if marked as answered but no answer text found
        if is_answered and not has_answer and answer_count == 0:
            return False, "StackExchange Q&A marked answered but no answer text found"
        
        # Flag if appears to be question-only with minimal content
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
    
    logger.info(f"📝 [{doc_idx}] Processing: {doc_title[:55]}...")
    
    # ══════════════════════════════════════════════════════
    # PHASE 1: VALIDATION
    # ══════════════════════════════════════════════════════
    passed, reason = validate_document_integrity(doc)
    if not passed:
        logger.warning(f"  ❌ Validation failed: {reason}")
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
    
    logger.info(
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
    
    logger.info(
        f"  🏷️ Type: {meta_extract.content_type} | "
        f"Vendor: {meta_extract.vendor or 'N/A'} | "
        f"Tech: {', '.join(meta_extract.technology[:3]) or 'N/A'}"
    )
    
    # ══════════════════════════════════════════════════════
    # BUILD FINAL DOCUMENT
    # ══════════════════════════════════════════════════════
    final_metadata = normalized_metadata.copy()
    
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
        "has_syntax_errors": meta_extract.has_syntax_errors,
        "status": "needs_review" if quality.action == QualityAction.ENRICH else "verified",
    })
    
    # Add version tag if detected
    if quality.version_tag:
        final_metadata["version_tag"] = quality.version_tag
    
    # Add code block if extracted
    if meta_extract.code_block:
        final_metadata["code_block"] = meta_extract.code_block
    
    # Determine final text (use enriched if available, else original)
    if quality.action == QualityAction.ENRICH and quality.enriched_text:
        final_text = quality.enriched_text
        final_metadata["text_was_enriched"] = True
    else:
        # Prefer existing enriched text from scraper, else raw text
        final_text = normalized_doc.get("text_enriched") or normalized_doc.get("text")
        final_metadata["text_was_enriched"] = False
    
    # Construct final document
    final_doc = {
        "text": final_text,
        "metadata": final_metadata,
    }
    
    # Preserve code snippets from scraper if present
    if normalized_doc.get("code_snippets"):
        final_doc["code_snippets"] = normalized_doc["code_snippets"]
    
    logger.info(f"  ✅ Finalized: status={final_metadata['status']}")
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
        
        with open(input_file, "r", encoding="utf-8") as f:
            docs = json.load(f)
        
        logger.info(f"   ✓ Loaded {len(docs)} raw documents")
        state["documents"] = docs
        state["stats"]["input_count"] = len(docs)
        state["stats"]["input_file"] = input_file
        
        return state
    
    # ── Node: Process All Documents ──
    async def process_documents(state: PipelineState) -> PipelineState:
        """Process all documents through validation → quality → enrichment pipeline."""
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
        
        # Semaphore for rate limiting concurrent LLM calls
        max_concurrent = config.get("max_concurrent", PipelineConfig.MAX_CONCURRENT_LLM_CALLS)
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_with_limit(doc: Dict, idx: int):
            async with semaphore:
                return await process_single_document(
                    doc=doc,
                    llm=llm,
                    doc_idx=idx,
                    min_quality_score=config.get("min_quality_score")
                )
        
        # Execute all document processing concurrently (with semaphore limit)
        logger.info(f"🔄 Processing {len(docs)} documents (max {max_concurrent} concurrent)...")
        
        tasks = [process_with_limit(doc, idx) for idx, doc in enumerate(docs)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect results
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"  💥 Exception processing doc {idx}: {result}")
                failed.append({
                    "original_doc": docs[idx],
                    "processing_error": f"EXCEPTION: {str(result)}",
                    "phase": "unknown",
                    "content_hash": docs[idx].get("metadata", {}).get("content_hash", f"doc_{idx}"),
                })
            else:
                final_doc, failed_doc = result
                if final_doc:
                    processed.append(final_doc)
                if failed_doc:
                    failed.append(failed_doc)
        
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
        
        # Ensure output directory exists
        out_dir = os.path.dirname(output_file)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        
        # Save processed documents
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(state["processed_docs"], f, indent=2, ensure_ascii=False)
        logger.info(f"💾 Saved {len(state['processed_docs'])} processed docs → {output_file}")
        
        # Save failed documents (if any)
        if state["failed_docs"]:
            with open(failed_file, "w", encoding="utf-8") as f:
                json.dump(state["failed_docs"], f, indent=2, ensure_ascii=False)
            logger.info(f"📋 Saved {len(state['failed_docs'])} failed docs → {failed_file}")
        
        # Save statistics separately
        stats_file = output_file.replace(".json", "_stats.json")
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(state["stats"], f, indent=2)
        logger.info(f"📊 Saved stats → {stats_file}")
        
        return state
    
    # ── Node: Log Summary ──
    async def log_summary(state: PipelineState) -> PipelineState:
        """Print a formatted summary of pipeline results."""
        stats = state["stats"]
        
        separator = "=" * 65
        logger.info(separator)
        logger.info("PIPELINE COMPLETE — SUMMARY")
        logger.info(separator)
        
        logger.info(f"  📥 Input documents:      {stats.get('input_count', 0)}")
        logger.info(f"  ✅ Processed (kept):     {stats.get('processed_count', 0)}")
        logger.info(f"  ❌ Failed/Filtered:      {stats.get('failed_count', 0)}")
        
        if stats.get("input_count", 0) > 0:
            pass_rate = (stats.get("processed_count", 0) / stats["input_count"]) * 100
            logger.info(f"  📈 Pass rate:            {pass_rate:.1f}%")
        
        logger.info("")
        logger.info(f"  🎯 Avg Quality Score:    {stats.get('avg_quality_score', 0):.2f}")
        logger.info(f"  📊 Score Range:          {stats.get('min_quality_score', 0)} – {stats.get('max_quality_score', 0)}")
        
        logger.info("")
        logger.info("  📑 Content Types:")
        for ct, count in stats.get("content_type_distribution", {}).items():
            pct = (count / max(stats.get("processed_count", 1), 1)) * 100
            logger.info(f"      {ct:<18} {count:>4} ({pct:>5.1f}%)")
        
        logger.info("")
        logger.info("  🌐 Sources:")
        for src, count in stats.get("source_distribution", {}).items():
            logger.info(f"      {src:<35} {count:>4}")
        
        logger.info("")
        logger.info("  🔧 Top Technologies:")
        for tech, count in list(stats.get("technology_distribution", {}).items())[:8]:
            logger.info(f"      {tech:<25} {count:>4}")
        
        if stats.get("vendor_distribution"):
            logger.info("")
            logger.info("  🏢 Vendors:")
            for vendor, count in stats.get("vendor_distribution", {}).items():
                if vendor != "unspecified":
                    logger.info(f"      {vendor:<25} {count:>4}")
        
        logger.info("")
        logger.info(f"  ⚠️  Docs with syntax errors: {stats.get('docs_with_syntax_errors', 0)}")
        logger.info(f"  💻 Docs with code blocks:    {stats.get('docs_with_code_blocks', 0)}")
        
        if stats.get("failure_by_phase"):
            logger.info("")
            logger.info("  🚫 Failures by Phase:")
            for phase, count in stats.get("failure_by_phase", {}).items():
                logger.info(f"      {phase:<25} {count:>4}")
        
        logger.info(separator)
        
        return state
    
    # ══════════════════════════════════════════════════════
    # ASSEMBLE GRAPH
    # ══════════════════════════════════════════════════════
    graph.add_node("load_documents", load_documents)
    graph.add_node("process_documents", process_documents)
    graph.add_node("compute_statistics", compute_statistics)
    graph.add_node("save_results", save_results)
    graph.add_node("log_summary", log_summary)
    
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
    """Execute the full data preparation pipeline."""
    
    initial_state: PipelineState = {
        "documents": [],
        "processed_docs": [],
        "failed_docs": [],
        "stats": {},
        "config": config,
    }
    
    graph = build_pipeline_graph(config)
    final_state = await graph.ainvoke(initial_state)
    
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
        default=60,
        help="LLM request timeout in seconds (default: 60)"
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
        logging.getLogger().setLevel(logging.DEBUG)
    
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