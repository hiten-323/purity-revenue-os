"""Call Result Capture + Call Learning loop.

Every AI call attempt gets exactly one structured ``CallResult`` row that is
opened at dial time and FINALIZED on every termination path (answered +
hangup, no answer, busy, voicemail, SIP error, agent crash, timeout). A lead
is never left in ``call_status='CALLING'``: the voice agent/engine report the
end, and the reconciler sweeps anything that was never reported.

Finalized rows feed the Outreach Intelligence ledger (PR #33) and a segment
aggregator with minimum-sample thresholds. Before the next dial a *call brief*
is built from this lead's prior calls plus segment lessons and passed to the
LiveKit agent as dispatch metadata.

Safety contract (same as outreach_intelligence):
  * advisory only -- nothing here grants eligibility, consent, or a retry;
  * it may only REFUSE a dial (standing do-not-call rules, prior terminal
    answer, requested callback time not reached) -- never permit one;
  * never invents data: every field is either observed (provider/agent),
    inferred from the transcript by a rule (marked INFERRED with evidence),
    or left empty. Reconciled rows are LOW_CONFIDENCE and excluded from
    learning rates;
  * WhatsApp/AiSensy is untouched; email eligibility is untouched.
"""
from app.services.call_intelligence.taxonomy import (  # noqa: F401
    RESULT_OUTCOMES,
    result_outcome_for,
    terminal_call_status_for,
)
