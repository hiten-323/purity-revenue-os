"""
Founder call sheet — a manual data-capture layer for Revenue OS.

Why this exists
---------------
Sending is gated twice: email_trust must reach VERIFIED, AND email_confidence
must clear CONFIDENCE_FLOOR (40). Confidence is earned from interaction, so a
scraped address sits at 0 and can never send however good the evidence looks.
Discovery cannot break that loop. A conversation can: trust_promoter's
on_founder_call() promotes to VERIFIED and recomputes confidence in one step.

1,430 leads have a phone number and no usable email. This turns each call into
structured intelligence that flows back through import_call_sheet.py.

Not a second CRM
----------------
CALL QUEUE is the only sheet anyone types into. ALL LEADS, CALL HISTORY,
QUALIFIED BUYERS, FOLLOW-UPS and DROPPED are rebuilt from the database on every
export. A hand-maintained parallel sheet drifts from the system it shadows and
then quietly outranks it; a derived one cannot. If a derived sheet looks wrong,
the database is wrong — fix it there and re-export.

    export  ->  call  ->  import_call_sheet.py  ->  Revenue OS  ->  re-export

Usage
-----
    python scripts/export_call_sheet.py                      # queue of 150
    python scripts/export_call_sheet.py --queue 300
    python scripts/export_call_sheet.py --city Bathinda
    python scripts/export_call_sheet.py --segment cafe,horeca --min-rating 4.3

Output lands in exports/, which is gitignored: real third-party contact data.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from call_sheet_schema import (BAND, BANNER, COLUMNS, GIVEN, LISTS,
                               index_of, segment_rank)

INK     = "1F2933"
HEAD_BG = "2E4057"
RULE    = Side(style="thin", color="B8C2CC")
BORDER  = Border(left=RULE, right=RULE, top=RULE, bottom=RULE)

# The status palette. Same six meanings everywhere they appear.
STATUS = [
    ("FF6B6B", "Call today",           "not yet attempted"),
    ("FFD166", "Follow-up due",        "follow-up date today or earlier"),
    ("06D6A0", "Qualified opportunity","switching open and a real volume"),
    ("4EA8DE", "Sample requested",     "send the sample"),
    ("B388EB", "Catalogue requested",  "send the catalogue"),
    ("6C757D", "Do not call",          "never dial again"),
]


def _digits(p) -> str:
    d = re.sub(r"\D", "", str(p or ""))
    return d[-10:] if len(d) >= 10 else d


def _phone_flag(raw) -> str:
    """Numbers not worth a founder's time, named rather than dropped.

    A 1800 line reaches a national call centre, never a purchase decision
    maker. A +55 number is a discovery defect — that business is in Brazil.
    Both stay visible so the record can be corrected; they just sort last.
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


# ── data ─────────────────────────────────────────────────────────────────────
def load(args):
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import territory

    db = SessionLocal()
    try:
        q = db.query(B2BLead).filter(B2BLead.phone.isnot(None), B2BLead.phone != "")
        rows = [l for l in q.all() if _digits(l.phone)]

        dnc = [l for l in rows if getattr(l, "do_not_call", False)]
        rows = [l for l in rows if not getattr(l, "do_not_call", False)]

        if args.city:
            want = args.city.strip().lower()
            rows = [l for l in rows if _clean(l.city).lower() == want]
        if args.min_rating:
            rows = [l for l in rows if (l.maps_rating or 0) >= args.min_rating]
        if args.territory:
            want = {w.strip().upper() for w in args.territory.split(",")}
            rows = [l for l in rows if territory.territory_of(l) in want]
        if args.stable:
            # Only rows whose contact data will still be true when you dial it.
            #
            # A SEARCH-provenance phone is perishable: in one observed 15-minute
            # window the enrichment loop rewrote 8 of them, including a Ludhiana
            # landline that became an unrelated mobile. Printing 1,400 of those
            # produces a sheet that disagrees with the database by the time it
            # reaches a hand. Two kinds of row survive that objection:
            #
            #   FIRST_PARTY phone  a business or brand published it, and the
            #                      provenance guard now forbids overwriting it
            #   SEND-ready         the decision engine will email this lead, so
            #                      the relationship does not depend on the phone
            #
            # Measured 2026-08-23: 27 and 7, with zero overlap — they are
            # different populations wanting different openings, which is why
            # each row says which one it is.
            from app.services.contact_enricher import FIRST_PARTY, phone_provenance
            from app.services.decision_engine import evaluate_next_action

            keep = []
            for l in rows:
                first_party = phone_provenance(l) == FIRST_PARTY
                sendable = False
                if not first_party and _clean(l.email):
                    try:
                        sendable = evaluate_next_action(l, db)["action"] == "SEND"
                    except Exception:
                        sendable = False
                if first_party or sendable:
                    l._stable_kind = "first-party phone" if first_party else "email is sendable"
                    keep.append(l)
            rows = keep
        if args.segment:
            want = {w.strip().lower() for w in args.segment.split(",")}
            rows = [l for l in rows
                    if _clean(getattr(l, "segment", "")).lower() in want
                    or _clean(getattr(l, "division", "")).lower() in want]

        # One row per phone number: 21 numbers are shared across branches and a
        # single Reliance toll-free line covered 7 rows. Calling the same desk
        # twice wastes the call and reads as spam.
        best, extra = {}, {}
        for l in sorted(rows, key=lambda x: -(x.maps_rating or 0)):
            k = _digits(l.phone)
            if k in best:
                extra[k] = extra.get(k, 0) + 1
            else:
                best[k] = l
        leads = list(best.values())

        def seg_of(l):
            return _clean(getattr(l, "segment", "")) or _clean(getattr(l, "division", ""))

        # Coffee-native segments first, then never-attempted, then city cluster,
        # then best-reviewed. Flagged numbers always last.
        leads.sort(key=lambda l: (
            bool(_phone_flag(l.phone)),
            # On a --stable sheet the first-party rows are the ones you actually
            # dial: they have no sendable email, so the call is the only channel
            # and it is what converts them. The email-sendable rows are already
            # reachable without picking up the phone, so they sort last rather
            # than interleaving and breaking the caller's rhythm.
            getattr(l, "_stable_kind", "") == "email is sendable",
            segment_rank(seg_of(l)),
            (l.call_attempts or 0) > 0,
            _clean(l.city).lower(),
            -(l.maps_rating or 0),
            -(l.maps_reviews_count or 0),
        ))

        def to_row(l, n):
            try:
                terr = territory.territory_of(l)
            except Exception:
                terr = ""
            more = extra.get(_digits(l.phone), 0)
            # Provenance belongs on the sheet, not just in the schema. A caller
            # about to dial should know whether a business gave us this number
            # or an engine listed it — the second kind is a lead to confirm,
            # not a fact. And a landline reaches a desk, so ask for the person.
            try:
                from app.services.contact_enricher import (FIRST_PARTY, SEARCH,
                                                           is_landline,
                                                           phone_provenance)
                prov = phone_provenance(l)
                prov_note = ("published by the business" if prov == FIRST_PARTY
                             else "from search — confirm on the call"
                             if prov == SEARCH else "source unrecorded — confirm")
                land = "landline (no WhatsApp)" if is_landline(l.phone) else ""
            except Exception:
                prov_note = land = ""
            kind = getattr(l, "_stable_kind", "")
            note = "  ·  ".join(x for x in (
                ("EMAIL SENDABLE — call is optional"
                 if kind == "email is sendable" else ""),
                (f"+{more} branch" + ("es" if more > 1 else "")) if more else "",
                prov_note, land,
                _phone_flag(l.phone),
            ) if x)
            return [
                n, _clean(l.company), seg_of(l), _clean(l.address), _clean(l.city),
                _clean(l.phone), _clean(getattr(l, "contact_name", "")),
                float(l.maps_rating) if l.maps_rating else None,
                int(l.maps_reviews_count) if l.maps_reviews_count else None,
                _clean(l.email), _clean(l.website), terr, note,
            ]

        queue = [to_row(l, i) for i, l in enumerate(leads[: args.queue], start=1)]
        every = [to_row(l, i) for i, l in enumerate(leads, start=1)]
        return {"queue": queue, "all": every, "leads": leads,
                "dnc": dnc, "db": db, "derived": derive(db)}
    finally:
        db.close()


def derive(db):
    """Everything rebuilt from the database. Never typed into."""
    from app.models.models import B2BLead, CallHistory

    out = {"history": [], "qualified": [], "followups": [], "dnc": []}
    today = date.today()

    try:
        hist = (db.query(CallHistory)
                  .order_by(CallHistory.call_date.desc()).limit(2000).all())
        by_id = {l.id: l for l in db.query(B2BLead).all()}
        for h in hist:
            l = by_id.get(h.lead_id)
            out["history"].append([
                str(h.call_date)[:16] if h.call_date else "",
                _clean(l.company) if l else f"lead {h.lead_id}",
                _clean(l.phone) if l else "",
                _clean(h.status or h.call_status),
                h.duration or h.call_duration or "",
                _clean(h.summary)[:300],
            ])
    except Exception:
        pass

    for l in db.query(B2BLead).all():
        name, phone = _clean(l.company), _clean(l.phone)
        if getattr(l, "do_not_call", False):
            out["dnc"].append([name, phone, _clean(l.city),
                               _clean(getattr(l, "dnc_reason", "")),
                               str(getattr(l, "last_call_date", "") or "")[:10]])
            continue

        kg = getattr(l, "monthly_consumption", None)
        if _clean(getattr(l, "current_brand", "")) or _clean(getattr(l, "current_supplier", "")) or kg:
            out["qualified"].append([
                name, phone, _clean(l.city),
                _clean(getattr(l, "current_brand", "")),
                _clean(getattr(l, "current_supplier", "")),
                kg or "", getattr(l, "price_per_kg", None) or "",
                _clean(getattr(l, "objection_reason", "")),
                _clean(getattr(l, "lead_temperature_tier", "")),
                _clean(l.email), _clean(getattr(l, "consent_status", "")),
            ])

        nf = getattr(l, "next_followup_date", None)
        if nf:
            try:
                d = nf.date() if hasattr(nf, "date") else nf
                due = "OVERDUE" if d < today else ("TODAY" if d == today else "")
                if d <= today + timedelta(days=7):
                    out["followups"].append([
                        str(d)[:10], due, name, phone, _clean(l.city),
                        _clean(getattr(l, "call_outcome_last", "")),
                        _clean(getattr(l, "call_summary", ""))[:200],
                    ])
            except Exception:
                pass

    out["followups"].sort(key=lambda r: r[0])
    out["qualified"].sort(key=lambda r: -(float(r[5]) if str(r[5]).replace(".", "").isdigit() else 0))
    return out


# ── rendering ────────────────────────────────────────────────────────────────
def _header(ws, titles, row=1, widths=None):
    for i, t in enumerate(titles, start=1):
        c = ws.cell(row, i, t)
        c.font = Font(bold=True, size=9, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEAD_BG)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 30


def _simple_sheet(wb, title, headers, widths, rows, empty_note):
    ws = wb.create_sheet(title)
    _header(ws, headers, 1, widths)
    if not rows:
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
        c = ws.cell(2, 1, empty_note)
        c.font = Font(italic=True, size=10, color="6C757D")
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[2].height = 28
    for r, row in enumerate(rows, start=2):
        for i, v in enumerate(row, start=1):
            c = ws.cell(r, i, v)
            c.border = BORDER
            c.font = Font(size=9)
            c.alignment = Alignment(vertical="top", wrap_text=i in (len(row),))
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    return ws


def build_queue(wb, rows, scope, title):
    """The one capture surface."""
    ws = wb.create_sheet(title, 0)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))
    t = ws.cell(1, 1, f"PURITY BEANS  ·  {title}      {scope}      "
                      f"{len(rows)} businesses      {date.today():%d %b %Y}")
    t.font = Font(bold=True, size=13, color="FFFFFF")
    t.fill = PatternFill("solid", fgColor=HEAD_BG)
    t.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26

    col = 1
    for key, label in BANNER:
        span = sum(1 for c in COLUMNS if c[3] == key)
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + span - 1)
        b = ws.cell(2, col, label)
        b.font = Font(bold=True, size=9, color=INK)
        b.fill = PatternFill("solid", fgColor=BAND[key])
        b.alignment = Alignment(horizontal="center", vertical="center")
        b.border = BORDER
        col += span
    ws.row_dimensions[2].height = 20

    for i, (_k, title_, width, _g, _dv, _t) in enumerate(COLUMNS, start=1):
        h = ws.cell(3, i, title_)
        h.font = Font(bold=True, size=9, color="FFFFFF")
        h.fill = PatternFill("solid", fgColor=HEAD_BG)
        h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        h.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[3].height = 40

    first = 4
    for r, row in enumerate(rows, start=first):
        row[0] = r - first + 1
        for i, (_k, title_, _w, group, _dv, _t) in enumerate(COLUMNS, start=1):
            v = row[i - 1] if i - 1 < len(row) else None
            c = ws.cell(r, i, v)
            c.border = BORDER
            c.font = Font(size=9)
            if group != GIVEN:
                c.fill = PatternFill("solid", fgColor=BAND[group])
            elif (r - first) % 2:
                c.fill = PatternFill("solid", fgColor="F7F9FB")
            wrap = title_ in ("Business", "Address", "EXACT WORDS / REMARKS") \
                or title_.startswith(("Why do they", "What would"))
            c.alignment = (Alignment(vertical="top", wrap_text=True) if wrap
                           else Alignment(horizontal="center", vertical="top"))
        ws.row_dimensions[r].height = 32
    last = first + len(rows) - 1

    lst = wb.create_sheet("Lists")
    for j, (key, vals) in enumerate(LISTS.items(), start=1):
        L = get_column_letter(j)
        lst.cell(1, j, key)
        for i, v in enumerate(vals, start=2):
            lst.cell(i, j, v)
        ref = f"Lists!${L}$2:${L}${len(vals) + 1}"
        for i, (_k, _t2, _w, _g, dv_key, _tg) in enumerate(COLUMNS, start=1):
            if dv_key == key:
                dv = DataValidation(type="list", formula1=ref, allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{get_column_letter(i)}{first}:{get_column_letter(i)}{last}")
    lst.sheet_state = "hidden"

    if rows:
        _conditional(ws, first, last)

    ws.freeze_panes = f"{get_column_letter(index_of('rating'))}{first}"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(COLUMNS))}{last}"
    ws.print_title_rows = "1:3"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def _conditional(ws, first, last):
    """Colour the row as the founder types, so state is visible without reading.

    Order matters: openpyxl applies rules in sequence and the first match on a
    cell wins, so 'do not call' is registered last only because it is the most
    specific — it must not be overwritten by the softer signals above it.
    """
    span = f"A{first}:{get_column_letter(len(COLUMNS))}{last}"
    C = lambda k: get_column_letter(index_of(k))   # noqa: E731

    def rule(formula, colour):
        ws.conditional_formatting.add(
            span, FormulaRule(formula=[formula], stopIfTrue=True,
                              fill=PatternFill("solid", fgColor=colour)))

    dnc, red, amber, green, blue, purple = (STATUS[5][0], STATUS[0][0], STATUS[1][0],
                                            STATUS[2][0], STATUS[3][0], STATUS[4][0])
    # Most specific first.
    rule(f'=${C("outcome")}{first}="Do not call again"', dnc)
    rule(f'=${C("cat_req")}{first}="Yes"', purple)
    rule(f'=${C("sample_req")}{first}="Yes"', blue)
    rule(f'=AND(${C("switch_open")}{first}="Yes",${C("kg_month")}{first}<>"")', green)
    rule(f'=AND(${C("follow_up")}{first}<>"",${C("follow_up")}{first}<=TODAY())', amber)
    rule(f'=${C("call_date")}{first}=""', red)


def guide(wb, n_queue, n_all, n_manual):
    g = wb.create_sheet("HOW TO USE")
    g.sheet_view.showGridLines = False
    g.column_dimensions["A"].width = 32
    g.column_dimensions["B"].width = 100
    state = {"r": 0}

    def line(a, b="", bold=False, fill=None, big=False):
        state["r"] += 1
        r = state["r"]
        ca, cb = g.cell(r, 1, a), g.cell(r, 2, b)
        ca.font = Font(bold=True, size=12 if big else 10,
                       color="FFFFFF" if fill == HEAD_BG else INK)
        cb.font = Font(bold=bold, size=10)
        cb.alignment = Alignment(wrap_text=True, vertical="top")
        if fill:
            ca.fill = cb.fill = PatternFill("solid", fgColor=fill)
        g.row_dimensions[r].height = 34 if len(str(b)) > 99 else 17

    line("PURITY BEANS CALL SHEET", "", fill=HEAD_BG, big=True)
    line("", f"{n_queue} businesses queued for calling, {n_all} in the master list.")
    line("")

    line("WHY THIS EXISTS", "", fill=BAND[GIVEN])
    line("The problem", "1,430 businesses have a phone number and no usable email. "
                        "Automated outreach cannot reach a single one of them.")
    line("Gate 1 — trust", "An address only becomes sendable when we can show the business "
                           "gave it to us. Scraped addresses never can.")
    line("Gate 2 — confidence", "Even at VERIFIED, sending needs confidence 40+, and "
                                "confidence is earned from real interaction. A cold record "
                                "sits at 0 forever.")
    line("Why a call fixes it", "An address given on a call is first-party evidence AND the "
                                "interaction that scores it. One call clears both gates.", bold=True)
    line("")

    line("THE ONLY RULE", "", fill=BAND["GATE"])
    line("Type in CALL QUEUE only", "Every other sheet is rebuilt from the database each time "
                                    "this file is exported. Anything typed there is erased on "
                                    "the next export — and worse, it would quietly compete with "
                                    "the system for the truth.", bold=True)
    line("The loop", "export  ->  call  ->  import_call_sheet.py  ->  Revenue OS  ->  re-export")
    line("If a sheet looks wrong", "The database is wrong. Fix it there and re-export. Do not "
                                   "patch the spreadsheet.")
    line("")

    line("THE SHEETS", "", fill=BAND["QUAL"])
    line("CALL QUEUE", "Today's calls, best-first. THE ONLY SHEET YOU FILL IN.", bold=True)
    line("ALL LEADS", "The full master list, for lookup.")
    line("CALL HISTORY", "Every attempt ever logged. Derived.")
    line("QUALIFIED BUYERS", "Anyone whose supplier, brand or volume we now know. Derived.")
    line("FOLLOW-UPS", "Due in the next 7 days, overdue first. Derived.")
    line("DROPPED - DNC", "Never call these again. Derived.")
    line("")

    line("COLOUR MEANS", "", fill=BAND["SIG"])
    for colour, name, when in STATUS:
        line(f"   {name}", when, fill=colour)
    line("")

    line("THE THREE THAT MATTER", "", fill=BAND["GATE"])
    line("EMAIL captured", "The highest-value field here. Ask on every call, even a hostile one.")
    line("OK to email/WhatsApp?", "Consent. Without an explicit yes we cannot start a sequence, "
                                  "so an email with no consent is half a result.")
    line("Is this the decider?", "Stops a full cadence running at someone who cannot buy.")
    line("")

    line("THE TWO GOLD COLUMNS", "", fill=BAND["LEARN"])
    line("Why do they STAY?", "The real switching cost, in their words. 'Delivery is reliable' "
                              "and 'the owner's cousin supplies it' need completely different "
                              "answers from us.")
    line("What would make them SWITCH?", "They will tell you the exact offer that wins. This is "
                                          "the most valuable sentence on the sheet.", bold=True)
    line("Why it beats a tick-box", "'Uses Nescafe through a local distributor, will not change "
                                    "because delivery is reliable' teaches the messaging engine "
                                    "something. 'Not interested' teaches it nothing.")
    line("")

    line("FILLING IT IN", "", fill=BAND["CALL"])
    line("Coloured cells", f"{n_manual} columns are yours. Grey ones are already known — do not "
                           f"retype them; the import matches on Phone and a retyped number "
                           f"breaks the match.")
    line("Dropdowns", "Anything with a list is a dropdown, so answers stay countable. Type "
                      "freely only in the remarks and the two gold columns.")
    line("Blank is fine", "Never guess a figure. An empty cell is data; an invented one corrupts "
                          "every number built on top of it.", bold=True)
    line("Do not call again", "Choose it in Call outcome and it is honoured permanently.")
    line("")

    line("WHO TO CALL FIRST", "", fill=BAND["QUAL"])
    line("Order in the queue", "Cafe -> hotel/HORECA -> restaurant -> grocery/retail -> "
                               "distributor -> corporate pantry. Coffee-native businesses "
                               "answer every qualification question from memory, so the first "
                               "batch produces conversion data fastest.")
    line("Then", "Widen once that batch shows what actually converts.")
    line("")

    line("HANDLING", "", fill=BAND["LEARN"])
    line("This is real data", "Real businesses who never asked to be in a spreadsheet. Keep it "
                              "off shared drives and out of email. It is gitignored for the "
                              "same reason.")
    line("Regenerate", "python scripts/export_call_sheet.py --city <name> --queue 200")
    line("Import", "python scripts/import_call_sheet.py <file>            (dry run)")
    line("", "python scripts/import_call_sheet.py <file> --commit    (writes)")


def main():
    ap = argparse.ArgumentParser(description="Export the founder call sheet.")
    ap.add_argument("--city")
    ap.add_argument("--segment", help="comma-separated, e.g. cafe,horeca,grocery")
    ap.add_argument("--min-rating", type=float, default=0.0)
    ap.add_argument("--territory", help="e.g. DELHI_NCR, or R1_0_5,R2_5_15")
    ap.add_argument("--stable", action="store_true",
                    help="only first-party phones + SEND-ready leads "
                         "(excludes perishable search-derived numbers)")
    ap.add_argument("--queue", type=int, default=150, help="rows in CALL QUEUE")
    ap.add_argument("--out")
    args = ap.parse_args()

    data = load(args)
    if not data["queue"]:
        print("No leads matched. Nothing written.")
        return 1

    scope = args.city or "All cities"
    if args.segment:
        scope += f"  ·  {args.segment}"
    if args.stable:
        scope = "Stable contacts only"
    if args.territory:
        scope = args.territory.replace("_", " ")
    if args.min_rating:
        scope += f"  ·  rating {args.min_rating}+"

    wb = Workbook()
    wb.remove(wb.active)

    build_queue(wb, data["queue"], scope, "CALL QUEUE")

    d = data["derived"]
    _simple_sheet(wb, "ALL LEADS",
                  ["#", "Business", "Type", "Address", "City", "Phone",
                   "Known contact", "Rating", "Reviews", "Email on file",
                   "Website", "Territory", "Note"],
                  [5, 34, 15, 40, 14, 16, 20, 8, 9, 24, 24, 13, 26],
                  data["all"], "")
    _simple_sheet(wb, "CALL HISTORY",
                  ["When", "Business", "Phone", "Status", "Duration", "Summary"],
                  [17, 34, 16, 16, 10, 80], d["history"],
                  "No calls logged yet. This fills in after the first import.")
    _simple_sheet(wb, "QUALIFIED BUYERS",
                  ["Business", "Phone", "City", "Brand now", "Supplier", "Kg/month",
                   "Price/kg", "Objection", "Temperature", "Email", "Consent"],
                  [34, 16, 14, 20, 20, 10, 10, 20, 13, 24, 14], d["qualified"],
                  "Nobody qualified yet. A lead lands here once a call records their "
                  "supplier, brand or volume.")
    _simple_sheet(wb, "FOLLOW-UPS",
                  ["Due", "Status", "Business", "Phone", "City", "Last outcome", "Notes"],
                  [12, 11, 34, 16, 14, 18, 60], d["followups"],
                  "Nothing due. Set a follow-up date in CALL QUEUE and it appears here.")
    _simple_sheet(wb, "DROPPED - DNC",
                  ["Business", "Phone", "City", "Reason", "Last call"],
                  [34, 16, 14, 40, 12], d["dnc"],
                  "Nobody on the do-not-call list.")

    guide(wb, len(data["queue"]), len(data["all"]),
          sum(1 for c in COLUMNS if c[3] != GIVEN))

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    outdir = os.path.join(root, "exports")
    os.makedirs(outdir, exist_ok=True)
    tag = ("stable" if args.stable else
           (args.territory or args.city or args.segment or "all")).lower().replace(" ", "-").replace(",", "-")
    path = args.out or os.path.join(outdir, f"call-sheet-{tag}-{date.today():%Y%m%d}.xlsx")
    wb.save(path)

    print(f"CALL QUEUE {len(data['queue'])}  ·  ALL LEADS {len(data['all'])}  ·  "
          f"history {len(d['history'])}  ·  qualified {len(d['qualified'])}  ·  "
          f"follow-ups {len(d['followups'])}  ·  dnc {len(d['dnc'])}")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
