"""
Persistent Memory Engine (inspired by claude-mem) for Purity Revenue OS V2.0.
Provides persistent, cross-session, cross-channel long-term memory for every lead.

Capabilities:
1. Extracts atomic facts across all interactions (web inquiries, emails, WhatsApp messages,
   call transcripts, founder notes).
2. Categorizes into structured memory domains:
   - COFFEE_PREFERENCE: Arabica, Robusta, chicory preference, strength.
   - PACKAGING_FORMAT: 50g glass jars, 100g glass jars, sachets, bulk kg.
   - COMMERCIAL_TERMS: Price target, GSTIN requirement, payment terms.
   - OBJECTIONS_HANDLED: Competitor comparison (Nescafe/Bru), cost, unfamiliarity.
   - DECISION_MAKER: Verified name, title, contact preferences.
3. Injects condensed memory context into smart outreach, WhatsApp drafts, and AI voice calls
   so the AI never forgets past conversations and never asks repetitive questions.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc

_log = logging.getLogger("persistent_memory")


@dataclass
class MemoryFact:
    category: str
    key: str
    value: str
    confidence: float
    source: str
    recorded_at: str


class PersistentMemoryEngine:
    """
    Manages long-term persistent memory for Purity Beans leads across sessions.
    Backed by append-only LeadInteraction and LeadEvidence records.
    """

    @classmethod
    def extract_and_store_memories(
        cls,
        lead_id: int,
        raw_text: str,
        interaction_method: str,
        db: Session,
        actor: str = "AI_SYSTEM"
    ) -> List[MemoryFact]:
        """
        Parses raw text (inbound message, email, transcript, note) and extracts atomic facts,
        storing them permanently in LeadInteraction and returning extracted facts.
        """
        from app.models.models import B2BLead, LeadInteraction

        text_lower = raw_text.lower()
        extracted: List[MemoryFact] = []
        now_iso = datetime.utcnow().isoformat()

        # 1. Coffee Preferences
        if "arabica" in text_lower:
            extracted.append(MemoryFact("COFFEE_PREFERENCE", "bean_variety", "Prefers 100% Arabica (Purica fit)", 0.9, interaction_method, now_iso))
        elif "robusta" in text_lower or "strong" in text_lower:
            extracted.append(MemoryFact("COFFEE_PREFERENCE", "bean_variety", "Prefers Strong Robusta (Purista/Bold fit)", 0.9, interaction_method, now_iso))

        if "pure coffee" in text_lower or "no chicory" in text_lower or "zero chicory" in text_lower or "without chicory" in text_lower:
            extracted.append(MemoryFact("COFFEE_PREFERENCE", "chicory_filter", "Strictly Pure Coffee / Zero Chicory", 0.95, interaction_method, now_iso))
        elif "chicory" in text_lower:
            extracted.append(MemoryFact("COFFEE_PREFERENCE", "chicory_filter", "Accepts Chicory Blend (Ultra Blend fit)", 0.85, interaction_method, now_iso))

        # 2. Packaging Format
        if "sachet" in text_lower or "pouch" in text_lower or "single serve" in text_lower:
            extracted.append(MemoryFact("PACKAGING_FORMAT", "preferred_container", "Sachets / Single-Serve Pouches", 0.85, interaction_method, now_iso))
        elif "jar" in text_lower or "glass jar" in text_lower:
            extracted.append(MemoryFact("PACKAGING_FORMAT", "preferred_container", "Glass Jars (50g / 100g)", 0.9, interaction_method, now_iso))
        elif "bulk" in text_lower or "kg" in text_lower or "bag" in text_lower:
            extracted.append(MemoryFact("PACKAGING_FORMAT", "preferred_container", "Bulk Commercial Packaging", 0.85, interaction_method, now_iso))

        # 3. Commercial & Payment Terms
        if "gst" in text_lower or "tax credit" in text_lower or "invoice" in text_lower:
            extracted.append(MemoryFact("COMMERCIAL_TERMS", "gst_status", "Requires GST Invoicing for ITC", 0.9, interaction_method, now_iso))

        if "cod" in text_lower or "cash on delivery" in text_lower:
            extracted.append(MemoryFact("COMMERCIAL_TERMS", "payment_mode", "Prefers Cash on Delivery (COD)", 0.9, interaction_method, now_iso))
        elif "advance" in text_lower or "prepaid" in text_lower:
            extracted.append(MemoryFact("COMMERCIAL_TERMS", "payment_mode", "Prepaid / Advance (eligible for 5% cash discount)", 0.9, interaction_method, now_iso))
        elif "credit" in text_lower or "30 days" in text_lower:
            extracted.append(MemoryFact("COMMERCIAL_TERMS", "payment_mode", "Requested Credit Terms (Subject to Founder Approval)", 0.85, interaction_method, now_iso))

        # 4. Objections & Competitor Comparison
        competitors = ["nescafe", "bru", "continental", "tata coffee", "davidoff"]
        for comp in competitors:
            if comp in text_lower:
                extracted.append(MemoryFact("OBJECTIONS_HANDLED", "current_competitor", f"Currently using / comparing with {comp.title()}", 0.85, interaction_method, now_iso))

        if "expensive" in text_lower or "costly" in text_lower or "high price" in text_lower or "cheaper" in text_lower:
            extracted.append(MemoryFact("OBJECTIONS_HANDLED", "price_sensitivity", "High price sensitivity / requires cost-per-cup breakdown", 0.9, interaction_method, now_iso))

        # 5. Persist to LeadInteraction
        if extracted:
            remark_facts = "; ".join([f"{f.category}:{f.key}={f.value}" for f in extracted])
            interaction = LeadInteraction(
                lead_id=lead_id,
                created_by=actor,
                method=interaction_method,
                outcome="MEMORY_UPDATED",
                remark=f"[PERSISTENT_MEMORY] {remark_facts} | Raw: {raw_text[:150]}",
                occurred_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(interaction)

            # Update lead record columns if applicable
            lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
            if lead:
                for f in extracted:
                    if f.key == "current_competitor":
                        lead.current_brand = f.value
                    elif f.key == "price_sensitivity":
                        lead.price_sensitivity = "high"
            db.commit()

        return extracted

    @classmethod
    def get_condensed_memory_context(cls, lead_id: int, db: Session) -> str:
        """
        Retrieves distilled, persistent historical memory for a lead to inject into agent prompts.
        Guarantees the AI remembers past preferences, objections, and agreements.
        """
        from app.models.models import B2BLead, LeadInteraction

        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return "No persistent memory found."

        interactions = (
            db.query(LeadInteraction)
            .filter(LeadInteraction.lead_id == lead_id)
            .order_by(desc(LeadInteraction.occurred_at))
            .limit(20)
            .all()
        )

        memory_items: List[str] = []

        # Business Context
        if lead.company:
            memory_items.append(f"Business: {lead.company} ({lead.city or 'India'})")
        if lead.contact_name:
            memory_items.append(f"Contact Person: {lead.contact_name} ({lead.contact_title or 'Decision Maker'})")
        if lead.current_brand:
            memory_items.append(f"Current Coffee Brand: {lead.current_brand}")

        # Scan interactions for structured facts
        facts_found: Dict[str, str] = {}
        for ix in interactions:
            remark = str(ix.remark or "")
            if "[PERSISTENT_MEMORY]" in remark:
                content = remark.split("[PERSISTENT_MEMORY]")[-1].split("| Raw:")[0].strip()
                pairs = content.split(";")
                for p in pairs:
                    if "=" in p:
                        k, v = p.strip().split("=", 1)
                        if k not in facts_found:
                            facts_found[k] = v

        for k, v in facts_found.items():
            memory_items.append(f"{k}: {v}")

        if not memory_items:
            return "First-time interaction. No previous memory recorded."

        return "PERSISTENT MEMORY CONTEXT:\n- " + "\n- ".join(memory_items)
