"""
Notion Import NGOs — Imports NGO outreach CSVs into the Notion Accounts DB.

Maps CSV columns from the NGO pipeline (partner → enrichment → copywriter)
to Notion Accounts DB properties. Handles deduplication by domain and org name.

Property Mapping (CSV → Notion):
  ngo_name              → Organization*        (title)
  col href              → Website URL*         (url)
  listing_locations     → City                 (select)
  work_area             → Work Area NGO        (select)
  sub_work_area         → Mission*             (rich_text, combined with reasoning)
  account_type          → Account Type*        (select)
  campaign_id           → Campaign ID          (multi_select)
  total_score           → Lead Score           (number)
  estimated_employees   → # Employees          (number, parsed)
  selected_contact_name → [Suspect] Contact Name      (rich_text)
  selected_contact_role → [Suspect] Job Title         (rich_text)
  contact_person_email  → [Suspect] Contact Email     (rich_text)
  general_email         → General Email                (email)
  email_subject         → Cold Email Subject           (email)
  email_body            → Cold Email Body              (rich_text)
  ai_partnership_angle  → Trigger Event                (rich_text)

Usage:
    python -m "agents.NGO Outreach.notion_import_ngos" --csv <path>
    python -m "agents.NGO Outreach.notion_import_ngos" --csv <path> --dry-run
    python -m "agents.NGO Outreach.notion_import_ngos" --csv <path> --limit 5
"""

import argparse
import csv
import math
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests as http_requests

# NGO Outreach/ → agents/ → tum_sales_agent/
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from utils.config import NOTION_TOKEN, NOTION_DB_ACCOUNTS_ID, OPENAI_API_KEY

console = Console()

DEFAULT_ACCOUNT_TYPE = "nonprofit"
ACCOUNT_TYPE_ALIASES = {
    "ngo": DEFAULT_ACCOUNT_TYPE,
    "non profit": DEFAULT_ACCOUNT_TYPE,
    "non-profit": DEFAULT_ACCOUNT_TYPE,
}

# ---------------------------------------------------------------------------
# Sender → Notion user ID mapping
# Add new senders here; the copywriter assigns these names via NGO_OWNERS.
# ---------------------------------------------------------------------------
OWNER_IDS: dict[str, str] = {
    "Carlo Renner":    "2c5d872b-594c-81ca-abfc-00023d45afd3",
    "Lisa Gavrilova":  "328d872b-594c-8133-8a1f-00020ea856a1",
    "Florian Lichius": "a6894add-55e8-481c-8c25-39760d7e7593",
}

# Notion API config
NOTION_API_VERSION = "2022-06-28"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_API_VERSION,
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# CSV → Notion property mapping
# ---------------------------------------------------------------------------

def clean_value(val) -> str:
    """Clean a value for safe use in API calls."""
    if val is None:
        return ""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return ""
    return str(val).strip()


def normalize_account_type(raw) -> str:
    """Map legacy account type values to the current Notion select options."""
    value = clean_value(raw)
    if not value:
        return DEFAULT_ACCOUNT_TYPE
    return ACCOUNT_TYPE_ALIASES.get(value.lower(), value)


def normalize_domain(url: str) -> str:
    """Extract normalized domain from URL."""
    url = url.strip().lower()
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]
        domain = domain.replace("www.", "")
        return domain.rstrip("/")
    except Exception:
        return ""


def normalize_url(url: str) -> str:
    """Ensure URL has https:// prefix."""
    url = url.strip()
    if not url:
        return ""
    if not url.startswith("http"):
        return f"https://{url}"
    return url


def parse_employees(raw: str) -> Optional[int]:
    """Parse estimated_employees like '1000+', '50-100', '200-500' to a number."""
    raw = clean_value(raw)
    if not raw:
        return None
    # Remove non-numeric except dash
    raw = raw.replace("+", "").replace(",", "").strip()
    if "-" in raw:
        # Take midpoint of range
        parts = raw.split("-")
        try:
            low = int(parts[0].strip())
            high = int(parts[1].strip())
            return (low + high) // 2
        except (ValueError, IndexError):
            pass
    try:
        return int(raw)
    except ValueError:
        return None


def build_mission_text(row: dict) -> str:
    """Build mission text from sub_work_area and reasoning."""
    parts = []
    sub = clean_value(row.get("sub_work_area", ""))
    if sub:
        parts.append(sub)
    reasoning = clean_value(row.get("reasoning", ""))
    if reasoning:
        parts.append(reasoning)
    return ". ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Notion API helpers
# ---------------------------------------------------------------------------

def fetch_existing_accounts(campaign_id: str = "") -> dict:
    """
    Fetch existing accounts from Notion for deduplication.
    Returns: {"domains": {domain: page_id}, "names": {name_lower: page_id}}
    """
    lookup = {"domains": {}, "names": {}}

    has_more = True
    cursor = None
    total = 0

    while has_more:
        body = {"page_size": 100}

        # If campaign_id specified, filter by it for faster queries
        if campaign_id:
            body["filter"] = {
                "property": "Campaign ID",
                "multi_select": {"contains": campaign_id},
            }

        if cursor:
            body["start_cursor"] = cursor

        resp = http_requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ACCOUNTS_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
        )

        if resp.status_code != 200:
            console.print(f"[red]Notion query failed: {resp.status_code}[/red]")
            break

        data = resp.json()

        for page in data.get("results", []):
            page_id = page["id"]
            props = page["properties"]

            # Extract domain from Website URL*
            website_url = props.get("Website URL*", {}).get("url", "") or ""
            domain = normalize_domain(website_url)
            if domain:
                lookup["domains"][domain] = page_id

            # Extract org name
            org_title = props.get("Organization*", {}).get("title", [])
            if org_title:
                name = org_title[0].get("plain_text", "").strip().lower()
                if name:
                    lookup["names"][name] = page_id

            total += 1

        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    console.print(f"[cyan]Fetched {total} existing accounts ({len(lookup['domains'])} domains, {len(lookup['names'])} names)[/cyan]")
    return lookup


def find_existing_account(row: dict, lookup: dict) -> Optional[str]:
    """Check if an account already exists. Returns page_id or None."""
    # Primary: domain match
    website = clean_value(row.get("col href", ""))
    domain = normalize_domain(website)
    if domain and domain in lookup["domains"]:
        return lookup["domains"][domain]

    # Fallback: org name match
    name = clean_value(row.get("ngo_name", "")).lower()
    if name and name in lookup["names"]:
        return lookup["names"][name]

    return None


def create_account(row: dict) -> Optional[str]:
    """Create a new account in Notion. Returns page_id or None."""
    ngo_name = clean_value(row.get("ngo_name", ""))
    if not ngo_name:
        return None

    website = normalize_url(clean_value(row.get("col href", "")))
    city = clean_value(row.get("listing_locations", ""))
    work_area = clean_value(row.get("work_area", ""))
    account_type = normalize_account_type(row.get("account_type", DEFAULT_ACCOUNT_TYPE))
    campaign_id = clean_value(row.get("campaign_id", ""))
    mission = build_mission_text(row)
    ai_angle = clean_value(row.get("ai_partnership_angle", ""))
    employees = parse_employees(row.get("estimated_employees", ""))
    total_score = None
    try:
        total_score = float(row.get("total_score", ""))
    except (ValueError, TypeError):
        pass

    # Contact fields
    contact_name = clean_value(row.get("selected_contact_name", ""))
    contact_role = clean_value(row.get("selected_contact_role", ""))
    contact_email = clean_value(row.get("contact_person_email", ""))
    general_email = clean_value(row.get("general_email", ""))
    email_subject = clean_value(row.get("email_subject", ""))
    email_body = clean_value(row.get("email_body", ""))

    # Build properties
    properties = {
        "Organization*": {
            "title": [{"text": {"content": ngo_name[:2000]}}]
        },
        "Status": {
            "status": {"name": "Prospect Qualified"}
        },
    }

    # Website URL*
    if website:
        properties["Website URL*"] = {"url": website}

    # City (select)
    if city:
        properties["City"] = {"select": {"name": city}}

    # Work Area NGO (select)
    if work_area:
        properties["Work Area NGO"] = {"select": {"name": work_area}}

    # Account Type* (select)
    if account_type:
        properties["Account Type*"] = {"select": {"name": account_type}}

    # Campaign ID (multi_select)
    if campaign_id:
        properties["Campaign ID"] = {
            "multi_select": [{"name": campaign_id}]
        }

    # Mission* (rich_text)
    if mission:
        properties["Mission*"] = {
            "rich_text": [{"text": {"content": mission[:2000]}}]
        }

    # Trigger Event (rich_text) — AI partnership angle
    if ai_angle:
        properties["Trigger Event"] = {
            "rich_text": [{"text": {"content": ai_angle[:2000]}}]
        }

    # Lead Score (number) — total_score from ranking
    if total_score is not None:
        properties["Lead Score"] = {"number": total_score}

    # # Employees (number)
    if employees is not None:
        properties["# Employees"] = {"number": employees}

    # [Suspect] Contact Name (rich_text)
    if contact_name:
        properties["[Suspect] Contact Name"] = {
            "rich_text": [{"text": {"content": contact_name[:2000]}}]
        }

    # [Suspect] Job Title (rich_text)
    if contact_role:
        properties["[Suspect] Job Title"] = {
            "rich_text": [{"text": {"content": contact_role[:2000]}}]
        }

    # [Suspect] Contact Email (rich_text)
    if contact_email:
        properties["[Suspect] Contact Email"] = {
            "rich_text": [{"text": {"content": contact_email[:2000]}}]
        }

    # General Email (email)
    if general_email and "@" in general_email:
        properties["General Email"] = {"email": general_email.lower()}

    # Cold Email Subject (email — stored as email type in Notion)
    if email_subject:
        properties["Cold Email Subject"] = {"email": email_subject[:2000]}

    # Cold Email Body (rich_text)
    if email_body:
        properties["Cold Email Body"] = {
            "rich_text": [{"text": {"content": email_body[:2000]}}]
        }

    # Owner* (people) — new pages always get owner assigned
    owner = clean_value(row.get("owner", ""))
    user_id = OWNER_IDS.get(owner)
    if user_id:
        properties["Owner*"] = {"people": [{"object": "user", "id": user_id}]}

    # Create page
    resp = http_requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": NOTION_DB_ACCOUNTS_ID},
            "properties": properties,
        },
    )

    if resp.status_code == 200:
        return resp.json()["id"]
    else:
        error = resp.json().get("message", resp.text[:200])
        console.print(f"[red]  Create failed for {ngo_name}: {error}[/red]")
        return None


def update_account(page_id: str, row: dict) -> bool:
    """
    Update an existing account — append campaign ID, fill empty fields only.
    Never overwrite existing values. Never downgrade status.
    """
    # Fetch current page
    resp = http_requests.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
    )
    if resp.status_code != 200:
        return False

    current = resp.json()["properties"]
    updates = {}

    # Append Campaign ID (multi_select union)
    campaign_id = clean_value(row.get("campaign_id", ""))
    if campaign_id:
        existing_campaigns = [o["name"] for o in current.get("Campaign ID", {}).get("multi_select", [])]
        if campaign_id not in existing_campaigns:
            existing_campaigns.append(campaign_id)
            updates["Campaign ID"] = {
                "multi_select": [{"name": c} for c in existing_campaigns]
            }

    # Fill empty fields only
    def _is_empty(prop_name: str) -> bool:
        prop = current.get(prop_name, {})
        ptype = prop.get("type", "")
        if ptype == "rich_text":
            return not prop.get("rich_text", [])
        elif ptype == "url":
            return not prop.get("url")
        elif ptype == "select":
            return not prop.get("select")
        elif ptype == "multi_select":
            return not prop.get("multi_select", [])
        elif ptype == "number":
            return prop.get("number") is None
        elif ptype == "email":
            return not prop.get("email")
        elif ptype == "title":
            return not prop.get("title", [])
        return True

    # Map CSV fields to Notion properties (only fill if empty)
    field_map = {
        "Website URL*": ("url", normalize_url(clean_value(row.get("col href", "")))),
        "City": ("select", clean_value(row.get("listing_locations", ""))),
        "Work Area NGO": ("select", clean_value(row.get("work_area", ""))),
        "Account Type*": ("select", normalize_account_type(row.get("account_type", DEFAULT_ACCOUNT_TYPE))),
        "Mission*": ("rich_text", build_mission_text(row)),
        "[Suspect] Contact Name": ("rich_text", clean_value(row.get("selected_contact_name", ""))),
        "[Suspect] Job Title": ("rich_text", clean_value(row.get("selected_contact_role", ""))),
        "[Suspect] Contact Email": ("rich_text", clean_value(row.get("contact_person_email", ""))),
        "Cold Email Body": ("rich_text", clean_value(row.get("email_body", ""))),
    }

    for prop_name, (ptype, value) in field_map.items():
        if not value:
            continue
        if not _is_empty(prop_name):
            continue

        if ptype == "url":
            updates[prop_name] = {"url": value}
        elif ptype == "select":
            updates[prop_name] = {"select": {"name": value}}
        elif ptype == "rich_text":
            updates[prop_name] = {"rich_text": [{"text": {"content": value[:2000]}}]}

    # Email fields
    general_email = clean_value(row.get("general_email", ""))
    if general_email and "@" in general_email and _is_empty("General Email"):
        updates["General Email"] = {"email": general_email.lower()}

    email_subject = clean_value(row.get("email_subject", ""))
    if email_subject and _is_empty("Cold Email Subject"):
        updates["Cold Email Subject"] = {"email": email_subject[:2000]}

    # Number fields
    employees = parse_employees(row.get("estimated_employees", ""))
    if employees is not None and _is_empty("# Employees"):
        updates["# Employees"] = {"number": employees}

    try:
        total_score = float(row.get("total_score", ""))
        if _is_empty("Lead Score"):
            updates["Lead Score"] = {"number": total_score}
    except (ValueError, TypeError):
        pass

    # Trigger Event — always append (never overwrite)
    ai_angle = clean_value(row.get("ai_partnership_angle", ""))
    if ai_angle:
        current_trigger = ""
        trigger_rt = current.get("Trigger Event", {}).get("rich_text", [])
        if trigger_rt:
            current_trigger = trigger_rt[0].get("plain_text", "")
        if ai_angle not in current_trigger:
            new_trigger = f"{current_trigger}\n{ai_angle}".strip() if current_trigger else ai_angle
            updates["Trigger Event"] = {
                "rich_text": [{"text": {"content": new_trigger[:2000]}}]
            }

    # Owner* — only set if account is on Prospect Qualified (or has no owner yet)
    owner = clean_value(row.get("owner", ""))
    if owner:
        current_status = current.get("Status", {}).get("status", {}).get("name", "")
        current_owner = current.get("Owner*", {}).get("people", [])
        if not current_owner or current_status == "Prospect Qualified":
            user_id = OWNER_IDS.get(owner)
            if user_id:
                updates["Owner*"] = {"people": [{"object": "user", "id": user_id}]}

    if not updates:
        return True  # Nothing to update

    resp = http_requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": updates},
    )

    return resp.status_code == 200


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_import(csv_path: str, dry_run: bool = False, limit: int = 0, start_from: int = 1):
    """Import NGO outreach CSV into Notion Accounts DB."""
    console.print("\n" + "=" * 60)
    console.print("[bold magenta]NGO Notion Import Agent[/bold magenta]")
    if dry_run:
        console.print("[yellow]DRY RUN — no Notion writes[/yellow]")
    console.print("=" * 60)

    if not NOTION_TOKEN:
        console.print("[red]NOTION_TOKEN not set[/red]")
        return
    if not NOTION_DB_ACCOUNTS_ID:
        console.print("[red]NOTION_DB_ACCOUNTS_ID not set[/red]")
        return

    # Load CSV
    path = Path(csv_path)
    if not path.exists():
        console.print(f"[red]CSV not found: {path}[/red]")
        return

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    console.print(f"[cyan]Loaded {len(rows)} NGOs from {path.name}[/cyan]")

    # Filter by rank
    rows_to_process = [r for r in rows if int(r.get("rank", 0)) >= start_from]
    if limit > 0:
        rows_to_process = rows_to_process[:limit]

    # Skip empty rows
    rows_to_process = [r for r in rows_to_process if clean_value(r.get("ngo_name", ""))]

    console.print(f"[cyan]Processing {len(rows_to_process)} NGOs[/cyan]")

    # Fetch existing accounts for deduplication
    # Extract campaign_id from first row
    sample_campaign = clean_value(rows_to_process[0].get("campaign_id", "")) if rows_to_process else ""
    console.print(f"[cyan]Campaign: {sample_campaign}[/cyan]")
    console.print(f"[cyan]Fetching existing accounts for dedup...[/cyan]")
    lookup = fetch_existing_accounts()  # Fetch all accounts for thorough dedup

    # Process
    created = 0
    updated = 0
    skipped = 0
    errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Importing...", total=len(rows_to_process))

        for row in rows_to_process:
            name = clean_value(row.get("ngo_name", ""))
            rank = row.get("rank", "?")
            progress.update(task, description=f"[cyan]#{rank} {name[:40]}")

            existing_id = find_existing_account(row, lookup)

            if existing_id:
                # Account exists — update empty fields
                if not dry_run:
                    ok = update_account(existing_id, row)
                    if ok:
                        updated += 1
                        progress.console.print(f"  [yellow]#{rank} Updated:[/yellow] {name}")
                    else:
                        errors += 1
                        progress.console.print(f"  [red]#{rank} Update failed:[/red] {name}")
                else:
                    updated += 1
                    progress.console.print(f"  [yellow]#{rank} Would update:[/yellow] {name}")
            else:
                # New account — create
                if not dry_run:
                    page_id = create_account(row)
                    if page_id:
                        created += 1
                        # Add to lookup for dedup within this batch
                        domain = normalize_domain(clean_value(row.get("col href", "")))
                        if domain:
                            lookup["domains"][domain] = page_id
                        lookup["names"][name.lower()] = page_id
                        progress.console.print(f"  [green]#{rank} Created:[/green] {name}")
                    else:
                        errors += 1
                else:
                    created += 1
                    progress.console.print(f"  [green]#{rank} Would create:[/green] {name}")

            progress.advance(task)
            if not dry_run:
                time.sleep(0.35)  # Notion rate limit: ~3 req/s

    # Summary
    console.print("\n" + "=" * 40)
    summary = Table(title="NGO Import Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green")
    summary.add_row("Total processed", str(len(rows_to_process)))
    summary.add_row("Created (new)", str(created))
    summary.add_row("Updated (existing)", str(updated))
    summary.add_row("Errors", str(errors))
    summary.add_row("Campaign ID", sample_campaign)
    if dry_run:
        summary.add_row("Mode", "DRY RUN")
    console.print(summary)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import NGO outreach CSV into Notion Accounts DB")
    parser.add_argument("--csv", type=str, required=True, help="Path to NGO outreach CSV")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to Notion")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N rows")
    parser.add_argument("--start-from", type=int, default=1, help="Start from rank N")
    args = parser.parse_args()

    run_import(
        csv_path=args.csv,
        dry_run=args.dry_run,
        limit=args.limit,
        start_from=args.start_from,
    )
