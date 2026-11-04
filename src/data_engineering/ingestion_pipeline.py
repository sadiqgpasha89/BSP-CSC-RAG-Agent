"""Continuous Ingestion & Layout-Aware Semantic Indexing Pipeline (Pipeline 1)."""

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class KnowledgeChunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    category: str
    content: str
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
    plan_code: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    is_legacy: bool = False
    keywords: List[str] = Field(default_factory=list)

    def matches(self, filters: Optional[Dict[str, Any]]) -> bool:
        """Evaluate deterministic pre-retrieval metadata filters against this chunk.

        A ``plan_code`` filter admits chunks pinned to that plan *and* plan-agnostic chunks (SOPs, device
        manuals, error-code references have ``plan_code=None``). Previously those were excluded outright, so
        grandfathered subscribers could never retrieve any troubleshooting knowledge.
        """
        if not filters:
            return True
        for key, expected in filters.items():
            actual = getattr(self, key, None)
            if actual is None:
                actual = self.raw_metadata.get(key)
            if key == "plan_code" and actual is None:
                continue  # plan-agnostic chunk
            if actual != expected:
                return False
        return True


class IngestionPipeline:
    """Ingests raw SOPs, tariffs, and technical manuals into normalized semantic chunks with strict metadata."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir) if data_dir else Path(settings.data_dir) / "knowledge_base"

    def load_and_chunk_all(self) -> List[KnowledgeChunk]:
        """Traverse knowledge base directory, parse JSON/documents and produce chunked embeddings candidates."""
        chunks: List[KnowledgeChunk] = []

        if not self.data_dir.exists():
            logger.warning("Knowledge base directory does not exist", path=str(self.data_dir))
            return chunks

        for file_path in sorted(self.data_dir.glob("*.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    docs = json.load(f)
                    if isinstance(docs, list):
                        for doc in docs:
                            chunks.extend(self._process_document(doc, file_path.name))
                    elif isinstance(docs, dict):
                        chunks.extend(self._process_document(docs, file_path.name))
            except Exception as e:
                logger.error("Failed to parse knowledge file", file=str(file_path), error=str(e))

        logger.info("Ingestion pipeline completed", total_chunks=len(chunks))
        return chunks

    def _process_document(self, doc: Dict[str, Any], source_filename: str) -> List[KnowledgeChunk]:
        """Convert a document into one or more semantically intact chunks with temporal & contractual metadata."""
        chunks: List[KnowledgeChunk] = []
        # Stable content hash: the builtin hash() is salted per process, which made chunk ids non-reproducible.
        fallback_id = "DOC-" + hashlib.sha1(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:8].upper()
        doc_id = doc.get("id", fallback_id)
        title = doc.get("title", doc.get("device_model", "Telecom Technical Reference"))
        category = doc.get("category", doc.get("topic", "general"))
        plan_code = doc.get("plan_code")
        valid_from = doc.get("valid_from")
        valid_to = doc.get("valid_to")
        is_legacy = doc.get("is_legacy", False)
        keywords = doc.get("keywords", [])

        # Format 1: Tariffs & Plan matrices
        if "data_allowance" in doc:
            content_parts = [
                f"Plan Name: {title} (Code: {plan_code})",
                f"Category: {category} | Legacy Grandfathered: {is_legacy}",
                f"Validity Period: {valid_from} to {valid_to}",
                f"Monthly Fee: ${doc.get('monthly_fee_usd', 0.00)} USD",
                f"Data Allowance: {doc.get('data_allowance')}",
                f"Tethering / Mobile Hotspot: {doc.get('tethering_hotspot', 'N/A')}",
                f"Roaming Details: {doc.get('roaming_included', doc.get('coverage_scope', 'N/A'))}",
            ]
            if "grandfathered_notes" in doc:
                content_parts.append(f"Important Contractual Notice: {doc['grandfathered_notes']}")
            if "cancellation_terms" in doc:
                content_parts.append(f"Cancellation Terms: {doc['cancellation_terms']}")

            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{doc_id}-CHK-01",
                    doc_id=doc_id,
                    title=title,
                    category=category,
                    content="\n".join(content_parts),
                    raw_metadata=doc,
                    plan_code=plan_code,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    is_legacy=is_legacy,
                    keywords=(keywords + [plan_code]) if plan_code else keywords,
                )
            )

        # Format 2: Network Diagnostic SOPs & Error codes
        elif "diagnostic_steps" in doc or "troubleshooting_steps" in doc:
            steps = doc.get("diagnostic_steps", doc.get("troubleshooting_steps", []))
            symptoms = doc.get("symptoms", "Operational line failure / degradation")
            rca = doc.get("root_cause_analysis", "Technical network discrepancy")

            content = (
                f"SOP: {title}\n"
                f"Topic: {category}\n"
                f"Keywords: {', '.join(keywords)}\n"
                f"Observed Symptoms: {symptoms}\n"
                f"Root Cause Analysis: {rca}\n"
                f"Standard Operating Troubleshooting Steps:\n" + "\n".join(str(step) for step in steps)
            )
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{doc_id}-CHK-01",
                    doc_id=doc_id,
                    title=title,
                    category=category,
                    content=content,
                    raw_metadata=doc,
                    plan_code=None,
                    is_legacy=False,
                    keywords=keywords,
                )
            )

        # Format 3: Device specifications and LED diagnostics
        elif "led_diagnostics" in doc:
            leds = doc.get("led_diagnostics", {})
            for led_name, states in leds.items():
                state_desc = [f"  * Status [{st}]: {desc}" for st, desc in states.items()]
                content = (
                    f"Device Hardware Manual: {title}\n"
                    f"Component Indicator: {led_name} LED\n"
                    f"Diagnostic States:\n" + "\n".join(state_desc)
                )
                chunks.append(
                    KnowledgeChunk(
                        chunk_id=f"{doc_id}-{led_name}",
                        doc_id=doc_id,
                        title=f"{title} - {led_name} Diagnostics",
                        category="hardware_led_diagnostics",
                        content=content,
                        raw_metadata={"device_model": title, "led": led_name, "states": states},
                        plan_code=None,
                        is_legacy=False,
                        keywords=[title, led_name, "LED", "Router", "Gateway"],
                    )
                )

        # Fallback / General JSON representation
        else:
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{doc_id}-GEN-01",
                    doc_id=doc_id,
                    title=title,
                    category=category,
                    content=json.dumps(doc, indent=2),
                    raw_metadata=doc,
                    plan_code=plan_code,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    is_legacy=is_legacy,
                    keywords=keywords,
                )
            )

        return chunks


ingestion_pipeline = IngestionPipeline()
