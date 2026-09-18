"""
Category-aware outreach drafting.

WHY THIS EXISTS
---------------
"Balaji's Mart" is a kirana in Abohar. It was emailed:

    We understand Balaji's Mart manages pantry services for a significant
    number of employees across its offices.

Not a translation error — a silent fallback. email_sender did

    _SEGMENT_CONTEXT.get(div, _SEGMENT_CONTEXT["corporate"])

and _SEGMENT_CONTEXT is keyed on corporate / horeca / distributor / wholesale /
retail / government / gifting while classify_business() actually emits grocery,
kirana_store, hotel_canteen, wholesaler_agglo, corporate_office, education_mess,
hospital and retail_chain. Almost nothing matched, so almost every draft became
the corporate pantry pitch — offered to grocers, hotels and wholesalers alike.

So: every division the classifier can emit is mapped here explicitly, and an
unmapped one raises instead of quietly picking a pitch. A wrong pitch that sends
is worse than a draft that refuses to generate.

PRODUCTS
--------
Taken from the Purity Beans Product Catalogue 2026: four blends, each in 50 g
and 100 g, every one in a food-grade glass jar. Nothing outside that table is
offered — a pitch naming a pack we cannot ship is a promise the founder has to
break. There is no bulk or sachet SKU, so hospitality and pantry buyers are
shown the 100 g jar rather than a "bulk pack" that does not exist.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# The real catalogue — Purity Beans Product Catalogue 2026, "The Full Range at a
# Glance". Four blends, two pack sizes each, every one in a food-grade glass jar.
# Prices here are MRP from that table.
#
# This is the ONLY price list. MRP is uniform for a variant across every channel,
# confirmed by the founder — a retailer, a distributor and a hotel all see the
# same MRP for Bold 50 g. The contradicting "institutional" table that used to
# live in email_sender has been removed rather than reconciled.
#
# Uniform MRP is a selling point to anyone who resells: their margin cannot be
# undercut by a cheaper channel, so it is stated in the reseller pitches rather
# than left as an internal detail.
@dataclass(frozen=True)
class Sku:
    name: str
    kind: str          # what it is, in the catalogue's own words
    process: str
    mrp_50: int
    mrp_100: int

    def line(self, pack: str = "both") -> str:
        if pack == "100":
            return f"{self.name} ({self.kind}, {self.process}) — 100 g glass jar, MRP Rs {self.mrp_100}"
        if pack == "50":
            return f"{self.name} ({self.kind}, {self.process}) — 50 g glass jar, MRP Rs {self.mrp_50}"
        return (f"{self.name} ({self.kind}, {self.process}) — 50 g Rs {self.mrp_50} / "
                f"100 g Rs {self.mrp_100}, glass jar")


SKUS = {
    "ultra_blend": Sku("Ultra Blend", "signature", "agglomerated", 209, 309),
    "bold":        Sku("Bold", "100% pure coffee", "agglomerated", 239, 369),
    "purista":     Sku("Purista", "robusta", "freeze-dried", 319, 509),
    "purica":      Sku("Purica", "arabica", "freeze-dried", 329, 559),
}

# Verified claims from the catalogue, usable in any draft.
CLAIMS = {
    "no_chicory": "Most Indian instant coffee carries 40–60% chicory filler. Ours is only coffee.",
    "glass": "Sealed in a lead-free, food-grade glass jar — recyclable, not plastic.",
    "shelf": "Two-year shelf life.",
    "dissolve": "Dissolves in about 30 seconds in hot, cold or milk.",
}


@dataclass
class Pitch:
    label: str                      # what the founder sees this category called
    context: str                    # one line about THEIR business, uses {company}
    focus: list[str]                # bullets — the value proposition
    skus: list[str]                 # which SKUs to lead with
    cta: str
    pack: str = "both"              # "50" | "100" | "both" — what suits this buyer
    forbid: list[str] = field(default_factory=list)   # phrases wrong for this category


# One entry per division classify_business() can emit. No defaults.
PITCHES: dict[str, Pitch] = {
    "distributor": Pitch(
        "Distributor",
        "We understand {company} manages distribution channels where FMCG margins are key.",
        ["FMCG trade margins", "Uniform MRP across channels", "No supply lag"],
        ["purista", "ultra_blend"],
        "Shall I send pricing?",
        forbid=["employee pantry", "retail shelf"]),
    "wholesaler": Pitch(
        "Wholesaler",
        "We understand {company} handles wholesale trade where volume and margin protect the deal.",
        ["Bulk trading margins", "Uniform pricing to protect wholesale", "No supply lag"],
        ["purista", "ultra_blend"],
        "Shall I send pricing?",
        forbid=["employee pantry", "retail shelf"]),
    "modern_trade": Pitch(
        "Modern Trade",
        "We understand {company} manages high-visibility retail premises where premium assortments and reliable stock levels drive customer loyalty.",
        ["Attractive margins on premium freeze-dried glass jars", "Uniform MRP across all channels protects your retail price", "Clean label: 100% coffee, zero chicory", "Eye-catching packaging designed for premium shelf appeal"],
        ["purica", "purista", "bold"],
        "Shall we share our commercial catalog for modern retail channels?",
        forbid=["employee pantry"]),
    "supermarket": Pitch(
        "Supermarket",
        "We understand {company} operates premium supermarket operations with high FMCG product rotation.",
        ["Attractive margins on premium freeze-dried glass jars", "Uniform MRP across all channels protects your retail price", "Clean label: 100% coffee, zero chicory", "Eye-catching packaging designed for premium shelf appeal"],
        ["purica", "purista", "bold"],
        "Shall we share our commercial catalog for modern retail channels?",
        forbid=["employee pantry"]),
    "grocery_chain": Pitch(
        "Grocery Chain",
        "We understand {company} manages grocery chains where high retail shelf velocity is key.",
        ["Attractive margins on premium freeze-dried glass jars", "Uniform MRP across all channels protects your retail price", "Clean label: 100% coffee, zero chicory", "Eye-catching packaging designed for premium shelf appeal"],
        ["purica", "purista", "bold"],
        "Shall we share our commercial catalog for modern retail channels?",
        forbid=["employee pantry"]),
    "retail_kirana": Pitch(
        "Kirana Store",
        "We understand {company} is a trusted neighborhood grocery store in {city}.",
        ["Popular freeze-dried blend", "Attractive retail margins", "No slow stock"],
        ["ultra_blend"],
        "Shall I send pricing?",
        forbid=["employee pantry", "corporate pantry"]),
    "corporate_office": Pitch(
        "Corporate Office",
        "We understand {company} provides workspace facilities where quality coffee drives employee energy.",
        ["Direct contract pricing", "Pantry dispensing optimization", "Reliable delivery"],
        ["bold", "ultra_blend"],
        "Shall I send sample kits?",
        forbid=["retail shelf", "trade margins"]),
    "office_pantry": Pitch(
        "Office Pantry",
        "We understand {company} coordinates workplace refreshment solutions where consistent quality and simple procurement keep employees satisfied.",
        ["Direct institutional pricing for office volumes", "Scheduled dispatch so your pantry is never out of stock", "100% pure coffee — zero chicory filler for a clean taste"],
        ["purica", "purista", "bold"],
        "Would you like office pantry sample kits for your admin team to try?",
        forbid=["retail shelf", "trade margins"]),
    "manufacturing": Pitch(
        "Manufacturing",
        "We understand {company} operates manufacturing facilities where canteen/staff pantry refreshments keep the workforce productive.",
        ["Direct contract supply with GST invoicing", "100% pure coffee, zero chicory", "Pace and delivery commitments you can rely on"],
        ["bold", "ultra_blend"],
        "Shall I send volume pricing for canteen procurement?",
        forbid=["retail shelf", "trade margins"]),
    "facility_management": Pitch(
        "Facility Management",
        "We understand {company} provides client facility management services where pantry and refreshment operations require consistent partners.",
        ["Aggregated contract rates for multi-site supply", "Reliable logistics and delivery tracking", "High-quality pure coffee to elevate client canteens"],
        ["purista", "bold"],
        "Shall we set up a brief call to discuss corporate client terms?",
        forbid=["retail shelf", "premium glass"]),
    "hotel": Pitch(
        "Hotel",
        "We understand {company} runs premium hospitality spaces where beverage quality matters.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["ultra_blend", "purista"],
        "Shall I send the range?",
        forbid=["retail shelf", "employee pantry"]),
    "restaurant": Pitch(
        "Restaurant",
        "We understand {company} operates premium food service venues.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["ultra_blend", "purista"],
        "Shall I send the range?",
        forbid=["retail shelf", "employee pantry"]),
    "cafe": Pitch(
        "Cafe",
        "We understand {company} operates cafe operations where taste consistency is key.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["ultra_blend", "purista"],
        "Would you like to try a sample before deciding anything?",
        forbid=["retail shelf", "employee pantry"]),
    "hospital": Pitch(
        "Hospital",
        "We understand {company} coordinates canteen refreshments for staff and patients.",
        ["Bulk institutional rates", "100% pure coffee, zero chicory", "Consistent dispatch"],
        ["bold", "ultra_blend"],
        "Shall I send pricing?",
        forbid=["retail shelf", "premium glass"]),
    "school": Pitch(
        "School",
        "We understand {company} manages student canteens and school dining.",
        ["Bulk institutional rates", "Quick cup preparation", "Reliable dispatch"],
        ["bold", "ultra_blend"],
        "Shall I send pricing?",
        forbid=["retail shelf", "premium glass"]),
    "college": Pitch(
        "College",
        "We understand {company} manages college canteens and student hostels.",
        ["Bulk institutional rates", "Quick cup preparation", "Reliable dispatch"],
        ["bold", "ultra_blend"],
        "Shall I send pricing?",
        forbid=["retail shelf", "premium glass"]),
    "government": Pitch(
        "Government Office",
        "We understand {company} runs staff canteens.",
        ["Bulk institutional rates", "Consistent cup profile", "GST billing"],
        ["bold"],
        "Shall I send pricing?",
        forbid=["retail shelf", "premium glass"]),
    "corporate_gifting": Pitch(
        "Corporate Gifting",
        "We understand {company} curates gifts where product quality is the point.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["ultra_blend", "purista"],
        "Shall I send the range?",
        forbid=["employee pantry", "shelf space"]),
    "private_label": Pitch(
        "Private Label",
        "We understand {company} curates customized brand selections for custom-labeled products.",
        ["Custom blending and contract packaging option", "Food-grade glass jar supply", "Strict quality parameters and certificates on demand"],
        ["purica", "purista"],
        "Can we share a technical specification sheet for private labeling?",
        forbid=["employee pantry", "shelf space"]),
    "exporter": Pitch(
        "Exporter",
        "We understand {company} manages export shipments of premium agricultural and packaged goods.",
        ["Premium freeze-dried coffee suited for international export", "Lead-free glass jar packaging with 2-year shelf life", "FOB/CIF terms with documented specification"],
        ["purica", "purista"],
        "Shall we provide catalog details and export quotation terms?",
        forbid=["employee pantry", "retail shelf"]),
    "institutional_buyer": Pitch(
        "Institutional Buyer",
        "We understand {company} procures volume consumables against structured buyer specifications.",
        ["GST invoicing with competitive bulk pricing", "100% pure coffee without chicory filler", "Consistent delivery against procurement contracts"],
        ["bold", "ultra_blend"],
        "Can we register as a vendor and send a quote against your monthly requirements?",
        forbid=["retail shelf", "premium glass"]),
    "needs_reclassification": Pitch(
        "General Wholesale",
        "We understand {company} values quality beverages and reliable partners.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["bold", "purica"],
        "Shall I send some information regarding our wholesale range?",
        forbid=["employee pantry", "shelf space"]),
    "unknown": Pitch(
        "General Wholesale",
        "We understand {company} values quality beverages and reliable partners.",
        ["Premium freeze-dried coffee", "100% coffee, no chicory", "Volume pricing"],
        ["bold", "purica"],
        "Shall I send some information regarding our wholesale range?",
        forbid=["employee pantry", "shelf space"]),
}


class UnmappedCategory(Exception):
    """Raised rather than guessing a pitch. See module docstring."""


def pitch_for(division: str) -> Pitch:
    key = (division or "").lower().strip()
    
    # Map legacy categories to their V2 pitch equivalents
    legacy_map = {
        "grocery": "retail_kirana",
        "kirana_store": "retail_kirana",
        "retail": "retail_kirana",
        "retail_chain": "grocery_chain",
        "hotel_canteen": "hotel",
        "wholesaler_agglo": "wholesaler",
        "gifting": "corporate_gifting",
        "corporate": "corporate_office",
        "tender": "institutional_buyer",
        "horeca": "needs_reclassification",
        "education_mess": "needs_reclassification"
    }
    if key in legacy_map:
        key = legacy_map[key]
        
    if key not in PITCHES:
        raise UnmappedCategory(
            f"no pitch defined for category '{division}' — add one to PITCHES rather than "
            f"letting it fall back, which is how grocers were sent the pantry pitch")
    return PITCHES[key]


# ── Stage: what kind of message is this? ─────────────────────────────────────

STAGES = ("intro", "reply", "followup_1", "followup_2", "founder_note")


def choose_stage(sent_count: int, replied: bool, days_since_last: int | None) -> str:
    """
    Never introduce ourselves to someone we have already written to. That was
    the other half of the complaint: a fresh intro regenerated over an existing
    conversation.
    """
    if replied:
        return "reply"
    if sent_count <= 0:
        return "intro"
    if days_since_last is None:
        return "followup_1"
    if days_since_last < 3:
        return "followup_1"      # too soon to escalate, but not an intro
    if days_since_last < 7:
        return "followup_1"
    if days_since_last < 21:
        return "followup_2"
    return "founder_note"


# A different angle each time. Repeating the first email louder is not a
# follow-up, and the founder asked specifically that these not repeat.
_ANGLE = {
    "followup_1": "Since coffee is bought again every few weeks, it tends to be judged on "
                  "repeat demand rather than a single order.",
    "followup_2": "If it helps, we can start with a sample so nothing is committed before "
                  "you have tasted it.",
    "founder_note": "I will stop writing after this — if it is ever useful, my number is below.",
}


def build_draft(lead, memory: dict | None, sent_count: int, replied: bool,
                days_since_last: int | None, signature: str,
                rel: dict | None = None) -> dict:
    """
    Returns subject, body, and the reasoning the founder reviews before approving.
    Everything referenced is either a real SKU or a fact stored in Business Memory.
    """
    mem = memory or {}
    p = pitch_for(getattr(lead, "division", "") or "")
    stage = choose_stage(sent_count, replied, days_since_last)
    # Customer-facing name, never the raw directory string. Nine subject
    # templates across four modules interpolate this, and one produced
    # "A sample for WrkPod | Coworking Space in Coimbatore | Shared Office
    # Space?" as a live subject. Cleaning at each derivation keeps the raw
    # value on the record for provenance while nothing customer-facing sees it.
    from app.services.lead_quality import display_name
    company = display_name(lead.company or "")

    used = []
    dm = (mem.get("decision_maker") or "").strip()
    greet = f"Dear {dm}," if dm else "Dear Sir/Madam,"
    if dm:
        used.append(f"decision maker: {dm}")

    lines = [greet, ""]
    # A prior contact on ANY channel means this is a continuation, not an
    # opening. Introducing ourselves to someone who has already spoken to the
    # founder is the specific failure this guards against.
    cont = continuity_line(rel or {}, mem)
    
    if stage == "intro":
        city_part = f" in {lead.city}" if lead.city else ""
        div = (getattr(lead, "division", "") or "").lower().strip()
        
        is_verified = div not in ("", "unknown", "needs_reclassification")
        
        if is_verified:
            if div in ("distributor", "wholesaler"):
                why_line = f"I came across {company}{city_part} while researching food distribution businesses in your region."
                prop_line = "We are exploring distribution opportunities for Purity Beans and wanted to see whether the range could be relevant to your network."
                cta = "Would you be open to reviewing our catalogue and distributor pricing?"
            elif div in ("retail_kirana", "retail"):
                why_line = f"I came across {company}{city_part} while researching retail stores in your region."
                prop_line = "We are expanding retail availability of Purity Beans and wanted to explore whether the range could be relevant for your store."
                cta = "May I share our catalogue and retailer pricing?"
            elif div in ("supermarket", "grocery_chain", "modern_trade"):
                why_line = f"I came across {company}{city_part} while researching modern trade and supermarket outlets in your region."
                prop_line = "We are expanding the retail presence of Purity Beans and would like to explore a potential listing with your stores."
                cta = "Would it be useful if I shared our product catalogue and trade terms?"
            elif div in ("corporate_office", "office_pantry", "manufacturing", "facility_management", "hospital", "school", "college", "government", "institutional_buyer"):
                why_line = f"I came across {company}{city_part} while researching organizations and workspace facility networks in your region."
                prop_line = "We supply instant coffee for workplace and pantry requirements and wanted to understand whether this may be relevant to your organisation."
                cta = "May I share our institutional range for consideration?"
            elif div in ("hotel", "restaurant", "cafe", "horeca"):
                why_line = f"I came across {company}{city_part} while researching hospitality and F&B venues in your region."
                prop_line = "We wanted to explore whether Purity Beans could be relevant to your instant coffee requirements."
                cta = "Would you be open to reviewing our range and trade pricing?"
            else:
                why_line = f"I came across {company}{city_part} while researching businesses in your region."
                prop_line = "We are reaching out to explore potential opportunities to work with your organization."
                cta = "Would you be open to reviewing our catalogue and trade pricing?"
        else:
            why_line = f"I came across {company}{city_part} while researching businesses in your region that may have a potential requirement for our instant coffee range."
            prop_line = "We are reaching out to explore whether our instant coffee portfolio is relevant to your operations."
            cta = "Would you be open to reviewing our catalogue and trade pricing?"

        body_lines = [
            greet,
            "",
            "I hope you are doing well.",
            "",
            f"{why_line} {prop_line}",
            "",
            "Purity Beans is a premium instant coffee brand of Pure Pantry Provisions. We offer a portfolio of premium freeze-dried and agglomerated instant coffees, all crafted with 100% purity — zero chicory, and zero artificial additives.",
            "",
            "We're FSSAI Licensed, MSME Registered, GST Compliant, with reliable PAN-India supply.",
            "",
            cta,
            "",
            signature
        ]
        body = "\n".join(body_lines)
    else:
        if cont:
            lines += [cont, ""]
            used.append(f"prior contact via {(rel or {}).get('last_channel')}")
        elif stage == "reply":
            lines += [f"Thank you for coming back to me about {company}.", ""]
        else:
            lines += [f"Following up on my note about coffee supply for {company}.", ""]

        supplier = (mem.get("current_supplier") or "").strip()
        if supplier:
            lines += [f"You mentioned you currently buy {supplier} — we would simply like to give "
                      f"you something to compare it against, on quality and on price.", ""]
            used.append(f"current supplier: {supplier}")

        # Search for customer requirement keywords in memory/interactions to progressively personalize
        all_text = " ".join([str(v) for v in mem.values()])
        if rel and rel.get("last_outcome"):
            all_text += " " + rel["last_outcome"]
        all_text_lower = all_text.lower()
        
        requirement_text = ""
        if "economical" in all_text_lower or "budget" in all_text_lower:
            requirement_text = "Regarding your interest in our more economical options, our Ultra Blend and Bold agglomerated coffees offer excellent quality at a highly competitive price point."
        elif "freeze-dried" in all_text_lower or "premium" in all_text_lower or "arabica" in all_text_lower or "robusta" in all_text_lower:
            requirement_text = "Regarding your interest in premium freeze-dried coffees, our Purica (100% Arabica) and Purista (100% Robusta) offer a premium cup profile comparable to fresh brew."
        elif "100g" in all_text_lower or "100 g" in all_text_lower:
            requirement_text = "Regarding your requirement for 100 g pack sizes, our full range (Purica, Purista, Bold, Ultra Blend) is available in lead-free 100 g glass jars."
        elif "margin" in all_text_lower or "commission" in all_text_lower or "distributor margin" in all_text_lower:
            requirement_text = "Regarding your question about trade margins, we offer distributors a competitive margin of 35% to 42% on MRP, depending on volume commitments."
        elif "sample" in all_text_lower or "try" in all_text_lower:
            requirement_text = "We would be happy to arrange a complimentary sample kit of our range for your evaluation. Please let us know the delivery address and contact person."

        if requirement_text:
            lines += [requirement_text, ""]
            used.append("progressive personalization: requirement matched")

        lines += ["I am writing from Pure Pantry Provisions about Purity Beans instant coffee.", ""]
        lines += [b if b.startswith("•") else f"• {b}" for b in p.focus]
        lines.append("")

        if "economical" in all_text_lower:
            lines.append("Our economical coffee options:")
            lines.append(f"• {SKUS['ultra_blend'].line(p.pack)}")
            lines.append(f"• {SKUS['bold'].line(p.pack)}")
        elif "freeze-dried" in all_text_lower or "premium" in all_text_lower:
            lines.append("Our premium freeze-dried coffee options:")
            lines.append(f"• {SKUS['purica'].line(p.pack)}")
            lines.append(f"• {SKUS['purista'].line(p.pack)}")
        else:
            lines.append("Our product range:")
            for k in p.skus:
                lines.append(f"• {SKUS[k].line(p.pack)}")
                
        lines += ["", "Those are the MRPs, and they are the same in every channel. Your buying "
                      "price depends on volume — tell me roughly what you need and I will work "
                      "it out.", ""]

        if stage in _ANGLE:
            lines += [_ANGLE[stage], ""]

        kg = mem.get("monthly_consumption_kg")
        if kg:
            lines += [f"Against roughly {kg} kg a month, I can work out exact landed pricing "
                      f"for you.", ""]
            used.append(f"consumption: {kg} kg/month")

        when = (mem.get("preferred_contact_time") or "").strip()
        cta = p.cta
        if when:
            cta += f" I will keep to {when}, as you asked."
            used.append(f"preferred contact time: {when}")
        lines += [cta, "", signature]
        body = "\n".join(lines)

    # A category's forbidden phrases must never appear. This is the specific
    # failure being fixed, so it is asserted rather than trusted.
    leaked = [f for f in p.forbid if f.lower() in body.lower()]

    subj = {
        "intro": f"Purity Beans instant coffee — {company}",
        "reply": f"Re: Purity Beans instant coffee — {company}",
        "followup_1": f"Following up — Purity Beans for {company}",
        "followup_2": f"A sample for {company}?",
        "founder_note": f"Last note from me — Purity Beans, {company}",
    }[stage]

    return {
        "subject": subj,
        "body": body,
        "stage": stage,
        "category": p.label,
        "products": [SKUS[k].name for k in p.skus],
        "pack_offered": p.pack,
        "memory_used": used,
        "forbidden_phrases_leaked": leaked,
        "continuity": cont,
        "channels_available": channels_for(lead),
        "why": (f"{p.label} · {stage.replace('_', ' ')} · "
                f"{len(used)} fact(s) from Business Memory · "
                f"{sent_count} previous email(s), {'replied' if replied else 'no reply'}"),
    }


# ── Channels ─────────────────────────────────────────────────────────────────
# Email is not assumed. Of 353 businesses only 4 have an address that passes the
# send gate, while 328 have a phone — so a drafter that starts from email is
# writing for 1% of the pipeline and silently ignoring the rest.

CHANNEL_PRIORITY = ("email", "whatsapp", "founder_call", "visit")


def channels_for(lead) -> list[str]:
    """Verified channels only, best first. Unverified contact is not a channel."""
    out = []
    from app.services.contact_trust import sendable
    if sendable(lead)[0]:
        out.append("email")
    phone = (getattr(lead, "whatsapp_number", "") or lead.phone or "").strip()
    if phone and bool(getattr(lead, "phone_verified", False)):
        out += ["whatsapp", "founder_call"]
    elif phone:
        # A number we hold but never verified still supports a founder dialling
        # it — a person hearing a wrong number costs nothing. Bulk WhatsApp to
        # an unverified number does not get the same latitude.
        out.append("founder_call")
    if not out:
        out.append("visit")
    return sorted(set(out), key=CHANNEL_PRIORITY.index)


# ── Relationship history across every channel ────────────────────────────────

_TOUCH_EVENTS = {
    "EMAIL_SENT": "email", "WHATSAPP_SENT": "whatsapp",
    "FOUNDER_CALL": "founder call", "AI_CALL": "AI call",
    "MEETING_HELD": "meeting", "SAMPLE_SENT": "sample", "PROPOSAL_SENT": "proposal",
}


def relationship(events, interactions) -> dict:
    """
    What has actually passed between us and this business, on ANY channel.

    Reading only EMAIL_SENT was the bug behind "never restart the relationship":
    a founder call that went well was invisible, so the next email opened by
    introducing the company to someone who had already spoken to the founder.
    """
    touches = []
    for e in events:
        ch = _TOUCH_EVENTS.get(e.event_type)
        if ch:
            touches.append((e.occurred_at, ch, ""))
    for i in interactions:
        touches.append((i.occurred_at, (i.method or "contact").replace("_", " "),
                        (i.outcome or "")))
    touches.sort(key=lambda t: t[0] or __import__("datetime").datetime.min)
    last = touches[-1] if touches else None
    return {
        "touches": len(touches),
        "channels_used": sorted({t[1] for t in touches}),
        "last_channel": last[1] if last else None,
        "last_at": last[0] if last else None,
        "last_outcome": last[2] if last else "",
        "emails_sent": sum(1 for t in touches if t[1] == "email"),
    }


def continuity_line(rel: dict, mem: dict) -> str:
    """
    The sentence that stops an email reading like a cold open when it is not.
    Empty when there genuinely is no history — silence beats a fabricated
    "as discussed" for a conversation that never happened.
    """
    if not rel.get("touches"):
        return ""
    ch, when = rel.get("last_channel"), rel.get("last_at")
    dm = (mem.get("decision_maker") or "").strip()
    on = f" on {when:%d %b}" if when else ""
    if ch in ("founder call", "founder_call", "AI call", "ai_call"):
        return (f"Thank you for speaking with {'me' if not dm else 'me'}{on}. "
                f"Following up on that conversation rather than starting again.")
    if ch in ("whatsapp",):
        return f"Following on from our WhatsApp exchange{on}."
    if ch in ("meeting",):
        return f"Thank you for meeting{on}."
    if ch in ("sample",):
        return f"Following up on the sample we sent{on}."
    if ch == "email":
        return ""      # handled by the follow-up stage wording
    return f"Following up on our last contact{on}."


# ── Channel-shaped rendering ─────────────────────────────────────────────────

def as_whatsapp(draft: dict, lead, mem: dict) -> str:
    """Short, scannable, no letterhead. Not the email with the greeting removed."""
    dm = (mem.get("decision_maker") or "").strip()
    p = pitch_for(getattr(lead, "division", "") or "")
    hi = f"Namaste {dm} ji," if dm else "Namaste,"
    bits = [hi, "", f"Hiten from Pure Pantry Provisions — Purity Beans instant coffee."]
    if draft.get("continuity"):
        bits.append(draft["continuity"])
    bits.append("")
    bits.append("100% coffee, zero chicory, in a glass jar:")
    for k in p.skus[:2]:
        s = SKUS[k]
        bits.append(f"• {s.name} — {'100 g' if p.pack == '100' else '50 g / 100 g'}")
    bits += ["", p.cta]
    return "\n".join(bits)


def as_call_brief(lead, mem: dict, rel: dict) -> dict:
    """What the founder needs in hand before dialling. No script to read aloud."""
    p = pitch_for(getattr(lead, "division", "") or "")
    supplier = (mem.get("current_supplier") or "").strip()
    objections = []
    if supplier:
        objections.append(f"\"We already buy {supplier}.\" — ask what they pay and what "
                          f"they would change about it. Offer a comparison, not a switch.")
    objections.append("\"Send me something on WhatsApp.\" — agree, then confirm the number "
                      "and a time to follow up.")
    if p.label.startswith(("Grocery", "Kirana", "Retail")):
        objections.append("\"No shelf space.\" — a 50 g jar takes little room; "
                          "offer a small opening order.")
    return {
        "who": mem.get("decision_maker") or "decision maker not yet known — ask for the owner",
        "business": f"{lead.company} · {p.label} · {lead.city or 'city unknown'}",
        "why_they_buy": p.context.format(company=lead.company),
        "history": (f"{rel['touches']} previous contact(s) via {', '.join(rel['channels_used'])}"
                    if rel["touches"] else "no previous contact"),
        "opening": (f"Namaste, am I speaking with {mem['decision_maker']}?"
                    if mem.get("decision_maker")
                    else f"Namaste, may I speak to the owner of {lead.company}?"),
        "ask": ["Which coffee brands do you stock or use now?",
                "Roughly how much do you go through in a month?",
                "Who decides what you buy?"],
        "products": [SKUS[k].line(p.pack) for k in p.skus],
        "likely_objections": objections,
        "objective": "Get agreement to receive pricing, and a name to address it to.",
        "call_when": mem.get("preferred_contact_time") or "no preferred time recorded",
    }
