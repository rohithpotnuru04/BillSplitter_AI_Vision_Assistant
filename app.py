
import io
import base64
import json
import html
import os
import smtplib
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import streamlit as st
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

try:
    from supabase import create_client
except ImportError:
    create_client = None

from prompts import (
    SYSTEM_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
    RECEIPT_ANALYSIS_PROMPT,
    CALCULATION_PROMPT,
    SUMMARY_REQUEST_PROMPT,
    COMPARISON_SYSTEM_PROMPT,
    COMPARISON_REQUEST_PROMPT,
)

MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-3.5-flash")

st.set_page_config(
    page_title="BillSplit AI • Smart Expense Companion",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Models ----------

class ReceiptItem(BaseModel):
    name: str
    quantity: float = 1
    unit_price: float = 0
    line_total: float = 0
    notes: str = ""

class ReceiptData(BaseModel):
    merchant: str = "Unknown"
    currency: str = "INR"
    items: list[ReceiptItem] = Field(default_factory=list)
    subtotal: float = 0
    tax: float = 0
    service_charge: float = 0
    discount: float = 0
    tip: float = 0
    total: float = 0
    notes: str = ""

class Assignment(BaseModel):
    item_index: int
    quantity_number: int
    people: list[str]

class ChatAssignmentUpdate(BaseModel):
    item_index: int
    quantity_number: int
    people: list[str]

class ChatTurn(BaseModel):
    reply: str = "I can help with this bill. Could you clarify your question?"
    assignment_updates: list[ChatAssignmentUpdate] = Field(default_factory=list)
    needs_assignment_clarification: bool = False
    recalculate_required: bool = False

class BillCalculation(BaseModel):
    """Authoritative monetary result produced by the deterministic Python engine."""
    currency: str = "INR"
    person_totals: dict[str, float]
    person_item_breakdown: dict[str, list[str]]
    pre_tax_totals: dict[str, float] = Field(default_factory=dict)
    tax_breakdown: dict[str, float]
    charges_breakdown: dict[str, float] = Field(default_factory=dict)
    grand_total: float
    receipt_total: float = 0
    receipt_total_known: bool = True
    reconciliation_difference: float
    explanation: str

# ---------- Clients ----------

@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

@st.cache_resource
def get_supabase_client():
    if create_client is None:
        return None
    url = st.secrets.get("SUPABASE_URL", "").strip()
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        key = st.secrets.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        return None
    return create_client(url, key)

# ---------- State ----------

DEFAULTS = {
    "onboarded": False,
    "name": "",
    "email": "",
    "active_bill_id": "",
    "active_bill": None,
    "bills": [],
    "receipt": None,
    "receipt_bytes": None,
    "receipt_mime": "",
    "people": [],
    "assignments": {},
    "calculation": None,
    "review_confirmed": False,
    "chat_messages": [],
    "gemini_chat": None,
    "email_sent": False,
    "email_status": "",
    "save_status": "",
    "last_saved": "",
    "restore_checked": False,
    "new_bill_mode": False,
    "comparison_report": "",
    "comparison_bill_ids": [],
    "comparison_email_status": "",
    "comparison_chat_messages": [],
    "show_resume_choices": False,
    "pending_drafts": [],
    "add_person_input": "",
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ---------- Styling ----------

st.markdown("""
<style>
:root {
  --radius: 18px;
}
.block-container {max-width: 1250px; padding-top: 1.5rem; padding-bottom: 4rem;}
.hero {
  padding: 28px 30px;
  border-radius: 24px;
  background: linear-gradient(135deg, rgba(99,102,241,.14), rgba(14,165,233,.10));
  border: 1px solid rgba(99,102,241,.18);
  margin-bottom: 20px;
}
.hero h1 {margin:0 0 6px 0; font-size: 2.4rem;}
.hero p {margin:0; color: #64748b;}
.status {
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(34,197,94,.10);
  border: 1px solid rgba(34,197,94,.18);
  display:inline-block;
  font-size:.85rem;
}
.small-muted {color:#64748b; font-size:.9rem;}
.bill-card {
  padding: 18px;
  border-radius: 18px;
  border: 1px solid rgba(100,116,139,.18);
  margin-bottom: 10px;
  background: rgba(255,255,255,.55);
}

.person-chip {
  display:inline-block; padding:8px 14px; margin:4px 6px 4px 0;
  border-radius:999px; background:linear-gradient(135deg,#312e81,#155e75);
  color:#fff; border:1px solid rgba(125,211,252,.35); font-weight:600;
}
.chat-intro {
  border:1px solid rgba(99,102,241,.35); border-radius:18px;
  padding:18px 20px; margin:12px 0 22px 0;
  background:linear-gradient(135deg,rgba(99,102,241,.15),rgba(14,165,233,.10));
}
.chat-intro h3 {margin-top:0;}
[data-testid="stAppViewContainer"] {
  background:
    radial-gradient(ellipse at 8% 0%, rgba(79,70,229,.12), transparent 35%),
    radial-gradient(ellipse at 92% 10%, rgba(14,165,233,.09), transparent 32%);
}
[data-testid="stSidebar"] {background:linear-gradient(180deg,#202433,#171b28);}
.stButton > button, [data-testid="stFormSubmitButton"] > button {
  border-radius:12px; font-weight:600; min-height:42px;
  transition:transform .12s ease, box-shadow .12s ease;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] > button:hover {
  transform:translateY(-1px); box-shadow:0 8px 22px rgba(79,70,229,.16);
}
[data-testid="stMetric"] {
  padding:14px 16px; border:1px solid rgba(148,163,184,.18);
  border-radius:16px; background:rgba(30,41,59,.45);
}
[data-testid="stChatMessage"] {
  border:1px solid rgba(148,163,184,.18); border-radius:16px;
  padding:12px 16px; margin:8px 0;
}
.quantity-card {
  padding: 16px;
  border-radius: 16px;
  border: 1px solid rgba(100,116,139,.16);
  margin: 8px 0;
}
</style>
""", unsafe_allow_html=True)

# ---------- Helpers ----------

def money(value, currency="INR"):
    try:
        v = float(value)
    except Exception:
        v = 0.0
    symbol = "₹" if currency.upper() in {"INR", "₹"} else currency + " "
    return f"{symbol}{v:,.2f}"

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def clean_email(email):
    return "@" in email and "." in email.split("@")[-1]

def receipt_dict(receipt):
    return receipt.model_dump() if receipt else None

def assignments_for_prompt():
    rows = []
    if not st.session_state.receipt:
        return rows
    for i, item in enumerate(st.session_state.receipt.items):
        qty = max(1, int(round(item.quantity)))
        qmap = st.session_state.assignments.get(str(i), {})
        for q in range(1, qty + 1):
            rows.append({
                "item_index": i,
                "item": item.name,
                "quantity_number": q,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
                "people": qmap.get(str(q), []),
            })
    return rows

def all_quantities_assigned():
    receipt = st.session_state.receipt
    if not receipt or not receipt.items or not st.session_state.people:
        return False
    for i, item in enumerate(receipt.items):
        qty = max(1, int(round(item.quantity)))
        qmap = st.session_state.assignments.get(str(i), {})
        for q in range(1, qty + 1):
            if not qmap.get(str(q), []):
                return False
    return True

def progress_counts():
    total = 0
    done = 0
    if st.session_state.receipt:
        for i, item in enumerate(st.session_state.receipt.items):
            qty = max(1, int(round(item.quantity)))
            total += qty
            qmap = st.session_state.assignments.get(str(i), {})
            for q in range(1, qty + 1):
                if qmap.get(str(q), []):
                    done += 1
    return done, total

def build_bill_state():
    return {
        "name": st.session_state.name,
        "email": st.session_state.email,
        "receipt": receipt_dict(st.session_state.receipt),
        "receipt_image_b64": base64.b64encode(st.session_state.receipt_bytes).decode("ascii") if st.session_state.receipt_bytes else "",
        "receipt_mime": st.session_state.receipt_mime,
        "people": st.session_state.people,
        "assignments": st.session_state.assignments,
        "calculation": st.session_state.calculation.model_dump() if st.session_state.calculation else None,
        "review_confirmed": st.session_state.review_confirmed,
        "chat_messages": st.session_state.chat_messages,
        "status": "completed" if st.session_state.calculation else "draft",
        "updated_at": now_iso(),
    }

def apply_bill_state(state):
    st.session_state.name = state.get("name") or st.session_state.name
    st.session_state.email = state.get("email") or st.session_state.email
    st.session_state.onboarded = True
    receipt_data = state.get("receipt")
    st.session_state.receipt = ReceiptData.model_validate(receipt_data) if receipt_data else None
    st.session_state.people = state.get("people") or []
    st.session_state.assignments = state.get("assignments") or {}
    calc = state.get("calculation")
    st.session_state.calculation = BillCalculation.model_validate(calc) if calc else None
    st.session_state.review_confirmed = bool(state.get("review_confirmed", False))
    st.session_state.chat_messages = state.get("chat_messages") or []
    st.session_state.gemini_chat = None
    image_b64 = state.get("receipt_image_b64") or ""
    st.session_state.receipt_bytes = base64.b64decode(image_b64) if image_b64 else None
    st.session_state.receipt_mime = state.get("receipt_mime") or ""
    st.session_state.email_sent = False
    st.session_state.email_status = ""

def save_draft(force=False):
    """Persist current bill state. No money is calculated here."""
    if not st.session_state.active_bill_id:
        return False
    client = get_supabase_client()
    if client is None:
        return False
    payload = {
        "title": bill_title(),
        "state": build_bill_state(),
        "updated_at": now_iso(),
        "status": "completed" if st.session_state.calculation else "draft",
    }
    try:
        client.table("bill_drafts").update(payload).eq(
            "id", st.session_state.active_bill_id
        ).execute()
        st.session_state.last_saved = datetime.now().strftime("%I:%M:%S %p")
        st.session_state.save_status = "Saved"
        return True
    except Exception as exc:
        st.session_state.save_status = f"Save failed: {exc}"
        return False

def create_draft():
    client = get_supabase_client()
    if client is None:
        raise RuntimeError("Supabase is not configured. Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (recommended) to secrets.")
    payload = {
        "owner_email": st.session_state.email,
        "owner_name": st.session_state.name,
        "title": "New Bill",
        "status": "draft",
        "state": build_bill_state(),
        "updated_at": now_iso(),
    }
    result = client.table("bill_drafts").insert(payload).execute()
    row = result.data[0]
    st.session_state.active_bill_id = row["id"]
    st.session_state.last_saved = datetime.now().strftime("%I:%M:%S %p")
    st.session_state.save_status = "Saved"

def list_drafts():
    client = get_supabase_client()
    if client is None or not st.session_state.email:
        return []
    try:
        result = (
            client.table("bill_drafts")
            .select("id, title, status, updated_at, state")
            .eq("owner_email", st.session_state.email)
            .order("updated_at", desc=True)
            .limit(20)
            .execute()
        )
        return result.data or []
    except Exception:
        return []

def load_draft(draft_id):
    client = get_supabase_client()
    if client is None:
        return None
    result = client.table("bill_drafts").select("*").eq("id", draft_id).limit(1).execute()
    if not result.data:
        return None
    row = result.data[0]
    st.session_state.active_bill_id = row["id"]
    st.session_state.name = row.get("owner_name") or st.session_state.name
    st.session_state.email = row.get("owner_email") or st.session_state.email
    apply_bill_state(row.get("state") or {})
    st.session_state.restore_checked = True
    st.session_state.new_bill_mode = False
    st.session_state.comparison_report = ""
    st.session_state.comparison_bill_ids = []
    st.session_state.comparison_email_status = ""
    return row

def delete_draft(draft_id):
    client = get_supabase_client()
    if client:
        client.table("bill_drafts").delete().eq("id", draft_id).execute()

def analyze_receipt(image_bytes, mime_type):
    client = get_gemini_client()
    part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[part, RECEIPT_ANALYSIS_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ReceiptData,
            temperature=0.1,
        ),
    )
    return ReceiptData.model_validate_json(response.text)

def start_gemini_chat():
    if st.session_state.gemini_chat is not None:
        return st.session_state.gemini_chat
    client = get_gemini_client()
    chat = client.chats.create(
        model=MODEL_NAME,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    st.session_state.gemini_chat = chat
    return chat

def send_to_gemini(text_or_parts):
    chat = start_gemini_chat()
    return chat.send_message(text_or_parts).text

def authoritative_bill_context():
    return json.dumps({
        "receipt": receipt_dict(st.session_state.receipt),
        "participants": st.session_state.people,
        "assignments": assignments_for_prompt(),
        "current_calculation": st.session_state.calculation.model_dump() if st.session_state.calculation else None,
        "conversation": st.session_state.chat_messages,
    }, ensure_ascii=False, indent=2)

def restore_gemini_context():
    """Seed a newly created Gemini chat with saved bill state after a refresh/load."""
    if st.session_state.gemini_chat is None and st.session_state.receipt and st.session_state.calculation:
        chat = start_gemini_chat()
        chat.send_message(
            "RESTORE CURRENT BILL STATE. Do not answer the user. "
            "Treat this JSON as authoritative context for the current bill.\n\n"
            + authoritative_bill_context()
        )

def initialize_bill_chat():
    # The welcome/help panel is rendered above Receipt Vision, not inside the chat history.
    # Keeping it out of chat_messages prevents it being duplicated in the email transcript.
    return None

def calculation_request():
    """Return bill data for AI explanations/validation, not for arithmetic."""
    receipt = receipt_dict(st.session_state.receipt)
    payload = {
        "RECEIPT": receipt,
        "PARTICIPANTS": st.session_state.people,
        "QUANTITY_LEVEL_ASSIGNMENTS": assignments_for_prompt(),
    }
    return CALCULATION_PROMPT + "\n\nCURRENT BILL DATA:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _money_decimal(value):
    """Convert numeric input to Decimal without binary floating-point surprises."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _allocate_proportionally(total, bases):
    """Allocate a bill-level amount proportionally with cent-accurate rounding."""
    total = _money_decimal(total)
    positive = {p: _money_decimal(v) for p, v in bases.items()}
    denominator = sum(positive.values(), Decimal("0.00"))
    result = {p: Decimal("0.00") for p in positive}
    if denominator == 0:
        return result

    allocated = Decimal("0.00")
    ordered = list(positive.keys())
    for p in ordered[:-1]:
        share = (total * positive[p] / denominator).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        result[p] = share
        allocated += share
    if ordered:
        result[ordered[-1]] = total - allocated
    return result


def calculate_with_python():
    """
    Authoritative financial calculation.

    Gemini is used to understand the receipt and user language. Python performs
    all monetary arithmetic so repeated calculations are deterministic.
    """
    receipt = st.session_state.receipt
    people = list(dict.fromkeys(st.session_state.people))
    if not receipt or not receipt.items:
        raise ValueError("Upload and analyze a receipt before calculating.")
    if not people:
        raise ValueError("Add at least one participant.")
    if not all_quantities_assigned():
        raise ValueError("Every purchased quantity must be assigned before calculation.")

    currency = receipt.currency or "INR"
    pre_tax = {p: Decimal("0.00") for p in people}
    item_breakdown = {p: [] for p in people}

    # Allocate each purchased quantity independently. The line total is used
    # as the authoritative item amount; this handles receipts where unit price
    # and quantity have small rounding differences.
    for i, item in enumerate(receipt.items):
        qty = max(1, int(round(item.quantity)))
        qmap = st.session_state.assignments.get(str(i), {})
        line_total = _money_decimal(item.line_total)
        unit_amount = (line_total / Decimal(qty)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for q in range(1, qty + 1):
            selected = [p for p in qmap.get(str(q), []) if p in people]
            if not selected:
                raise ValueError(f"{item.name} #{q} is not assigned.")
            # Split this exact purchased quantity equally among its consumers.
            shares = _allocate_proportionally(
                unit_amount,
                {p: Decimal("1") for p in selected},
            )
            for p, amount in shares.items():
                pre_tax[p] += amount
                item_breakdown[p].append(
                    f"{item.name} #{q}: {money(amount, currency)}"
                    + (f" shared with {', '.join(x for x in selected if x != p)}" if len(selected) > 1 else "")
                )

    # If receipt subtotal differs from summed line totals, preserve the receipt
    # subtotal and distribute the difference proportionally across item shares.
    receipt_subtotal = _money_decimal(receipt.subtotal)
    line_sum = sum(pre_tax.values(), Decimal("0.00"))
    if receipt_subtotal != Decimal("0.00") and line_sum != receipt_subtotal:
        adjustment = receipt_subtotal - line_sum
        adj = _allocate_proportionally(adjustment, pre_tax)
        for p in people:
            pre_tax[p] += adj[p]

    # Bill-level amounts are allocated according to each person's pre-tax share.
    tax = _allocate_proportionally(_money_decimal(receipt.tax), pre_tax)
    service = _allocate_proportionally(_money_decimal(receipt.service_charge), pre_tax)
    tip = _allocate_proportionally(_money_decimal(receipt.tip), pre_tax)
    discount = _allocate_proportionally(_money_decimal(receipt.discount), pre_tax)

    totals = {}
    for p in people:
        totals[p] = (
            pre_tax[p] + tax[p] + service[p] + tip[p] - discount[p]
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    receipt_total_known = receipt.total is not None and float(receipt.total) > 0
    receipt_total = _money_decimal(receipt.total)
    calculated_total = sum(totals.values(), Decimal("0.00"))
    reconciliation_difference = (
        (calculated_total - receipt_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if receipt_total_known else Decimal("0.00")
    )

    # A clear explanation is generated from the exact values; Gemini can later
    # turn these facts into natural language without becoming the calculator.
    lines = [
        f"Calculated total from assigned items and charges: {money(calculated_total, currency)}.",
        (f"Receipt total: {money(receipt_total, currency)}; difference: {money(reconciliation_difference, currency)}."
         if receipt_total_known else "Receipt final total was not detected, so reconciliation cannot yet be verified."),
        "Tax is allocated proportionally to each person's pre-tax share.",
    ]
    for p in people:
        lines.append(
            f"{p}: pre-tax {money(pre_tax[p], currency)}, "
            f"tax {money(tax[p], currency)}, "
            f"service charge {money(service[p], currency)}, "
            f"tip {money(tip[p], currency)}, "
            f"discount {money(discount[p], currency)}, "
            f"final {money(totals[p], currency)}."
        )

    return BillCalculation(
        currency=currency,
        person_totals={p: float(totals[p]) for p in people},
        person_item_breakdown=item_breakdown,
        pre_tax_totals={p: float(pre_tax[p]) for p in people},
        tax_breakdown={p: float(tax[p]) for p in people},
        charges_breakdown={
            "service_charge_total": float(_money_decimal(receipt.service_charge)),
            "tip_total": float(_money_decimal(receipt.tip)),
            "discount_total": float(_money_decimal(receipt.discount)),
        },
        grand_total=float(calculated_total),
        receipt_total=float(receipt_total),
        receipt_total_known=receipt_total_known,
        reconciliation_difference=float(reconciliation_difference),
        explanation="\n".join(lines),
    )


def send_email(to_address, subject, body):
    sender = st.secrets["GMAIL_ADDRESS"]
    password = st.secrets["GMAIL_APP_PASSWORD"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_address
    msg.attach(MIMEText(body, "plain", "utf-8"))
    # Convert headings and bullet lines into readable HTML instead of sending raw Markdown/JSON.
    import re
    html_lines = []
    in_list = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if set(line) <= {"=", "-"} and len(line) > 3:
            continue
        escaped_line = html.escape(line)
        escaped_line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped_line)
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<h2>" + html.escape(line.lstrip("# ").strip()) + "</h2>")
        elif line.startswith(("- ", "• ")):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append("<li>" + escaped_line[2:].strip() + "</li>")
        elif line.isupper() and len(line) < 70:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<h3>" + escaped_line + "</h3>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<p>" + escaped_line + "</p>")
    if in_list:
        html_lines.append("</ul>")
    html_body = (
        "<html><body style='font-family:Arial,sans-serif;line-height:1.6;color:#172033;background:#f8fafc'>"
        "<div style='max-width:760px;margin:24px auto;padding:28px;background:#fff;border-radius:16px'>"
        + "".join(html_lines) +
        "<hr><p style='color:#64748b;font-size:12px'>Generated by BillSplit AI</p></div></body></html>"
    )
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.send_message(msg)

def conversation_insights_for_email():
    """Summarize only the saved bill-related conversation; exact money comes from Python."""
    messages = st.session_state.chat_messages or []
    if not messages:
        return "No additional chatbot questions were asked for this bill."
    client = get_gemini_client()
    prompt = (
        "Summarize the following bill-related conversation for an email in 2-5 concise bullets. "
        "Mention useful questions, explanations, and assignment corrections. Do not output JSON. "
        "Do not invent facts or change any amount. If there were no substantive exchanges, say so.\n\n"
        + json.dumps(messages, ensure_ascii=False, indent=2)
    )
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        return (response.text or "").strip() or "No additional conversation summary available."
    except Exception:
        # Email still works if Gemini's conversation-summary request is temporarily unavailable.
        return "\\n".join(
            f"- {m.get('role', 'message').title()}: {m.get('content', '')}"
            for m in messages[-12:]
        )


def summary_to_email():
    """Build a reliable, structured email from authoritative Python values plus AI conversation notes."""
    receipt = st.session_state.receipt
    calc = st.session_state.calculation
    if not receipt or not calc:
        raise ValueError("Analyze a receipt and calculate the bill before emailing its summary.")

    conversation_summary = conversation_insights_for_email()
    currency = receipt.currency or "INR"
    lines = [
        "BILLSPLIT AI — BILL SUMMARY",
        "=" * 32,
        "",
        f"Merchant: {receipt.merchant or 'Unknown'}",
        f"Bill owner: {st.session_state.name}",
        f"Receipt total: {money(receipt.total, currency) if receipt.total > 0 else 'Not visible / not confirmed'}",
        f"Calculated total from assigned items and charges: {money(calc.grand_total, currency)}",
        "",
        "1. RECEIPT ITEMS",
        "-" * 18,
    ]
    for item in receipt.items:
        lines.append(
            f"• {item.name} — quantity {item.quantity:g}; "
            f"unit price {money(item.unit_price, currency)}; "
            f"line total {money(item.line_total, currency)}"
        )
    lines += ["", "2. QUANTITY-LEVEL ASSIGNMENTS", "-" * 30]
    for row in assignments_for_prompt():
        lines.append(
            f"• {row['item']} #{row['quantity_number']}: "
            + (", ".join(row["people"]) if row["people"] else "Unassigned")
        )
    lines += ["", "3. INDIVIDUAL BREAKDOWN", "-" * 22]
    for person in st.session_state.people:
        lines += [
            "",
            f"{person}: FINAL AMOUNT {money(calc.person_totals.get(person, 0), currency)}",
            f"  Pre-tax share: {money(calc.pre_tax_totals.get(person, 0), currency)}",
            f"  Tax share: {money(calc.tax_breakdown.get(person, 0), currency)}",
        ]
        for item_line in calc.person_item_breakdown.get(person, []):
            lines.append(f"  - {item_line}")
        if calc.charges_breakdown:
            lines.append(
                f"  Bill-level charges: service {money(calc.charges_breakdown.get('service_charge_total', 0), currency)}, "
                f"tip {money(calc.charges_breakdown.get('tip_total', 0), currency)}, "
                f"discount {money(calc.charges_breakdown.get('discount_total', 0), currency)} (allocated proportionally)"
            )
    lines += ["", "4. RECONCILIATION", "-" * 17]
    if not calc.receipt_total_known:
        lines.append("The receipt's final total was not readable/confirmed. The individual shares are calculated from the extracted items and charges, but the total still needs receipt verification.")
    elif abs(calc.reconciliation_difference) <= 0.01:
        lines.append(f"Reconciled: individual totals equal the receipt total ({money(calc.receipt_total, currency)}).")
    else:
        lines.append(
            f"Needs review: calculated total {money(calc.grand_total, currency)} differs from receipt total "
            f"{money(calc.receipt_total, currency)} by {money(calc.reconciliation_difference, currency)}. "
            "Please verify the receipt values before payment."
        )
    lines += ["", "5. CHAT CONVERSATION SUMMARY", "-" * 28, conversation_summary, "", "Generated by BillSplit AI."]
    return "\n".join(lines)


def comparison_chat_reply(user_question, selected_ids):
    rows = [d for d in list_drafts() if d.get("id") in selected_ids]
    if len(rows) < 2:
        raise ValueError("Select at least two saved bills first.")
    selected = []
    for row in rows:
        state = row.get("state") or {}
        if not state.get("receipt") or not state.get("calculation"):
            continue
        selected.append({
            "title": row.get("title"),
            "receipt": state.get("receipt"),
            "people": state.get("people") or [],
            "assignments": state.get("assignments") or {},
            "calculation": state.get("calculation"),
        })
    if len(selected) < 2:
        raise ValueError("Both selected bills must have completed calculations.")
    prompt = (
        "You are a BillSplit AI comparison-only assistant. Answer only questions comparing the supplied bills. "
        "For questions unrelated to these bills, politely say you can only answer about the selected bills. "
        "Use the supplied calculated values as exact. Never change any bill. Do not return JSON.\n\n"
        "SELECTED BILLS (read-only):\n" + json.dumps(selected, ensure_ascii=False, indent=2)
        + "\n\nUSER QUESTION: " + user_question
    )
    response = get_gemini_client().models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=COMPARISON_SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    return (response.text or "").strip() or "I couldn't generate a comparison answer. Please try again."


def compare_bills_with_gemini(rows):
    """Generate a read-only AI comparison from saved, Python-calculated bill results."""
    if len(rows) < 2:
        raise ValueError("Select at least two completed bills to compare.")

    selected = []
    for row in rows:
        state = row.get("state") or {}
        receipt = state.get("receipt")
        calculation = state.get("calculation")
        if not receipt or not calculation:
            continue
        selected.append({
            "bill_id": row.get("id"),
            "title": row.get("title") or receipt.get("merchant") or "Bill",
            "updated_at": row.get("updated_at"),
            "receipt": receipt,
            "participants": state.get("people") or [],
            "quantity_level_assignments": state.get("assignments") or {},
            "calculation": calculation,
        })

    if len(selected) < 2:
        raise ValueError("At least two selected bills must be completed and calculated.")

    client = get_gemini_client()
    prompt = (
        COMPARISON_REQUEST_PROMPT
        + "\n\nSELECTED BILLS FOR COMPARISON (READ ONLY):\n"
        + json.dumps(selected, ensure_ascii=False, indent=2)
    )
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=COMPARISON_SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    return response.text.strip()

def comparison_to_email():
    if not st.session_state.comparison_report:
        raise ValueError("Generate a comparison report first.")
    conversation = st.session_state.comparison_chat_messages or []
    chat_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in conversation
    ) if conversation else "No follow-up comparison questions were asked."
    return (
        "AI BILL COMPARISON REPORT\n"
        "=========================\n\n"
        + st.session_state.comparison_report
        + "\n\nCOMPARISON CHAT\n----------------\n"
        + chat_text
        + "\n\nGenerated by BillSplit AI."
    )

def bill_title():
    if st.session_state.receipt:
        return st.session_state.receipt.merchant
    return "New Bill"

def reset_current_to_new():
    st.session_state.onboarded = True
    st.session_state.active_bill_id = ""
    st.session_state.receipt = None
    st.session_state.receipt_bytes = None
    st.session_state.receipt_mime = ""
    st.session_state.people = [st.session_state.name] if st.session_state.name else []
    st.session_state.assignments = {}
    st.session_state.calculation = None
    st.session_state.review_confirmed = False
    st.session_state.chat_messages = []
    st.session_state.gemini_chat = None
    st.session_state.email_sent = False
    st.session_state.email_status = ""
    st.session_state.save_status = ""
    st.session_state.last_saved = ""
    st.session_state.new_bill_mode = True
    st.session_state.comparison_report = ""
    st.session_state.comparison_bill_ids = []
    st.session_state.comparison_email_status = ""
    st.session_state.comparison_chat_messages = []
    st.session_state.add_person_input = ""

def add_person_callback():
    """Add a participant and clear the input before the next render."""
    person = (st.session_state.get("add_person_input") or "").strip()
    if not person:
        st.session_state["add_person_feedback"] = "Enter a name first."
        return
    if person.casefold() in {p.casefold() for p in st.session_state.people}:
        st.session_state["add_person_feedback"] = "That person is already in this bill."
        st.session_state["add_person_input"] = ""
        return
    st.session_state.people.append(person)
    st.session_state["add_person_input"] = ""
    st.session_state["add_person_feedback"] = f"Added {person}."
    save_draft()


def remove_person(person):
    """Remove a participant from all assignments; affected quantities must be reassigned."""
    st.session_state.people = [p for p in st.session_state.people if p != person]
    for qmap in st.session_state.assignments.values():
        for quantity_key, assigned in list(qmap.items()):
            qmap[quantity_key] = [p for p in assigned if p != person]
    st.session_state.calculation = None
    st.session_state.review_confirmed = False
    st.session_state.chat_messages = []
    save_draft()


# ---------- Onboarding ----------

if st.session_state.show_resume_choices:
    st.markdown("""
    <div class="hero">
      <h1>🧾 Welcome back to BillSplit AI</h1>
      <p>Choose a saved bill to continue, or create a new one.</p>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"Signed in as {st.session_state.name} · {st.session_state.email}")
    pending = st.session_state.pending_drafts or []
    if pending:
        for row in pending:
            title = row.get("title") or "Untitled bill"
            status = "Completed" if row.get("status") == "completed" else "Draft"
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{title}** · {status} · {(row.get('updated_at') or '')[:10]}")
            if c2.button("Continue", key=f"resume_onboarding_{row['id']}", use_container_width=True):
                load_draft(row["id"])
                st.session_state.show_resume_choices = False
                st.session_state.pending_drafts = []
                st.rerun()
    if st.button("＋ Create a new bill", type="primary", use_container_width=True):
        st.session_state.show_resume_choices = False
        st.session_state.pending_drafts = []
        reset_current_to_new()
        create_draft()
        st.rerun()
    st.stop()

if not st.session_state.onboarded:
    st.markdown("""
    <div class="hero">
      <h1>🧾 BillSplit AI</h1>
      <p>See the bill. Understand the bill. Split the bill — with AI.</p>
    </div>
    """, unsafe_allow_html=True)
    st.info("Upload a receipt, assign every quantity to the people who consumed it, review the bill, then chat with your AI bill assistant.")
    with st.form("onboarding"):
        name = st.text_input("Your name", placeholder="e.g., Alex")
        email = st.text_input("Email address", placeholder="you@gmail.com")
        submitted = st.form_submit_button("Start BillSplit AI →", type="primary", use_container_width=True)
        if submitted:
            if not name.strip() or not clean_email(email.strip()):
                st.error("Please enter a valid name and email address.")
            else:
                st.session_state.name = name.strip()
                st.session_state.email = email.strip()
                st.session_state.people = [name.strip()]
                try:
                    client = get_supabase_client()
                    existing = (
                        client.table("bill_drafts").select("*")
                        .eq("owner_email", st.session_state.email)
                        .order("updated_at", desc=True).limit(20).execute().data or []
                    ) if client else []
                    if existing:
                        st.session_state.pending_drafts = existing
                        st.session_state.show_resume_choices = True
                    else:
                        st.session_state.onboarded = True
                        create_draft()
                except Exception as exc:
                    st.session_state.onboarded = True
                    try:
                        create_draft()
                    except Exception:
                        st.warning(f"Cloud saving could not start: {exc}. Check Supabase secrets and schema.")
                st.rerun()
    st.stop()

# ---------- Restore / dashboard ----------

drafts = list_drafts()
with st.sidebar:
    st.markdown("## 🧾 BillSplit AI")
    st.caption(f"Signed in as **{st.session_state.name}**")
    st.caption(st.session_state.email)
    if st.session_state.save_status:
        st.success(f"💾 {st.session_state.save_status}" + (f" • {st.session_state.last_saved}" if st.session_state.last_saved else ""))

    st.divider()
    st.markdown("### My Bills")
    if drafts:
        for d in drafts:
            title = d.get("title") or "Untitled Bill"
            status = "✅" if d.get("status") == "completed" else "🟡"
            if st.button(f"{status} {title}", key=f"open_{d['id']}", use_container_width=True):
                load_draft(d["id"])
                st.rerun()
    else:
        st.caption("No saved bills yet.")

    if st.button("＋ New Bill", type="primary", use_container_width=True):
        reset_current_to_new()
        try:
            create_draft()
        except Exception:
            pass
        st.rerun()

    st.divider()
    if st.session_state.active_bill_id and st.button("🗑️ Delete current draft", use_container_width=True):
        try:
            delete_draft(st.session_state.active_bill_id)
        except Exception:
            pass
        reset_current_to_new()
        try:
            create_draft()
        except Exception:
            pass
        st.rerun()

    st.caption("Drafts are saved automatically after major actions. Your bill can be resumed after a refresh.")

# ---------- Header ----------

st.markdown(f"""
<div class="hero">
  <h1>🧾 BillSplit AI</h1>
  <p>AI Vision • Accurate splitting • Bill-specific chat • Expense insights</p>
</div>
""", unsafe_allow_html=True)

if st.session_state.receipt:
    cols = st.columns([4, 1, 1])
    with cols[0]:
        st.subheader(f"🧾 {bill_title()}")
    with cols[1]:
        done, total = progress_counts()
        st.metric("Progress", f"{done}/{total}")
    with cols[2]:
        if st.session_state.calculation:
            st.success("Calculated")
        else:
            st.warning("Draft")
else:
    st.subheader("Start a new bill")


# ---------- AI comparison dashboard ----------

completed_drafts = [
    d for d in drafts
    if d.get("status") == "completed"
    and (d.get("state") or {}).get("receipt")
    and (d.get("state") or {}).get("calculation")
]

with st.expander("📊 AI Compare Bills — read-only insights across your saved bills", expanded=False):
    st.caption(
        "Select two or more completed bills. Gemini will compare only the bills you choose. "
        "The original bills, assignments and calculations are never modified."
    )

    if len(completed_drafts) < 2:
        st.info("Complete at least two bills to unlock AI comparison.")
    else:
        compare_options = {}
        for d in completed_drafts:
            state = d.get("state") or {}
            receipt = state.get("receipt") or {}
            title = d.get("title") or receipt.get("merchant") or "Untitled Bill"
            updated = (d.get("updated_at") or "")[:10]
            label = f"{title} • {updated} • {str(d['id'])[:8]}"
            compare_options[label] = d["id"]

        selected_labels = st.multiselect(
            "Choose bills to compare",
            options=list(compare_options.keys()),
            key="compare_bill_selector",
            help="Choose 2 or more completed bills. Comparison is read-only.",
        )
        selected_ids_now = [compare_options[label] for label in selected_labels]
        if st.session_state.comparison_bill_ids and set(selected_ids_now) != set(st.session_state.comparison_bill_ids):
            st.session_state.comparison_report = ""
            st.session_state.comparison_bill_ids = []
            st.session_state.comparison_chat_messages = []
            st.session_state.comparison_email_status = ""

        compare_col, email_col = st.columns(2)
        with compare_col:
            if st.button(
                "🔍 Generate AI comparison",
                type="primary",
                use_container_width=True,
                disabled=len(selected_labels) < 2,
            ):
                selected_ids = [compare_options[label] for label in selected_labels]
                selected_rows = [d for d in completed_drafts if d["id"] in selected_ids]
                try:
                    with st.spinner("Gemini is comparing the selected bills..."):
                        report = compare_bills_with_gemini(selected_rows)
                    st.session_state.comparison_report = report
                    st.session_state.comparison_bill_ids = selected_ids
                    st.session_state.comparison_chat_messages = []
                    st.session_state.comparison_email_status = ""
                except Exception as exc:
                    st.error(f"Comparison failed: {exc}")

        if st.session_state.comparison_report:
            st.markdown("#### 🤖 AI Comparison Report")
            st.info(
                f"Comparing {len(st.session_state.comparison_bill_ids)} selected bills. "
                "This analysis is read-only and does not change any saved bill."
            )
            st.markdown(st.session_state.comparison_report)

            with email_col:
                if st.button("📧 Email comparison report", use_container_width=True):
                    try:
                        with st.spinner("Preparing the comparison email..."):
                            body = comparison_to_email()
                        send_email(
                            st.session_state.email,
                            "📊 BillSplit AI — Bill Comparison Report",
                            body,
                        )
                        st.session_state.comparison_email_status = (
                            f"Comparison report sent to {st.session_state.email}"
                        )
                    except Exception as exc:
                        st.error(f"Comparison email failed: {exc}")

            if st.session_state.comparison_email_status:
                st.success(st.session_state.comparison_email_status)

            st.markdown("#### 💬 Ask about these selected bills")
            for msg in st.session_state.comparison_chat_messages:
                with st.chat_message(msg["role"], avatar=("🧑" if msg["role"] == "user" else "🤖")):
                    st.markdown(msg["content"])
            with st.form("comparison_chat_form", clear_on_submit=True):
                compare_question = st.text_input(
                    "Ask only about the selected bills",
                    placeholder="e.g., Which item cost the most across these bills?",
                )
                compare_submit = st.form_submit_button("Ask comparison assistant")
            if compare_submit and compare_question.strip():
                st.session_state.comparison_chat_messages.append({"role": "user", "content": compare_question.strip()})
                try:
                    answer = comparison_chat_reply(compare_question.strip(), st.session_state.comparison_bill_ids)
                    st.session_state.comparison_chat_messages.append({"role": "assistant", "content": answer})
                    st.rerun()
                except Exception:
                    st.session_state.comparison_chat_messages.append({"role": "assistant", "content": "I couldn't answer that comparison question. Please generate a comparison again and retry."})

# ---------- Welcome / receipt upload ----------

st.markdown(f"""
<div class="chat-intro">
  <h3>💬 Welcome to BillSplit AI, {html.escape(st.session_state.name)}!</h3>
  <p>Upload a receipt, verify the extracted items, assign each quantity to the people who consumed it, and review the exact shares.</p>
  <p>Ask bill-specific questions after calculating the bill. Compare Bills has its own separate, read-only assistant.</p>
</div>
""", unsafe_allow_html=True)

st.markdown("### 1. 📸 Receipt Vision")
uploaded = st.file_uploader(
    "Upload a clear receipt image",
    type=["jpg", "jpeg", "png", "webp"],
    key="receipt_uploader",
)
if uploaded:
    new_bytes = uploaded.getvalue()
    if st.session_state.receipt_bytes != new_bytes:
        st.session_state.receipt_bytes = new_bytes
        st.session_state.receipt_mime = uploaded.type
        st.session_state.receipt = None
        st.session_state.assignments = {}
        st.session_state.calculation = None
        st.session_state.review_confirmed = False
        st.session_state.gemini_chat = None
        st.session_state.chat_messages = []
        st.session_state.email_sent = False
        st.session_state.email_status = ""
        try:
            save_draft()
        except Exception:
            pass
    st.image(new_bytes, caption="Receipt preview", width=520)
    if st.button("🔍 Analyze receipt with Gemini Vision", type="primary", use_container_width=True):
        try:
            with st.spinner("Reading items, quantities, prices, tax and total..."):
                receipt = analyze_receipt(new_bytes, uploaded.type)
            st.session_state.receipt = receipt
            st.session_state.people = [st.session_state.name]
            st.session_state.assignments = {}
            st.session_state.calculation = None
            st.session_state.review_confirmed = False
            st.session_state.gemini_chat = None
            st.session_state.chat_messages = []
            st.session_state.email_sent = False
            st.session_state.email_status = ""
            create_or_update = save_draft()
            st.success("Receipt understood. Please verify the extracted values.")
            st.rerun()
        except Exception as exc:
            st.error(f"Receipt analysis failed: {exc}")
elif st.session_state.receipt_bytes:
    st.image(st.session_state.receipt_bytes, caption="Saved receipt preview", width=520)

# ---------- Receipt review ----------

if st.session_state.receipt:
    receipt = st.session_state.receipt
    st.markdown("### 2. 🔎 Verify the receipt")
    st.caption("Gemini extracted these values. You can correct OCR/vision mistakes before assigning people.")
    st.dataframe(
        [
            {
                "Item": i.name,
                "Quantity": i.quantity,
                "Unit price": money(i.unit_price, receipt.currency),
                "Line total": money(i.line_total, receipt.currency),
                "Note": i.notes,
            }
            for i in receipt.items
        ],
        use_container_width=True,
        hide_index=True,
    )
    m = st.columns(5)
    m[0].metric("Subtotal", money(receipt.subtotal, receipt.currency))
    m[1].metric("Tax", money(receipt.tax, receipt.currency))
    m[2].metric("Service", money(receipt.service_charge, receipt.currency))
    m[3].metric("Discount", money(receipt.discount, receipt.currency))
    m[4].metric("Total", money(receipt.total, receipt.currency))
    with st.expander("✏️ Correct receipt values if Gemini missed anything", expanded=(receipt.total <= 0)):
        st.caption("If the bottom of the receipt was cut off or a tax/total was misread, correct it here before finalizing.")
        with st.form(f"receipt_corrections_{st.session_state.active_bill_id}"):
            corrected_merchant = st.text_input("Merchant", value=receipt.merchant or "Unknown", key=f"receipt_merchant_{st.session_state.active_bill_id}")
            edit_cols = st.columns(3)
            corrected_subtotal = edit_cols[0].number_input("Subtotal", min_value=0.0, value=float(receipt.subtotal), step=0.5, format="%.2f")
            corrected_tax = edit_cols[1].number_input("Total tax / GST", min_value=0.0, value=float(receipt.tax), step=0.5, format="%.2f")
            corrected_service = edit_cols[2].number_input("Service charge", min_value=0.0, value=float(receipt.service_charge), step=0.5, format="%.2f")
            edit_cols2 = st.columns(3)
            corrected_discount = edit_cols2[0].number_input("Discount", min_value=0.0, value=float(receipt.discount), step=0.5, format="%.2f")
            corrected_tip = edit_cols2[1].number_input("Tip", min_value=0.0, value=float(receipt.tip), step=0.5, format="%.2f")
            corrected_total = edit_cols2[2].number_input("Final receipt total", min_value=0.0, value=float(receipt.total), step=0.5, format="%.2f")
            receipt_correction_submit = st.form_submit_button("Save receipt corrections", type="primary")
            if receipt_correction_submit:
                st.session_state.receipt = receipt.model_copy(update={
                    "merchant": corrected_merchant.strip() or "Unknown",
                    "subtotal": corrected_subtotal,
                    "tax": corrected_tax,
                    "service_charge": corrected_service,
                    "discount": corrected_discount,
                    "tip": corrected_tip,
                    "total": corrected_total,
                })
                st.session_state.calculation = None
                st.session_state.review_confirmed = False
                save_draft()
                st.success("Receipt values saved. Confirm assignments and calculate again.")
                st.rerun()
    if receipt.notes:
        st.warning(receipt.notes)

    # ---------- Participants ----------

    st.markdown("### 3. 👥 People in this bill")
    st.write("The first person is the bill owner. Add everyone who may have consumed something.")
    add_col, button_col = st.columns([4, 1])
    with add_col:
        st.text_input(
            "Name", placeholder="Enter another person's name",
            label_visibility="collapsed", key="add_person_input",
        )
    with button_col:
        st.button("＋ Add", use_container_width=True, on_click=add_person_callback)
    if st.session_state.get("add_person_feedback"):
        st.caption(st.session_state["add_person_feedback"])

    if st.session_state.people:
        person_cols = st.columns(min(4, len(st.session_state.people)))
        for person_index, person in enumerate(list(st.session_state.people)):
            with person_cols[person_index % len(person_cols)]:
                with st.container(border=True):
                    st.markdown(f"👤 **{person}**")
                    if person != st.session_state.name and st.button("Remove", key=f"remove_person_{st.session_state.active_bill_id}_{person}", use_container_width=True):
                        remove_person(person)
                        st.rerun()
    if st.button("💾 Save people", use_container_width=True):
        save_draft()
        st.success("People saved.")

    # ---------- Quantity assignments ----------

    st.markdown("### 4. ☑️ Assign every quantity")
    st.caption("Each quantity is independent. The same item can be shared by different groups for different quantities.")

    assignments_changed = False
    for i, item in enumerate(receipt.items):
        qty = max(1, int(round(item.quantity)))
        with st.container(border=True):
            st.markdown(f"**{item.name}**  ·  {qty} × {money(item.unit_price, receipt.currency)}")
            qmap = st.session_state.assignments.setdefault(str(i), {})
            for q in range(1, qty + 1):
                current = qmap.get(str(q), [])
                selected = st.multiselect(
                    f"Quantity {q} — select everyone who consumed this quantity",
                    options=st.session_state.people,
                    default=current,
                    key=f"assign_{st.session_state.active_bill_id}_{i}_{q}",
                )
                if selected != current:
                    assignments_changed = True
                qmap[str(q)] = selected
                if selected:
                    st.success("Assigned: " + ", ".join(selected))
                else:
                    st.warning("Unassigned — choose at least one person.")
            st.session_state.assignments[str(i)] = qmap

    if assignments_changed:
        save_draft()

    if st.button("💾 Save assignments", use_container_width=True):
        save_draft()
        st.success("Assignments saved.")

    # ---------- Final review ----------

    done, total = progress_counts()
    st.markdown("### 5. 🧠 Final review")
    st.progress(done / total if total else 0)
    st.write(f"**{done} of {total} quantities assigned.**")

    if not all_quantities_assigned():
        st.warning("Complete every quantity assignment before the AI calculation can be finalized.")
        st.session_state.review_confirmed = False
    else:
        st.success("Every purchased quantity has at least one participant.")
        st.caption("Gemini understands the receipt and your instructions; Python performs the exact monetary arithmetic and proportional tax allocation.")
        with st.expander("Preview all assignments", expanded=True):
            for row in assignments_for_prompt():
                st.write(
                    f"**{row['item']} #{row['quantity_number']}** — "
                    f"{', '.join(row['people'])}"
                )
        if st.button("✅ Confirm assignments & calculate accurately", type="primary", use_container_width=True):
            try:
                st.session_state.review_confirmed = True
                with st.spinner("Calculating item shares, proportional tax and final amounts..."):
                    calc = calculate_with_python()
                st.session_state.calculation = calc
                st.session_state.chat_messages = []
                st.session_state.gemini_chat = None
                initialize_bill_chat()
                save_draft()
                if not calc.receipt_total_known:
                    st.warning("The receipt's final total could not be read. Shares are calculated from extracted items and charges; verify the printed total before payment.")
                elif abs(calc.reconciliation_difference) <= 0.01:
                    st.success("The individual shares match the receipt total. Your bill assistant is ready.")
                else:
                    st.warning(
                        f"The calculated total differs from the receipt total by "
                        f"{money(calc.reconciliation_difference, calc.currency)}. "
                        "This usually means a receipt value is missing or was read incorrectly. Check the subtotal, tax, charges, discount and final total."
                    )
                st.rerun()
            except Exception as exc:
                st.session_state.review_confirmed = False
                st.error(f"Calculation failed: {exc}")

    # ---------- Results ----------

    if st.session_state.calculation:
        calc = st.session_state.calculation
        st.markdown("### 6. 💰 Your final bill")
        if not calc.receipt_total_known:
            st.warning("Receipt total not confirmed: the final printed total was not readable. Verify it before paying.")
        elif abs(calc.reconciliation_difference) > 0.01:
            st.warning(
                f"Receipt check needs attention: calculated {money(calc.grand_total, calc.currency)} vs "
                f"receipt {money(calc.receipt_total, calc.currency)} (difference {money(calc.reconciliation_difference, calc.currency)}). "
                "This is a receipt-data check, not an assignment error by itself."
            )
        else:
            st.success(f"✓ Individual shares match the receipt total: {money(calc.grand_total, calc.currency)}")

        cols = st.columns(min(4, max(1, len(calc.person_totals))))
        for idx, (person, amount) in enumerate(calc.person_totals.items()):
            cols[idx % len(cols)].metric(person, money(amount, calc.currency))

        st.markdown("#### How each amount was calculated")
        st.caption("Exact monetary arithmetic is performed by Python; Gemini is used for receipt understanding and conversational explanations.")
        for person in st.session_state.people:
            with st.container(border=True):
                st.markdown(f"**{person} — {money(calc.person_totals.get(person, 0), calc.currency)}**")
                for line in calc.person_item_breakdown.get(person, []):
                    st.write("• " + line)
                st.write(f"Tax allocated: **{money(calc.tax_breakdown.get(person, 0), calc.currency)}**")
                if calc.charges_breakdown:
                    st.write(f"Other charges/adjustments: **{money(calc.charges_breakdown.get(person, 0), calc.currency)}**")

        # ---------- Bill chat ----------

        st.markdown("### 7. 💬 Ask your BillSplit AI")
        st.caption("Ask about this bill, individual shares, tax, items, or corrections. If the receipt total is unreadable, the app will explain that verification is still needed.")

        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"], avatar=("🧑" if msg["role"] == "user" else "🤖")):
                st.markdown(msg["content"])

        user_q = st.chat_input(
            "Ask about this bill, your share, tax, items, or corrections…",
            key=f"chat_{st.session_state.active_bill_id}",
        )
        if user_q:
            st.session_state.chat_messages.append({"role": "user", "content": user_q})
            try:
                chat_prompt = (
                    "CURRENT AUTHORITATIVE BILL STATE (JSON):\n" + authoritative_bill_context()
                    + "\n\nRECENT CONVERSATION (JSON):\n"
                    + json.dumps(st.session_state.chat_messages[-12:], ensure_ascii=False, indent=2)
                    + "\n\nNEW USER MESSAGE: " + user_q
                    + "\n\nReturn a ChatTurn JSON object. Always include reply. "
                    "Use assignment_updates only for an explicit, unambiguous quantity-level correction. "
                    "If removing a person leaves no one assigned to a quantity, do not return an empty list; ask who consumed it or whether it should be marked unused. "
                    "For ordinary bill questions, explain from the supplied Python-calculated values. For unrelated topics, politely limit scope to this bill."
                )
                response = get_gemini_client().models.generate_content(
                    model=MODEL_NAME,
                    contents=chat_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=ChatTurn,
                        temperature=0.2,
                    ),
                )
                try:
                    turn = ChatTurn.model_validate_json(response.text or "{}")
                except Exception:
                    # Never expose a raw Pydantic error to the user.
                    turn = ChatTurn(reply=(response.text or "").strip() or "I couldn't form a response. Please try asking in a different way.")
                applied = False
                for update in turn.assignment_updates:
                    if update.item_index < 0 or update.item_index >= len(receipt.items):
                        continue
                    qty = max(1, int(round(receipt.items[update.item_index].quantity)))
                    if update.quantity_number < 1 or update.quantity_number > qty:
                        continue
                    people = [p for p in update.people if p in st.session_state.people]
                    if not people:
                        continue
                    qmap = st.session_state.assignments.setdefault(str(update.item_index), {})
                    qmap[str(update.quantity_number)] = people
                    applied = True
                if applied:
                    with st.spinner("Updating the affected shares..."):
                        new_calc = calculate_with_python()
                    st.session_state.calculation = new_calc
                    turn.reply += "\n\nI've updated the assignment and recalculated the shares. Please review the updated totals below."
                st.session_state.chat_messages.append({"role": "assistant", "content": turn.reply})
                save_draft()
                st.rerun()
            except Exception as exc:
                # Log details server-side; keep the user-facing message friendly.
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": "I couldn't complete that request just now. Please try once more. If it was an assignment correction, state the item and quantity (for example, “Ram did not eat Pizza #2”)."
                })
                st.session_state.save_status = f"Chat request failed: {type(exc).__name__}"
                save_draft()

        # ---------- Email ----------

        st.markdown("### 8. 📧 Email the complete summary")
        if st.button("📤 Generate & email bill summary", type="primary", use_container_width=True):
            try:
                with st.spinner("Gemini is preparing your complete bill summary..."):
                    summary = summary_to_email()
                send_email(
                    st.session_state.email,
                    f"🧾 BillSplit AI — {bill_title()}",
                    summary,
                )
                st.session_state.email_sent = True
                st.session_state.email_status = f"Summary sent to {st.session_state.email}"
                save_draft()
                st.success("Email sent successfully.")
            except Exception as exc:
                st.error(f"Email failed: {exc}")

        if st.session_state.email_status:
            st.caption(st.session_state.email_status)

# ---------- Save footer ----------

if st.session_state.active_bill_id and st.session_state.receipt:
    st.divider()
    st.caption(
        f"💾 Auto-save enabled • {st.session_state.save_status or 'Draft'}"
        + (f" • Last saved {st.session_state.last_saved}" if st.session_state.last_saved else "")
        + " • Refresh-safe draft recovery"
    )
else:
    st.caption("💡 Tip: after onboarding, your bill is saved as a draft automatically.")
