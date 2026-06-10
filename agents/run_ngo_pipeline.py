"""
NGO Pipeline Orchestrator — Full end-to-end runner.
=====================================================
USAGE:
    python -m "agents.NGO Outreach.run_ngo_pipeline" --csv data/inputs/my_ngos.csv
    python -m "agents.NGO Outreach.run_ngo_pipeline" --csv file1.csv file2.csv file3.csv
    python -m "agents.NGO Outreach.run_ngo_pipeline" --csv data/inputs/my_ngos.csv --dry-run

PIPELINE STEPS:
  1. Load & validate CSV input  (from ngobase export)
  2. Score & rank NGOs           (ngo_partner_agent)
  3. Enrich contacts             (ngo_enrichment_agent)
  4. Generate outreach emails    (ngo_copywriter_agent)
  5. Upload to Notion            (notion_import_ngos)  ← dedup against full DB
  6. Send completion email       → REPORT_RECIPIENTS (see below)

CAMPAIGN ID FORMAT: "NGOs_DDMMYYYY_[MISSION]"
  where MISSION is derived from the most common `work_area` in the input CSV.

INPUT CSV (ngobase export) expects columns:
  ngo_name, col href, work_area, sub_work_area, listing_locations, account_type
"""

import argparse
import csv
import smtplib
import sys
import time
from collections import Counter
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

# ── path setup ──────────────────────────────────────────────────────────────
# Project root (tum_sales_agent/) → for utils.*
# THIS_DIR (NGO Outreach/)        → for sibling agents (ngo_partner_agent, etc.)
THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(THIS_DIR.parent))  # ngo-partnerships-agent/
sys.path.insert(0, str(THIS_DIR))                # agents/

from rich.console import Console
from rich.rule import Rule

from utils.config import (
    OPENAI_API_KEY,
    NOTION_TOKEN,
    NOTION_DB_ACCOUNTS_ID,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    DATA_DIR,
)

console = Console()

NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
NGO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Pipeline output files (canonical names, always overwritten by latest run)
RANKED_CSV   = NGO_OUTPUT_DIR / "ngo_ranked.csv"
ENRICHED_CSV = NGO_OUTPUT_DIR / "ngo_enriched.csv"
OUTREACH_CSV = NGO_OUTPUT_DIR / "ngo_outreach.csv"

# Fixed notification recipients
REPORT_RECIPIENTS = ["nicopaul19@gmail.com", "leon1.koerbs@gmail.com", "jschurer27@gmail.com", "carlo.rn02@gmail.com", "lisa.gavrilova@tum.de"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _derive_mission(csv_paths: List[Path]) -> str:
    """
    Extract the dominant `work_area` across all input CSVs.
    Returns a slug suitable for use in a campaign ID, e.g. 'Environment'.
    """
    work_areas = []
    for csv_path in csv_paths:
        try:
            with open(csv_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    wa = row.get("work_area", "").strip()
                    if wa:
                        work_areas.append(wa)
        except Exception:
            pass
    if not work_areas:
        return "NGO"
    most_common = Counter(work_areas).most_common(1)[0][0]
    # Slug: keep alphanumerics + underscore, max 20 chars
    slug = "".join(c if c.isalnum() else "_" for c in most_common)
    return slug[:20].strip("_") or "NGO"


def _build_campaign_id(mission_slug: str) -> str:
    """Format: NGOs_DDMMYYYY_[MISSION]"""
    date_str = datetime.now().strftime("%d%m%Y")
    return f"NGOs_{date_str}_{mission_slug}"


def _load_notion_uploaded_rows() -> List[dict]:
    """
    Load the outreach CSV and return only rows that were newly uploaded to Notion
    (i.e. rows that have a campaign_id set by the pipeline).
    Returns list of dicts with keys: ngo_name, col href, work_area, mission.
    """
    if not OUTREACH_CSV.exists():
        return []
    rows = []
    with open(OUTREACH_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("ngo_name", "").strip():
                rows.append(row)
    return rows


def _generate_email_html(
    campaign_id: str,
    accounts: List[dict],
    counts: dict,
    duration_secs: float,
) -> str:
    date_str = datetime.now().strftime("%B %d, %Y at %H:%M")
    duration_str = f"{int(duration_secs // 60)}m {int(duration_secs % 60)}s"

    # Build accounts table rows
    account_rows = ""
    for row in accounts:
        name   = row.get("ngo_name", "")
        url    = row.get("col href", "")
        area   = row.get("work_area", "")
        sub    = row.get("sub_work_area", "")
        mission_text = f"{sub}" if sub else area

        # Truncated mission from reasoning / sub_work_area
        mission_display = (row.get("reasoning") or mission_text or "—")[:120]

        link = f'<a href="{url}" style="color:#4f46e5;">{name}</a>' if url else name
        account_rows += f"""
        <tr>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;">{link}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#555;">{area}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:#555;font-size:12px;">{mission_display}</td>
        </tr>"""

    stats_rows = ""
    metric_colors = {
        "Created (new)": "#16a34a",
        "Updated (existing)": "#ca8a04",
        "Skipped (duplicate)": "#6b7280",
        "Errors": "#dc2626",
    }
    for label, value in counts.items():
        color = metric_colors.get(label, "#6b7280")
        stats_rows += f"""
        <tr>
          <td style="padding:7px 12px;border-bottom:1px solid #f5f5f5;">{label}</td>
          <td style="padding:7px 12px;border-bottom:1px solid #f5f5f5;text-align:right;">
            <span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold;">{value}</span>
          </td>
        </tr>"""

    return f"""
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:680px;margin:0 auto;padding:20px;color:#1a1a2e;">

  <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:28px 32px;border-radius:14px 14px 0 0;">
    <h1 style="margin:0;font-size:22px;">✅ NGO Upload Complete</h1>
    <p style="margin:8px 0 0;opacity:0.75;font-size:14px;">Campaign <strong>{campaign_id}</strong> · {date_str} · {duration_str}</p>
  </div>

  <div style="background:#fff;border:1px solid #e0e0e0;border-top:none;padding:24px 32px;border-radius:0 0 14px 14px;">

    <h3 style="margin-top:0;color:#1a1a2e;">Upload Summary</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;">
      {stats_rows}
    </table>

    <h3 style="color:#1a1a2e;margin-top:28px;">Accounts Uploaded ({len(accounts)})</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#f8f8f8;">
          <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">NGO</th>
          <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">Work Area</th>
          <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #e0e0e0;">Mission</th>
        </tr>
      </thead>
      <tbody>
        {account_rows}
      </tbody>
    </table>

    <p style="margin-top:24px;padding:12px 16px;background:#f8f9fa;border-radius:8px;font-size:12px;color:#666;">
      Full outreach CSV attached · Generated by TUM Social AI NGO Pipeline
    </p>
  </div>
</body>
</html>"""


def _send_completion_email(
    campaign_id: str,
    accounts: List[dict],
    counts: dict,
    duration_secs: float,
    csv_attachment_path: Optional[Path] = None,
) -> bool:
    """Send HTML completion email with the outreach CSV attached."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        console.print("[yellow]⚠ Email not configured (GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing). Skipping email.[/yellow]")
        return False

    subject = f"✅ NGO Upload Done — {campaign_id} ({counts.get('Created (new)', 0)} new accounts)"

    msg = MIMEMultipart()
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(REPORT_RECIPIENTS)
    msg["Subject"] = subject

    html = _generate_email_html(campaign_id, accounts, counts, duration_secs)
    msg.attach(MIMEText(html, "html"))

    # Attach outreach CSV
    if csv_attachment_path and csv_attachment_path.exists() and csv_attachment_path.stat().st_size > 0:
        with open(csv_attachment_path, "rb") as f:
            att = MIMEApplication(f.read(), _subtype="csv")
            att.add_header("Content-Disposition", "attachment", filename=csv_attachment_path.name)
            msg.attach(att)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        console.print(f"[green]📧 Completion email sent to {', '.join(REPORT_RECIPIENTS)}[/green]")
        return True
    except Exception as e:
        console.print(f"[red]❌ Email sending failed: {e}[/red]")
        return False


# ── main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(csv_inputs: List[str], dry_run: bool = False, min_score: float = 5.0):
    """
    Full NGO outreach pipeline:
      CSV(s) → Score → Enrich → Copywrite → Notion (dedup) → Email report
    """
    start_time = time.time()

    console.print()
    console.print(Rule("[bold magenta]TUM Social AI — NGO Outreach Pipeline[/bold magenta]"))
    for p in csv_inputs:
        console.print(f"[dim]Input: {p}[/dim]")
    if dry_run:
        console.print("[yellow]DRY RUN — Notion writes and email disabled[/yellow]")
    console.print()

    # ── validate prerequisites ────────────────────────────────────────────────
    if not OPENAI_API_KEY:
        console.print("[red]❌ OPENAI_API_KEY not set[/red]")
        return
    if not NOTION_TOKEN or not NOTION_DB_ACCOUNTS_ID:
        console.print("[red]❌ NOTION_TOKEN or NOTION_DB_ACCOUNTS_ID not set[/red]")
        return

    input_paths = []
    for csv_input in csv_inputs:
        p = Path(csv_input)
        if not p.exists():
            console.print(f"[red]❌ CSV not found: {csv_input}[/red]")
            return
        input_paths.append(p)

    # ── derive campaign ID from CSV work_area ─────────────────────────────────
    mission_slug = _derive_mission(input_paths)
    campaign_id  = _build_campaign_id(mission_slug)
    console.print(f"[cyan]Campaign ID: [bold]{campaign_id}[/bold][/cyan]")
    console.print()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Score & Rank
    # ─────────────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 1/4 · Scoring & Ranking NGOs[/bold]"))
    from ngo_partner_agent import run_scoring  # noqa: F401
    try:
        run_scoring(
            csv_paths=[str(p) for p in input_paths],
            min_score=min_score,
            batch_size=15,
            append=False,
        )
    except Exception as e:
        console.print(f"[red]❌ Scoring failed: {e}[/red]")
        return

    if not RANKED_CSV.exists():
        console.print("[red]❌ Ranked CSV not created — aborting pipeline[/red]")
        return

    console.print(f"[green]✓ Ranked CSV: {RANKED_CSV}[/green]")

    # Assign owners 50/50 alternating by rank order
    NGO_OWNERS = ["Carlo Renner", "Lisa Gavrilova"]
    with open(RANKED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        ranked_headers = list(reader.fieldnames or [])
        ranked_rows = list(reader)
    for i, row in enumerate(ranked_rows):
        row["owner"] = NGO_OWNERS[i % 2]
    if "owner" not in ranked_headers:
        ranked_headers.append("owner")
    with open(RANKED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ranked_headers)
        writer.writeheader()
        writer.writerows(ranked_rows)
    carlo_count = sum(1 for r in ranked_rows if r["owner"] == "Carlo Renner")
    lisa_count  = sum(1 for r in ranked_rows if r["owner"] == "Lisa Gavrilova")
    console.print(f"[cyan]Owner split: Carlo Renner × {carlo_count} | Lisa Gavrilova × {lisa_count}[/cyan]")
    console.print()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Enrich Contacts
    # ─────────────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 2/4 · Enriching Contact Details[/bold]"))
    # Clear previous enriched CSV so we get a clean run (not resuming old data)
    if ENRICHED_CSV.exists():
        ENRICHED_CSV.unlink()
    progress_file = NGO_OUTPUT_DIR / "enrichment_progress.json"
    if progress_file.exists():
        progress_file.unlink()

    from ngo_enrichment_agent import run_enrichment  # noqa: F401
    try:
        run_enrichment(input_csv=str(RANKED_CSV))
    except Exception as e:
        console.print(f"[red]❌ Enrichment failed: {e}[/red]")
        return

    if not ENRICHED_CSV.exists():
        console.print("[red]❌ Enriched CSV not created — aborting pipeline[/red]")
        return

    console.print(f"[green]✓ Enriched CSV: {ENRICHED_CSV}[/green]")
    console.print()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: Generate Outreach Emails
    # ─────────────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 3/4 · Generating Outreach Emails[/bold]"))
    from ngo_copywriter_agent import run_copywriter  # noqa: F401
    try:
        run_copywriter(
            dry_run=False,
            input_csv=str(ENRICHED_CSV),
            campaign_id=campaign_id,
        )
    except Exception as e:
        console.print(f"[red]❌ Copywriter failed: {e}[/red]")
        return

    if not OUTREACH_CSV.exists():
        console.print("[red]❌ Outreach CSV not created — aborting pipeline[/red]")
        return

    console.print(f"[green]✓ Outreach CSV: {OUTREACH_CSV}[/green]")
    console.print()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Upload to Notion (with full dedup against existing Accounts DB)
    # ─────────────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Step 4/4 · Uploading to Notion (with dedup)[/bold]"))

    created  = 0
    updated  = 0
    skipped  = 0
    errors   = 0

    if not dry_run:
        from notion_import_ngos import run_import  # noqa: F401
        # notion_import_ngos also creates/updates the shared Campaign Tracker
        # entry and relates it back to the campaign Accounts.
        try:
            import_result = run_import(
                csv_path=str(OUTREACH_CSV),
                dry_run=False,
            )
            if import_result:
                created = import_result.get("created", 0)
                updated = import_result.get("updated", 0)
                errors = import_result.get("errors", 0)
            # Re-read the outreach CSV to count rows for the email report
        except Exception as e:
            console.print(f"[red]❌ Notion upload failed: {e}[/red]")
            errors += 1
    else:
        console.print("[yellow]DRY RUN — skipping Notion upload[/yellow]")

    # Load upload results for the email
    uploaded_accounts = _load_notion_uploaded_rows()

    # Best-effort counters (notion_import_ngos prints them; approximate here)
    counts = {
        "Total processed": len(uploaded_accounts),
        "Created (new)": created if created > 0 else "see log",
        "Updated (existing)": updated if updated > 0 else "see log",
        "Errors": errors,
        "Campaign ID": campaign_id,
    }

    console.print()

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Send completion email
    # ─────────────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Sending Completion Email[/bold]"))

    duration = time.time() - start_time

    if not dry_run:
        _send_completion_email(
            campaign_id=campaign_id,
            accounts=uploaded_accounts,
            counts=counts,
            duration_secs=duration,
            csv_attachment_path=OUTREACH_CSV,
        )

    console.print()
    console.print(Rule("[bold green]Pipeline Complete[/bold green]"))
    console.print(f"[green]Campaign:  {campaign_id}[/green]")
    console.print(f"[green]Duration:  {int(duration // 60)}m {int(duration % 60)}s[/green]")
    console.print(f"[green]Accounts:  {len(uploaded_accounts)} processed[/green]")
    console.print(f"[dim]Output files in: {NGO_OUTPUT_DIR}[/dim]")
    console.print()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full NGO Outreach Pipeline: CSV → Score → Enrich → Email → Notion → Report"
    )
    parser.add_argument(
        "--csv", required=True, nargs="+",
        help="Path(s) to ngobase CSV export(s) — accepts one or more files"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run without writing to Notion or sending emails"
    )
    parser.add_argument(
        "--min-score", type=float, default=5.0,
        help="Minimum partner score to qualify an NGO (default: 5.0)"
    )
    args = parser.parse_args()

    run_pipeline(
        csv_inputs=args.csv,
        dry_run=args.dry_run,
        min_score=args.min_score,
    )
