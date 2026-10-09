# BillSplit AI — AI Vision Bill Assistant

A Streamlit + Gemini Vision application for complex real-world bill splitting.

## Features

- Receipt photo analysis with Gemini Vision
- Receipt verification and manual correction of subtotal, tax, charges, discount and final total
- Multiple bills in one user session
- Separate Gemini context per active bill
- Quantity-level assignments
- Different people can share different quantities of the same item
- Final review before calculation
- Deterministic Python/Decimal bill calculation and proportional tax allocation
- Bill reconciliation
- Conversational corrections ("Ram didn't eat Pizza #2")
- Prevents purchased quantities from silently becoming unassigned
- "Why am I paying this?" style explanations
- Bill-only chatbot with outside-topic protection
- Gmail SMTP summary
- Supabase draft auto-save and resume after refresh
- Saved bills dashboard
- AI Compare Bills: read-only comparison of 2+ completed bills\n- Separate comparison-only chat and comparison email including follow-up Q&A
- AI comparison dashboard with cost, tax, item and participant insights
- Separate email comparison report (not mixed into individual bill emails)

## Run locally

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

For Command Prompt:

```bat
venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```

## Secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in:
- GEMINI_API_KEY
- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY (recommended for this server-side app)
- GMAIL_ADDRESS
- GMAIL_APP_PASSWORD

Never commit the real secrets file.

## Supabase

Run `supabase_schema.sql` in the Supabase SQL Editor.

The app uses a server-side service-role key for draft persistence. Keep that key only in Streamlit secrets. Do not put it into frontend JavaScript or expose it to users.

## Important behavior

Python handles deterministic monetary arithmetic, rounding, tax allocation, validation, UI and persistence. Gemini handles receipt understanding, natural-language assignment corrections, bill explanations, conversation summaries and read-only cross-bill insights.

## Deployment

Push the project to GitHub without `.streamlit/secrets.toml`, then deploy `app.py` on Streamlit Community Cloud. Add the same secrets in the deployment's Secrets settings.

Before final deployment, test:
1. Receipt upload
2. Quantity assignments
3. Final review
4. Correction in chat
5. Tax allocation
6. Refresh/resume
7. Multiple bills
8. Email delivery
9. AI Compare Bills with two completed bills
10. Comparison email delivery
11. Missing/invalid receipt values
12. Supabase draft saving


### Calculation architecture
Exact monetary arithmetic is performed by the Python calculation engine using Decimal and cent-level rounding. Gemini is used for receipt vision, natural-language assignment corrections, explanations, summaries, and comparison insights. This prevents LLM arithmetic drift while keeping the application AI-driven.
