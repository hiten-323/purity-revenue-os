"""
Founder call sheet — the 1,400+ businesses we have a phone number for and no
way to email.

Why this exists
---------------
Sending is gated twice: email_trust must reach VERIFIED, AND email_confidence
must clear CONFIDENCE_FLOOR (40). Confidence is earned from interaction
history, so a scraped address sits at 0 and can never send no matter how good
the evidence looks. Discovery cannot break that loop; a conversation can.

One founder call clears both gates at once and fills six fields nothing else
in the system can produce:

    decision maker · consent · current supplier · consumption · timing · objection

So the manual columns here are not a notepad. Each maps to a field the system
needs back, and the three green ones are the gates themselves.

Nothing in this file invents data. Columns we do not have are exported empty —
contact_name and contact_title are 0% populated, and that is what the call is
for, not a gap to fill with a guess.

Usage
-----
    python scripts/export_call_sheet.py                  # every city
    python scripts/export_call_sheet.py --city Bathinda  # one city
    python scripts/export_call_sheet.py --limit 100 --min-rating 4.3

Output lands in exports/, which is gitignored: this is real third-party
contact data and must not enter version control.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ── palette ──────────────────────────────────────────────────────────────────
INK      = "1F2933"
GIVEN_BG = "E8EDF2"   # we already know this
CALL_BG  = "FFF4D6"   # fill during the call
GATE_BG  = "D6F0DC"   # fill these and the lead becomes contactable
BUY_BG   = "E6E2F5"   # buying situation
OUT_BG   = "FBE4E4"   # what happens next
HEAD_BG  = "2E4057"
RULE     = Side(style="thin", color="B8C2CC")
BORDER   = Border(left=RULE, right=RULE, top=RULE, bottom=RULE)

GIVEN, CALL, GATE, BUY, OUT = "GIVEN", "CALL", "GATE", "BUY", "OUT"

# (title, width, group, dropdown_key)
COLUMNS = [
    ("#",                       5,  GIVEN, None),
    ("Business",               34,  GIVEN, None),
    ("Type",                   15,  GIVEN, None),
    ("Address",                42,  GIVEN, None),
    ("City",                   14,  GIVEN, None),
    ("Phone",                  16,  GIVEN, None),
    ("Rating",                  8,  GIVEN, None),
    ("Reviews",                 9,  GIVEN, None),
    ("Email on file",          26,  GIVEN, None),
    ("Website",                26,  GIVEN, None),
    ("Territory",              13,  GIVEN, None),
    ("Note",                   26,  GIVEN, None),

    ("Call date",              12,  CALL,  None),
    ("Outcome",                18,  CALL,  "outcome"),
    ("Spoke with",             20,  CALL,  None),
    ("Their role",             18,  CALL,  "role"),
    ("Direct no.",             15,  CALL,  None),

    ("EMAIL captured",         28,  GATE,  None),
    ("OK to email/WhatsApp?",  20,  GATE,  "yesno"),
    ("Is this the decider?",   18,  GATE,  "yesno"),

    ("Coffee brand now",       22,  BUY,   None),
    ("Price paying/kg",        14,  BUY,   None),
    ("Kg per month",           13,  BUY,   None),
    ("Pack size",              12,  BUY,   None),
    ("Who signs off",          20,  BUY,   None),
    ("Buying window",          18,  BUY,   "timing"),

    ("Sample asked?",          13,  OUT,   "yesno"),
    ("Sample sent on",         13,  OUT,   None),
    ("Price quoted",           13,  OUT,   None),
    ("Main objection",         22,  OUT,   "objection"),
    ("REMARKS",                60,  OUT,   None),
    ("Next action",            20,  OUT,   "next"),
    ("Follow up on",           13,  OUT,   None),
]

BAND = {GIVEN: GIVEN_BG, CALL: CALL_BG, GATE: GATE_BG, BUY: BUY_BG, OUT: OUT_BG}
BANNER = [
    (GIVEN, "ALREADY KNOWN  —  do not retype"),
    (CALL,  "THE CALL"),
    (GATE,  "UNLOCKS THE SYSTEM  —  fill these three"),
    (BUY,   "BUYING SITUATION"),
    (OUT,   "OUTCOME  &  REMARKS"),
]

LISTS = {
    "outcome":   ["Connected", "No answer", "Busy - call back", "Wrong number",
                  "Number not in service", "Gatekeeper only", "Asked to call later",
                  "Not interested", "Do not call again"],
    "role":      ["Owner", "Partner", "Manager", "Purchase head", "F&B manager",
                  "Chef", "Admin/HR", "Store staff", "Accountant", "Unknown"],
    "yesno":     ["Yes", "No"],
    "timing":    ["Buying now", "Within 1 month", "1-3 months", "3-6 months",
                  "Contract locked", "No plan"],
    "objection": ["Price", "Happy with current", "Need to taste first",
                  "Volume too small", "Credit terms", "Decision maker away",
                  "Brand unknown", "No objection"],
    "next":      ["Send sample", "Send price list", "Call back", "Visit in person",
                  "Send WhatsApp", "Meeting fixed", "Closed - won", "Closed - lost",
                  "Drop"],
}


def _digits(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def _phone_flag(raw) -> str:
    """Numbers that are not worth a founder's time, named rather than dropped.

    A 1800 line reaches a national call centre, never a purchase decision
    maker. A +55 number is a discovery defect — that business is in Brazil.
    Both stay in the sheet so the record is visible and correctable; they just
    sort last and say why.
    """
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("91"):
        d = d[2:]
    if d.startswith(("1800", "1860", "1900")):
        return "toll-free — call centre, not a buyer"
    if len(d) > 11:
        return "not an India number — check the record"
    return ""


def _clean(v) -> str:
    s = str(v or "").strip()
    return "" if s.lower() in ("none", "null", "unknown", "n/a", "nan") else s


def fetch(args):
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import territory

    db = SessionLocal()
    try:
        q = db.query(B2BLead).filter(B2BLead.phone.isnot(None), B2BLead.phone != "")
        rows = [l for l in q.all() if _digits(l.phone)]
        rows = [l for l in rows if not getattr(l, "do_not_call", False)]
        if args.city:
            want = args.city.strip().lower()
            rows = [l for l in rows if _clean(l.city).lower() == want]
        if args.min_rating:
            rows = [l for l in rows if (l.maps_rating or 0) >= args.min_rating]
        if args.segment:
            want = {w.strip().lower() for w in args.segment.split(",")}
            rows = [l for l in rows
                    if _clean(getattr(l, "segment", "")).lower() in want
                    or _clean(getattr(l, "division", "")).lower() in want]

        # One row per phone number. 21 numbers are shared by several branches;
        # calling the same desk twice wastes the call and reads as spam. Keep
        # the best-rated branch and say how many others sit behind that number.
        best, extra = {}, {}
        for l in sorted(rows, key=lambda x: -(x.maps_rating or 0)):
            k = _digits(l.phone)
            if k in best:
                extra[k] = extra.get(k, 0) + 1
            else:
                best[k] = l
        leads = list(best.values())

        # Cluster by city, best businesses first inside each — a day's calling
        # should not zig-zag across Punjab.
        leads.sort(key=lambda l: (bool(_phone_flag(l.phone)),
                                  _clean(l.city).lower(),
                                  -(l.maps_rating or 0),
                                  -(l.maps_reviews_count or 0)))
        if args.limit:
            leads = leads[: args.limit]

        out = []
        for l in leads:
            k = _digits(l.phone)
            try:
                terr = territory.territory_of(l)
            except Exception:
                terr = ""
            n = extra.get(k, 0)
            out.append([
                None,
                _clean(l.company),
                _clean(getattr(l, "segment", "")) or _clean(getattr(l, "division", "")),
                _clean(l.address),
                _clean(l.city),
                _clean(l.phone),
                float(l.maps_rating) if l.maps_rating else None,
                int(l.maps_reviews_count) if l.maps_reviews_count else None,
                _clean(l.email),
                _clean(l.website),
                terr,
                "  ·  ".join(x for x in (
                    (f"+{n} branch" + ("es" if n > 1 else "")) if n else "",
                    _phone_flag(l.phone),
                ) if x),
            ])
        return out
    finally:
        db.close()


def build(rows, path, scope):
    wb = Workbook()
    ws = wb.active
    ws.title = "Call Sheet"
    n_manual = sum(1 for c in COLUMNS if c[2] != GIVEN)

    # ── title ────────────────────────────────────────────────────────────────
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))
    t = ws.cell(1, 1, f"PURITY BEANS  ·  FOUNDER CALL SHEET      {scope}      "
                      f"{len(rows)} businesses      generated {date.today():%d %b %Y}")
    t.font = Font(bold=True, size=13, color="FFFFFF")
    t.fill = PatternFill("solid", fgColor=HEAD_BG)
    t.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26

    # ── group banner ─────────────────────────────────────────────────────────
    col = 1
    for key, label in BANNER:
        span = sum(1 for c in COLUMNS if c[2] == key)
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + span - 1)
        b = ws.cell(2, col, label)
        b.font = Font(bold=True, size=9, color=INK)
        b.fill = PatternFill("solid", fgColor=BAND[key])
        b.alignment = Alignment(horizontal="center", vertical="center")
        b.border = BORDER
        col += span
    ws.row_dimensions[2].height = 20

    # ── header ───────────────────────────────────────────────────────────────
    for i, (title, width, _g, _dv) in enumerate(COLUMNS, start=1):
        h = ws.cell(3, i, title)
        h.font = Font(bold=True, size=9, color="FFFFFF")
        h.fill = PatternFill("solid", fgColor=HEAD_BG)
        h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        h.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[3].height = 34

    # ── data ─────────────────────────────────────────────────────────────────
    first = 4
    for r, row in enumerate(rows, start=first):
        row[0] = r - first + 1
        for i, (title, _w, group, _dv) in enumerate(COLUMNS, start=1):
            v = row[i - 1] if i - 1 < len(row) else None
            c = ws.cell(r, i, v)
            c.border = BORDER
            c.font = Font(size=9)
            if group != GIVEN:
                c.fill = PatternFill("solid", fgColor=BAND[group])
            elif (r - first) % 2:
                c.fill = PatternFill("solid", fgColor="F7F9FB")
            if title in ("Business", "Address", "REMARKS"):
                c.alignment = Alignment(vertical="top", wrap_text=True)
            else:
                c.alignment = Alignment(horizontal="center", vertical="top")
        ws.row_dimensions[r].height = 30
    last = first + len(rows) - 1

    # ── dropdowns, sourced from a hidden sheet (dodges the 255-char limit) ────
    lst = wb.create_sheet("Lists")
    for j, (key, vals) in enumerate(LISTS.items(), start=1):
        L = get_column_letter(j)
        lst.cell(1, j, key)
        for i, v in enumerate(vals, start=2):
            lst.cell(i, j, v)
        ref = f"Lists!${L}$2:${L}${len(vals) + 1}"
        for i, (_t, _w, _g, dv_key) in enumerate(COLUMNS, start=1):
            if dv_key == key:
                dv = DataValidation(type="list", formula1=ref, allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{get_column_letter(i)}{first}:{get_column_letter(i)}{last}")
    lst.sheet_state = "hidden"

    # ── usability ────────────────────────────────────────────────────────────
    ws.freeze_panes = "G4"                       # keep name + phone on screen
    ws.auto_filter.ref = f"A3:{get_column_letter(len(COLUMNS))}{last}"
    ws.print_title_rows = "1:3"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    guide(wb, len(rows), n_manual)
    wb.save(path)


def guide(wb, n_rows, n_manual):
    g = wb.create_sheet("How to use", 1)
    g.column_dimensions["A"].width = 30
    g.column_dimensions["B"].width = 96
    g.sheet_view.showGridLines = False

    def line(a, b="", bold=False, fill=None):
        r = g.max_row + 1 if g.max_row > 1 or g.cell(1, 1).value else 1
        ca, cb = g.cell(r, 1, a), g.cell(r, 2, b)
        ca.font = Font(bold=True, size=10, color=INK)
        cb.font = Font(bold=bold, size=10)
        cb.alignment = Alignment(wrap_text=True, vertical="top")
        if fill:
            ca.fill = cb.fill = PatternFill("solid", fgColor=fill)
        g.row_dimensions[r].height = 32 if len(str(b)) > 95 else 17
        return r

    r = line("WHY THIS SHEET EXISTS", "", fill=HEAD_BG)
    g.cell(r, 1).font = Font(bold=True, size=12, color="FFFFFF")
    line("", f"{n_rows} businesses where we have a phone number. Automated outreach "
             f"cannot reach any of them, and no amount of extra scraping will change "
             f"that — see the two gates below.")
    line("")
    line("Gate 1 — trust", "An email address only becomes sendable when we can show the "
                           "business gave it to us. An address scraped off a listing never can.")
    line("Gate 2 — confidence", "Even at VERIFIED, sending needs a confidence score of 40+, "
                                "and confidence is earned from real interaction. A cold "
                                "record sits at 0 permanently.")
    line("The call clears both", "An address given on a call is first-party evidence AND the "
                                 "interaction that scores it. That is the whole unlock.", bold=True)
    line("")

    line("THE THREE GREEN COLUMNS", "", fill=GATE_BG)
    line("EMAIL captured", "The highest-value field on this sheet. Ask every time, even on a "
                           "'not interested' call.")
    line("OK to email/WhatsApp?", "Consent. Without an explicit yes we cannot start a sequence, "
                                  "so a captured email with no consent is half a result.")
    line("Is this the decider?", "Stops us running a full cadence at someone who cannot buy.")
    line("")

    line("FILLING IT IN", "", fill=CALL_BG)
    line("Coloured cells", f"{n_manual} columns are yours to fill. Grey ones are already known — "
                           f"please do not retype them; a re-import matches on Phone.")
    line("Dropdowns", "Outcome, role, buying window, objection and next action are dropdowns so "
                      "the answers stay countable. Type freely only in REMARKS.")
    line("REMARKS", "Their words, not a summary. 'Buys 8kg from a Ludhiana distributor, unhappy "
                    "with delivery delays' is worth more than 'interested'.")
    line("Blank is fine", "Never guess a figure. An empty cell is data; an invented one corrupts "
                          "every number built on top of it.")
    line("")

    line("HOW IT IS ORDERED", "", fill=GIVEN_BG)
    line("City, then rating", "Clustered so a day's calling stays in one place, best-reviewed "
                              "business first — rating and review count are the only demand "
                              "signal we have without talking to them.")
    line("'Note' column", "Says what is odd about the row: a number shared with other branches "
                          "(one call covers them all), a 1800 line that reaches a call centre "
                          "rather than a buyer, or a foreign number that should not be here. "
                          "Flagged rows sort to the bottom.")
    line("")

    line("HANDLING", "", fill=OUT_BG)
    line("This is real data", "Real businesses who never asked to be in a spreadsheet. Keep it "
                              "off shared drives and out of email. It is gitignored for the "
                              "same reason.")
    line("Do not call again", "Mark it in Outcome and it will be honoured permanently.")
    line("Regenerate", "python scripts/export_call_sheet.py --city <name>")


def main():
    ap = argparse.ArgumentParser(description="Export the founder call sheet.")
    ap.add_argument("--city")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--segment", help="comma-separated, e.g. cafe,horeca,grocery")
    ap.add_argument("--out")
    args = ap.parse_args()

    rows = fetch(args)
    if not rows:
        print("No leads matched. Nothing written.")
        return 1

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    outdir = os.path.join(root, "exports")
    os.makedirs(outdir, exist_ok=True)
    tag = (args.city or "all-cities").lower().replace(" ", "-")
    path = args.out or os.path.join(outdir, f"call-sheet-{tag}-{date.today():%Y%m%d}.xlsx")

    scope = args.city or "All cities"
    if args.min_rating:
        scope += f"  ·  rating {args.min_rating}+"
    if args.segment:
        scope += f"  ·  {args.segment}"
    build(rows, path, scope)
    print(f"{len(rows)} businesses -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
