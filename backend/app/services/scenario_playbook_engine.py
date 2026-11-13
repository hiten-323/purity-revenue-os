"""
Scenario Playbook Engine for Purity Revenue OS V2.0.
Implements the 16 authoritative objection handling and communication playbooks (Scenarios A through P)
with strict SLA compliance, channel coordination, and fail-closed safety.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

_log = logging.getLogger("scenario_playbook_engine")

# 16 Authoritative Scenarios
SCENARIO_A = "SCENARIO_A_ACTIVE_BUYING_ENQUIRY"
SCENARIO_B = "SCENARIO_B_INTERESTED_DISTRIBUTOR"
SCENARIO_C = "SCENARIO_C_CAFE_BARISTA"
SCENARIO_D = "SCENARIO_D_RETAILER_SUPERMARKET"
SCENARIO_E = "SCENARIO_E_OFFICE_PROCUREMENT"
SCENARIO_F = "SCENARIO_F_PRICE_OBJECTION"
SCENARIO_G = "SCENARIO_G_SAMPLE_REQUESTED"
SCENARIO_H = "SCENARIO_H_NO_ANSWER_CADENCE"
SCENARIO_I = "SCENARIO_I_NOT_INTERESTED"
SCENARIO_J = "SCENARIO_J_WRONG_NUMBER"
SCENARIO_K = "SCENARIO_K_BUDGET_CYCLE_LATER"
SCENARIO_L = "SCENARIO_L_EXISTING_CUSTOMER"
SCENARIO_M = "SCENARIO_M_BOUNCED_EMAIL"
SCENARIO_N = "SCENARIO_N_WHATSAPP_FAILED"
SCENARIO_O = "SCENARIO_O_HUMAN_CALLBACK_REQUESTED"
SCENARIO_P = "SCENARIO_P_ORDER_CONVERTED"

ALL_SCENARIOS = [
    SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_D, SCENARIO_E,
    SCENARIO_F, SCENARIO_G, SCENARIO_H, SCENARIO_I, SCENARIO_J,
    SCENARIO_K, SCENARIO_L, SCENARIO_M, SCENARIO_N, SCENARIO_O,
    SCENARIO_P
]


@dataclass
class PlaybookAction:
    scenario_code: str
    scenario_title: str
    target_channel: str          # "PHONE", "EMAIL", "WHATSAPP", "HUMAN", "NONE"
    sla_minutes: int
    allowed: bool
    requires_human_alert: bool
    action_type: str             # "DISPATCH_OFFER", "QUOTATION", "OBJECTION_RESPONSE", "CADENCE_ADVANCE", "TERMINATE", "QUARANTINE", "HOLD"
    recommended_sku: Optional[str]
    suggested_copy: str
    next_state: str


class ScenarioPlaybookEngine:
    """
    Evaluates customer objections, inbound signals, call outcomes, and delivery statuses
    to return deterministic playbook instructions.
    """

    @classmethod
    def execute_playbook(
        cls,
        scenario_code: str,
        lead: Any,
        context: Optional[Dict[str, Any]] = None,
        db: Optional[Session] = None
    ) -> PlaybookAction:
        """
        Executes the playbook logic for a given scenario code.
        """
        ctx = context or {}
        comp_name = getattr(lead, "company", "your business") or "your business"
        contact_name = getattr(lead, "contact_name", "Sir/Madam") or "Sir/Madam"

        # SCENARIO A: Active Buying Enquiry
        if scenario_code == SCENARIO_A:
            return PlaybookAction(
                scenario_code=SCENARIO_A,
                scenario_title="Active Coffee Buying Enquiry",
                target_channel="PHONE",
                sla_minutes=15,
                allowed=True,
                requires_human_alert=False,
                action_type="DISPATCH_OFFER",
                recommended_sku="PB-VARIETY-4x50",
                suggested_copy=(
                    f"Hello {contact_name}, thank you for reaching out regarding coffee supply for {comp_name}. "
                    "We provide factory-direct freeze-dried pure Arabica and Robusta instant coffee. "
                    "You can evaluate our complete 4-flavour Variety Box directly at https://www.p3online.in/products/variety-box "
                    "with Cash on Delivery and free shipping."
                ),
                next_state="ACTIVE_ENQUIRY_RESPONDED"
            )

        # SCENARIO B: Interested Distributor / Wholesaler
        elif scenario_code == SCENARIO_B:
            return PlaybookAction(
                scenario_code=SCENARIO_B,
                scenario_title="Distributor / Wholesale Pricing Enquiry",
                target_channel="EMAIL",
                sla_minutes=60,
                allowed=True,
                requires_human_alert=False,
                action_type="QUOTATION",
                recommended_sku="MASTER_CARTON_PB-BLD-100",
                suggested_copy=(
                    f"Dear {contact_name},\n\n"
                    f"Thank you for your interest in partnering with Purity Beans for {comp_name}.\n\n"
                    "We offer distributor partners a 30% margin structure (25% trade discount + 5% cash/advance discount) "
                    "on factory-direct supply, backed by full GST invoicing for input tax credit.\n\n"
                    "Our master cartons start from 24/48 units with initial trial orders accepted on COD or advance payment.\n"
                    "Would you like us to share our official distributor rate card and catalogue?"
                ),
                next_state="DISTRIBUTOR_PRICING_SENT"
            )

        # SCENARIO C: Cafe Owner / Barista
        elif scenario_code == SCENARIO_C:
            return PlaybookAction(
                scenario_code=SCENARIO_C,
                scenario_title="Cafe / Barista Quality & Crema Evaluation",
                target_channel="PHONE",
                sla_minutes=120,
                allowed=True,
                requires_human_alert=False,
                action_type="DISPATCH_OFFER",
                recommended_sku="PB-GOURMET-DUO-50",
                suggested_copy=(
                    f"Hello {contact_name}, for {comp_name}'s beverage service, our Purista Freeze-Dried Robusta yields "
                    "intense aroma and thick crema without expensive espresso machine overhead. "
                    "You can test both our Pure Arabica and Robusta with the Gourmet Duo Mini at https://www.p3online.in/products/gourmet-duo-mini"
                ),
                next_state="HORECA_EVALUATION"
            )

        # SCENARIO D: Retailer / Supermarket
        elif scenario_code == SCENARIO_D:
            return PlaybookAction(
                scenario_code=SCENARIO_D,
                scenario_title="Retailer / Shelf Display Assortment",
                target_channel="EMAIL",
                sla_minutes=240,
                allowed=True,
                requires_human_alert=False,
                action_type="QUOTATION",
                recommended_sku="PB-VARIETY-4x50",
                suggested_copy=(
                    f"Dear {contact_name},\n\n"
                    f"Purity Beans premium glass jars offer high shelf appeal and rapid turnover for retail shelves at {comp_name}.\n"
                    "Our products are 100% barcode registered and FSSAI certified. "
                    "We support retailers with attractive trade margins and assorted trial cartons."
                ),
                next_state="RETAIL_OFFER_SENT"
            )

        # SCENARIO E: Corporate / Office Procurement
        elif scenario_code == SCENARIO_E:
            return PlaybookAction(
                scenario_code=SCENARIO_E,
                scenario_title="Corporate Office Pantry Supply",
                target_channel="EMAIL",
                sla_minutes=120,
                allowed=True,
                requires_human_alert=False,
                action_type="QUOTATION",
                recommended_sku="PB-BLD-100",
                suggested_copy=(
                    f"Dear {contact_name},\n\n"
                    f"We provide office pantries at {comp_name} with premium freeze-dried and agglomerated instant coffee, "
                    "delivering high employee satisfaction at factory-direct rates with 100% GST tax credit.\n\n"
                    "Our pantry trial packs allow your team to select their preferred blend before setting up scheduled monthly supply."
                ),
                next_state="CORPORATE_PROPOSAL_SENT"
            )

        # SCENARIO F: Price Objection ("Too Expensive")
        elif scenario_code == SCENARIO_F:
            return PlaybookAction(
                scenario_code=SCENARIO_F,
                scenario_title="Price Objection Handling",
                target_channel="EMAIL",
                sla_minutes=180,
                allowed=True,
                requires_human_alert=False,
                action_type="OBJECTION_RESPONSE",
                recommended_sku="PB-BLD-100",
                suggested_copy=(
                    f"Dear {contact_name},\n\n"
                    "We completely understand cost efficiency is crucial. Unlike mass-market brands blended with chicory, "
                    "Purity Beans freeze-dried and pure agglomerated granules are 100% pure coffee. "
                    "A single 100g jar yields 50 cups—working out to just ~₹6.50 per cup for our Bold pure coffee, "
                    "delivering far greater yield and zero bitter burnt taste.\n\n"
                    "We recommend evaluating our Bold 100g (₹329) for the optimal balance of strength and economy."
                ),
                next_state="PRICE_OBJECTION_HANDLED"
            )

        # SCENARIO G: Sample Requested
        elif scenario_code == SCENARIO_G:
            return PlaybookAction(
                scenario_code=SCENARIO_G,
                scenario_title="Sample Request Fulfillment",
                target_channel="EMAIL",
                sla_minutes=60,
                allowed=True,
                requires_human_alert=False,
                action_type="DISPATCH_OFFER",
                recommended_sku="PB-GOURMET-DUO-50",
                suggested_copy=(
                    f"Dear {contact_name},\n\n"
                    "We would be delighted for you to experience Purity Beans. To evaluate our coffee, "
                    "we offer our Gourmet Duo Mini (Purista + Purica) at a subsidized ₹570 with free shipping "
                    "and Cash on Delivery: https://www.p3online.in/products/gourmet-duo-mini\n\n"
                    "If you are a GST-registered business seeking a commercial carton evaluation, please reply with your "
                    "GSTIN and shipping address for our sample dispatch team."
                ),
                next_state="SAMPLE_OFFER_DISPATCHED"
            )

        # SCENARIO H: No Answer / Unreachable (Cadence Rule)
        elif scenario_code == SCENARIO_H:
            attempt_count = int(ctx.get("attempt_count", 1))
            if attempt_count >= 3:
                return PlaybookAction(
                    scenario_code=SCENARIO_H,
                    scenario_title="No Answer Max Retries Reached",
                    target_channel="NONE",
                    sla_minutes=0,
                    allowed=False,
                    requires_human_alert=False,
                    action_type="HOLD",
                    recommended_sku=None,
                    suggested_copy="Outreach paused for 30-day cooldown after 3 unsuccessful attempts.",
                    next_state="RESTING_COOLDOWN"
                )
            next_channel = "EMAIL" if attempt_count == 1 else "PHONE"
            return PlaybookAction(
                scenario_code=SCENARIO_H,
                scenario_title=f"No Answer Cadence (Attempt {attempt_count + 1})",
                target_channel=next_channel,
                sla_minutes=1440 * attempt_count,
                allowed=True,
                requires_human_alert=False,
                action_type="CADENCE_ADVANCE",
                recommended_sku=None,
                suggested_copy="Gentle follow-up regarding coffee requirements.",
                next_state=f"CADENCE_RETRY_{attempt_count + 1}"
            )

        # SCENARIO I: Not Interested
        elif scenario_code == SCENARIO_I:
            return PlaybookAction(
                scenario_code=SCENARIO_I,
                scenario_title="Lead Opt-Out / Not Interested",
                target_channel="NONE",
                sla_minutes=0,
                allowed=False,
                requires_human_alert=False,
                action_type="TERMINATE",
                recommended_sku=None,
                suggested_copy="Thank you for your time. We have removed you from future outreach.",
                next_state="OPTED_OUT_COOLDOWN"
            )

        # SCENARIO J: Wrong Contact / Wrong Number
        elif scenario_code == SCENARIO_J:
            return PlaybookAction(
                scenario_code=SCENARIO_J,
                scenario_title="Wrong Contact / Phone Quarantine",
                target_channel="NONE",
                sla_minutes=0,
                allowed=False,
                requires_human_alert=False,
                action_type="QUARANTINE",
                recommended_sku=None,
                suggested_copy="Number marked invalid and quarantined.",
                next_state="QUARANTINED_INVALID_PHONE"
            )

        # SCENARIO K: Budget Cycle Later
        elif scenario_code == SCENARIO_K:
            revival_date = ctx.get("revival_date", (datetime.utcnow() + timedelta(days=60)).strftime("%Y-%m-%d"))
            return PlaybookAction(
                scenario_code=SCENARIO_K,
                scenario_title="Future Budget Cycle Follow-up",
                target_channel="NONE",
                sla_minutes=0,
                allowed=False,
                requires_human_alert=False,
                action_type="HOLD",
                recommended_sku=None,
                suggested_copy=f"Sequence paused. Revival scheduled for {revival_date}.",
                next_state="BUDGET_CYCLE_SCHEDULED"
            )

        # SCENARIO L: Existing Customer
        elif scenario_code == SCENARIO_L:
            return PlaybookAction(
                scenario_code=SCENARIO_L,
                scenario_title="Existing Customer Account Management",
                target_channel="EMAIL",
                sla_minutes=1440,
                allowed=True,
                requires_human_alert=False,
                action_type="DISPATCH_OFFER",
                recommended_sku=None,
                suggested_copy=(
                    f"Hello {contact_name}, thank you for being a valued Purity Beans customer. "
                    "We wanted to ensure your coffee supply is running smoothly and offer priority replenishment."
                ),
                next_state="ACCOUNT_MANAGEMENT_ACTIVE"
            )

        # SCENARIO M: Bounced Email
        elif scenario_code == SCENARIO_M:
            return PlaybookAction(
                scenario_code=SCENARIO_M,
                scenario_title="Email Delivery Bounce",
                target_channel="NONE",
                sla_minutes=0,
                allowed=False,
                requires_human_alert=False,
                action_type="QUARANTINE",
                recommended_sku=None,
                suggested_copy="Email marked BOUNCED and quarantined from send queue.",
                next_state="EMAIL_BOUNCED"
            )

        # SCENARIO N: WhatsApp Message Failed
        elif scenario_code == SCENARIO_N:
            return PlaybookAction(
                scenario_code=SCENARIO_N,
                scenario_title="WhatsApp Undelivered",
                target_channel="EMAIL",
                sla_minutes=240,
                allowed=True,
                requires_human_alert=False,
                action_type="CADENCE_ADVANCE",
                recommended_sku=None,
                suggested_copy="Fallback to verified email following WhatsApp delivery failure.",
                next_state="WHATSAPP_FALLBACK_EMAIL"
            )

        # SCENARIO O: Human Callback Requested
        elif scenario_code == SCENARIO_O:
            return PlaybookAction(
                scenario_code=SCENARIO_O,
                scenario_title="Human Specialist Escalation",
                target_channel="HUMAN",
                sla_minutes=30,
                allowed=True,
                requires_human_alert=True,
                action_type="HOLD",
                recommended_sku=None,
                suggested_copy=(
                    f"ALERT: Lead #{getattr(lead, 'id', 'unknown')} ({comp_name}) requested human specialist callback. "
                    "All automated AI bots paused. Founder notification dispatched."
                ),
                next_state="HUMAN_INTERVENTION_REQUIRED"
            )

        # SCENARIO P: Order Placed / Converted
        elif scenario_code == SCENARIO_P:
            return PlaybookAction(
                scenario_code=SCENARIO_P,
                scenario_title="Paid Conversion Recorded",
                target_channel="NONE",
                sla_minutes=0,
                allowed=False,
                requires_human_alert=True,
                action_type="TERMINATE",
                recommended_sku=None,
                suggested_copy="Paid conversion verified. All outbound sales queues terminated immediately.",
                next_state="PAID_CONVERSION"
            )

        # Fallback default
        return PlaybookAction(
            scenario_code=scenario_code,
            scenario_title="Unknown Scenario",
            target_channel="NONE",
            sla_minutes=0,
            allowed=False,
            requires_human_alert=False,
            action_type="HOLD",
            recommended_sku=None,
            suggested_copy="Scenario not recognized.",
            next_state="UNKNOWN"
        )
