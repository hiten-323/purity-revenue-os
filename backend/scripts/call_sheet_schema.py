"""
The call sheet's column contract — shared by the exporter and the importer.

One definition, two consumers. If these lived in the exporter alone, the
importer would re-declare them and the two would drift the first time a column
moved; matching on a header name that no longer exists fails silently, which is
the worst way for a data-capture layer to break.

Each manual column names the field it feeds in Revenue OS. A column with
target=None is context for the caller and is deliberately not imported.
"""
from __future__ import annotations

# ── groups (drive the colour bands and the banner) ───────────────────────────
GIVEN = "GIVEN"   # already known — never typed into
CALL  = "CALL"    # the call itself
GATE  = "GATE"    # clears the two send gates
QUAL  = "QUAL"    # qualification
SIG   = "SIG"     # buying signal
LEARN = "LEARN"   # words worth keeping

BAND = {
    GIVEN: "E8EDF2",
    CALL:  "FFF4D6",
    GATE:  "D6F0DC",
    QUAL:  "E6E2F5",
    SIG:   "DCEBF7",
    LEARN: "FBE4E4",
}

BANNER = [
    (GIVEN, "ALREADY KNOWN  —  do not retype"),
    (CALL,  "THE CALL"),
    (GATE,  "UNLOCKS THE SYSTEM"),
    (QUAL,  "QUALIFICATION"),
    (SIG,   "BUYING SIGNAL"),
    (LEARN, "THEIR WORDS  —  training data"),
]

# ── dropdown vocabularies ────────────────────────────────────────────────────
# Kept short and countable. Anything that needs nuance goes in a free-text
# column instead of growing this list — a dropdown with 30 options is a text
# field wearing a hat.
LISTS = {
    "outcome":    ["Connected", "No answer", "Busy - call back", "Wrong number",
                   "Number not in service", "Gatekeeper only",
                   "Asked to call later", "Not interested", "Do not call again"],
    "role":       ["Owner", "Partner", "Manager", "Purchase head", "F&B manager",
                   "Chef", "Admin/HR", "Store staff", "Accountant", "Unknown"],
    "quality":    ["Hot", "Warm", "Neutral", "Cold"],
    "yesno":      ["Yes", "No"],
    "yesnounk":   ["Yes", "No", "Unknown"],
    "yesmaybeno": ["Yes", "Maybe", "No"],
    "emailtype":  ["Business", "Personal", "Generic"],
    "format":     ["Instant", "Beans", "Ground", "Machine", "Mixed", "None"],
    "procure":    ["Distributor", "Direct", "Online", "Cash and carry", "Other"],
    "frequency":  ["Weekly", "Fortnightly", "Monthly", "Quarterly", "Ad hoc"],
    "satisfied":  ["Happy", "Neutral", "Unhappy"],
    "problem":    ["Price", "Quality", "Taste", "Supply", "Delivery",
                   "Packaging", "Credit terms", "Other", "None"],
    "objection":  ["Price", "Happy with current", "Need to taste first",
                   "Volume too small", "Credit terms", "Decision maker away",
                   "Brand unknown", "No objection"],
    "next":       ["Send sample", "Send price list", "Send catalogue",
                   "Call back", "Visit in person", "Send WhatsApp",
                   "Meeting fixed", "Closed - won", "Closed - lost", "Drop"],
}

# ── the columns ──────────────────────────────────────────────────────────────
# (key, title, width, group, dropdown, target)
#
# target is the Revenue OS field the importer writes. "call_history:x" and
# "objection_learnings:x" write to those tables instead of b2b_leads.
COLUMNS = [
    # ---- already known -----------------------------------------------------
    ("row",        "#",                        5, GIVEN, None, None),
    ("company",    "Business",                34, GIVEN, None, None),
    ("segment",    "Type",                    15, GIVEN, None, None),
    ("address",    "Address",                 40, GIVEN, None, None),
    ("city",       "City",                    14, GIVEN, None, None),
    ("phone",      "Phone",                   16, GIVEN, None, None),   # match key
    ("known_name", "Known contact",           20, GIVEN, None, None),
    ("rating",     "Rating",                   8, GIVEN, None, None),
    ("reviews",    "Reviews",                  9, GIVEN, None, None),
    ("email_have", "Email on file",           24, GIVEN, None, None),
    ("website",    "Website",                 24, GIVEN, None, None),
    ("territory",  "Territory",               13, GIVEN, None, None),
    ("note",       "Note",                    26, GIVEN, None, None),

    # ---- the call ----------------------------------------------------------
    ("call_date",  "Call date",               12, CALL, None,      "call_history:call_date"),
    ("attempt",    "Attempt #",               10, CALL, None,      "call_attempts"),
    ("outcome",    "Call outcome",            18, CALL, "outcome", "call_outcome_last"),
    ("spoke_with", "Person spoken to",        20, CALL, None,      "contact_name"),
    ("their_role", "Their role",              18, CALL, "role",    "contact_title"),
    ("quality",    "Conversation quality",    18, CALL, "quality", "lead_temperature_tier"),
    ("owner",      "Owner (who called)",      16, CALL, None,      "lead_owner"),

    # ---- unlocks the system ------------------------------------------------
    ("email_new",  "EMAIL captured",          28, GATE, None,        "email"),
    ("email_type", "Email type",              14, GATE, "emailtype", "email_is_generic"),
    ("consent",    "OK to email/WhatsApp?",   20, GATE, "yesno",     "consent_status"),
    ("is_dm",      "Is this the decider?",    18, GATE, "yesnounk",  "decision_maker"),
    ("dm_name",    "Decision maker name",     22, GATE, None,        "decision_maker"),
    ("dm_phone",   "Decision maker phone",    18, GATE, None,        None),
    ("direct_ph",  "Direct phone",            16, GATE, None,        None),
    ("has_wa",     "WhatsApp on this no.?",   18, GATE, "yesno",     "whatsapp_number"),

    # ---- qualification -----------------------------------------------------
    ("biz_type",   "Business type (theirs)",  20, QUAL, None,        None),
    ("outlets",    "No. of outlets",          13, QUAL, None,        "distribution_outlets"),
    ("seats",      "Seats / capacity",        14, QUAL, None,        "company_size"),
    ("serves",     "Coffee served?",          14, QUAL, "yesnounk",  None),
    ("cof_format", "Coffee format",           15, QUAL, "format",    None),
    ("brand_now",  "Current coffee brand",    22, QUAL, None,        "current_brand"),
    ("supplier",   "Current supplier",        22, QUAL, None,        "current_supplier"),
    ("sup_contact","Supplier contact",        18, QUAL, None,        None),
    ("kg_month",   "Monthly consumption kg",  16, QUAL, None,        "monthly_consumption"),
    ("spend",      "Approx monthly spend",    16, QUAL, None,        None),
    ("price_kg",   "Current price/kg",        14, QUAL, None,        "price_per_kg"),
    ("pack",       "Pack size used",          13, QUAL, None,        "reorder_sku"),
    ("freq",       "Purchase frequency",      16, QUAL, "frequency", None),
    ("procure",    "Procurement method",      18, QUAL, "procure",   None),

    # ---- buying signal -----------------------------------------------------
    ("satisfied",  "Satisfaction now",        15, SIG, "satisfied",  None),
    ("problem",    "Main problem",            16, SIG, "problem",    None),
    ("switch_open","Switching open?",         15, SIG, "yesmaybeno", None),
    ("trial",      "Trial willing?",          14, SIG, "yesmaybeno", None),
    ("sample_req", "Sample requested?",       15, SIG, "yesno",      "sample_requested"),
    ("cat_req",    "Catalogue requested?",    17, SIG, "yesno",      None),
    ("price_req",  "Price list requested?",   17, SIG, "yesno",      None),
    ("buy_date",   "Expected buying date",    16, SIG, None,         None),
    ("contract",   "Existing contract?",      15, SIG, "yesnounk",   None),

    # ---- their words -------------------------------------------------------
    ("why_stay",   "Why do they STAY with their supplier?",
                                              46, LEARN, None, "objection_learnings:notes"),
    ("why_switch", "What would make them SWITCH?",
                                              46, LEARN, None, "objection_learnings:notes"),
    ("objection",  "Objection",               20, LEARN, "objection", "objection_reason"),
    ("competitor", "Competitor mentioned",    20, LEARN, None,
                                                     "objection_learnings:competitor_name"),
    ("remarks",    "EXACT WORDS / REMARKS",   58, LEARN, None,        "call_summary"),
    ("next_act",   "Next action",             20, LEARN, "next",      None),
    ("follow_up",  "Follow-up date",          14, LEARN, None,        "next_followup_date"),
]

BY_KEY = {c[0]: c for c in COLUMNS}
TITLES = [c[1] for c in COLUMNS]
MANUAL_KEYS = [c[0] for c in COLUMNS if c[3] != GIVEN]
MATCH_KEY = "phone"


def index_of(key: str) -> int:
    """1-based column index, for spreadsheet formulas."""
    for i, c in enumerate(COLUMNS, start=1):
        if c[0] == key:
            return i
    raise KeyError(key)


# ── who to call first ────────────────────────────────────────────────────────
# Coffee-native segments lead. A cafe already buys coffee every week and can
# answer every qualification question from memory; a corporate office may buy
# more in the end but the first batch has to produce conversion data fast, and
# that needs people who know their own numbers.
SEGMENT_PRIORITY = [
    "cafe", "horeca", "hotel", "restaurant",
    "grocery", "retail",
    "distributor", "wholesaler",
    "corporate_office", "corporate",
    "facility_management", "hospital",
]


def segment_rank(seg: str) -> int:
    s = (seg or "").strip().lower()
    for i, name in enumerate(SEGMENT_PRIORITY):
        if s == name:
            return i
    return len(SEGMENT_PRIORITY)
