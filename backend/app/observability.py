r"""
Make the engine's decisions visible, in the process that makes them.

The problem this fixes
----------------------
Fourteen modules call logging.getLogger(__name__) and log at INFO or DEBUG.
Nothing configured a handler. The root logger had zero handlers and the
effective level was WARNING, so every one of those lines was discarded --
including email_sender's record of WHY a send was suppressed. worker.py called
basicConfig, so the worker printed something; run_server.py did not, so the API
printed nothing below WARNING. Two processes, two different realities, neither
documented.

Adding more log calls without fixing that would have produced more silence.

What a decision line looks like
-------------------------------
    14:32:07 DECISION  phone.eligibility          lead=1274 REFUSED  preference registry: no preference registry configured
    14:32:07 DECISION  orchestrator.next_touch    lead=1274 WAIT     next touch is phone on day 4
    14:32:08 DECISION  email.send_gate            lead=88   ALLOWED  VERIFIED, confidence 65

One line per decision, aligned so a column scan works, and greppable by step:

    grep "phone.eligibility" logs/purity-worker.log
    grep "REFUSED" logs/purity-api.log | sort | uniq -c

The reason is ALWAYS the string the gate itself returned. It is never
paraphrased here and never invented -- a log that reports a prettier reason
than the code produced is worse than no log, because it will be believed.

Where it goes
-------------
stdout (so pm2 captures it into ~/.pm2/logs) and a rotating file under
backend/logs/. Both, because pm2's files are what you have after a crash and
the local file is what you have when pm2 is not running -- which, after a
reboot, is the normal state.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_configured = False

# Verdicts. Kept short and fixed-width so a column of them reads at a glance,
# and finite so `grep REFUSED | sort | uniq -c` is a real answer.
ALLOWED = "ALLOWED"
REFUSED = "REFUSED"
SKIPPED = "SKIPPED"
WAIT = "WAIT"
STOP = "STOP"
DONE = "DONE"


def enable_utf8_stdout() -> None:
    """Make the console agree with the log file about what a reason said.

    The gates' reason strings contain em-dashes. Windows stdout defaults to
    cp1252 and renders them as "?", so a reason read on screen differed from
    the same reason read from the file — the sort of small discrepancy that
    makes someone doubt the more important numbers next to it.

    Reconfiguring the stream is the fix, not rewriting the reasons: that text
    belongs to the code that decided, not to whatever is displaying it. Every
    script that prints a gate's reason should call this.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass                      # a stream that cannot be reconfigured is fine


def setup_logging(component: str = "app", level: str = "") -> logging.Logger:
    """Configure root once per process. Safe to call repeatedly.

    component names the log file, so purity-worker.log and purity-api.log stay
    separate -- interleaving two processes into one file makes a timeline
    unreadable exactly when you need it.
    """
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    lvl = (level or os.getenv("LOG_LEVEL", "") or "INFO").upper()
    root.setLevel(getattr(logging, lvl, logging.INFO))

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s",
                            datefmt="%H:%M:%S")

    # basicConfig may already have run (worker.py calls it). Replacing its
    # handler rather than adding to it avoids every line appearing twice.
    for h in list(root.handlers):
        root.removeHandler(h)

    enable_utf8_stdout()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fileh = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, f"purity-{component}.log"),
            maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except Exception as exc:  # noqa: BLE001
        # A read-only, missing or malformed directory must not stop the process
        # from running; it only costs the durable copy, and stdout survives.
        #
        # Deliberately broader than OSError: os.makedirs raises ValueError for
        # a path containing a null byte, so an OSError-only clause let a bad
        # LOG_DIR crash the API at boot. Found by the test, not by reading.
        root.warning("file logging unavailable (%s: %s); stdout only",
                     exc.__class__.__name__, exc)

    # These are loud and are not our decisions.
    for noisy in ("urllib3", "httpx", "httpcore", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.info("logging configured: component=%s level=%s dir=%s",
              component, lvl, LOG_DIR)
    return root


def decision(step: str, verdict: str, reason: str, *, lead=None, **facts) -> None:
    """Record one real decision, with the reason the deciding code produced.

    `step` is dotted and stable (phone.eligibility, email.send_gate) so it can
    be grepped and counted. `reason` is passed through verbatim.

    Never raises. Observability that can break the thing it observes is a
    liability, and a gate must not fail because a log file is full.
    """
    try:
        lead_id = getattr(lead, "id", lead)
        who = f"lead={lead_id}" if lead_id is not None else ""
        extra = " ".join(f"{k}={v}" for k, v in facts.items() if v not in (None, ""))
        logging.getLogger("purity.decision").info(
            "DECISION  %-26s %-10s %-8s %s%s",
            step, who, verdict, str(reason)[:300],
            f"  [{extra}]" if extra else "")
    except Exception:
        pass


def step(name: str, message: str, **facts) -> None:
    """Progress within a multi-step job -- what it is doing, on what, right now.

    Distinct from decision(): a step reports work, a decision reports a verdict
    and its reason. Mixing them makes it impossible to count refusals.
    """
    try:
        extra = " ".join(f"{k}={v}" for k, v in facts.items() if v not in (None, ""))
        logging.getLogger("purity.step").info(
            "STEP      %-26s %s%s", name, message, f"  [{extra}]" if extra else "")
    except Exception:
        pass
