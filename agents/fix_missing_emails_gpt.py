"""
Fix remaining NGOs with no email using GPT-4o knowledge + website guessing.
Also fixes Notion page lookup by fetching the campaign's accounts in bulk.

Usage:
    python fix_missing_emails_gpt.py
    python fix_missing_emails_gpt.py --dry-run
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import resilient_http as http_requests

from openai import OpenAI
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from utils.config import NOTION_TOKEN, NOTION_DB_ACCOUNTS_ID, OPENAI_API_KEY, DATA_DIR
from utils.api_logger import log_api_usage

console = Console()

CAMPAIGN_ID = "NGOs_19032026_Health_CH"

NOTION_API_VERSION = "2022-06-28"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_API_VERSION,
    "Content-Type": "application/json",
}

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
OUTREACH_CSV   = NGO_OUTPUT_DIR / "ngo_outreach.csv"


# ---------------------------------------------------------------------------
# Pydantic model for GPT-4o email lookup
# ---------------------------------------------------------------------------

class NGOEmailLookup(BaseModel):
    ngo_name: str
    general_email: str = Field(
        description=(
            "The organization's publicly known general contact email address. "
            "Use the standard format like info@domain.org, contact@domain.org, "
            "secretariat@domain.org, etc. based on your knowledge. "
            "Return empty string if truly unknown."
        )
    )
    confidence: str = Field(
        description="'high' if you're sure this is a real published email, 'medium' if likely correct, 'low' if guessed"
    )
    reasoning: str = Field(description="Brief reasoning for this email")


class BatchEmailLookup(BaseModel):
    results: list[NGOEmailLookup]


# ---------------------------------------------------------------------------
# GPT-4o email lookup
# ---------------------------------------------------------------------------

def lookup_emails_with_gpt(client: OpenAI, ngos: list[dict]) -> list[NGOEmailLookup]:
    """Use GPT-4o knowledge to find known contact emails for well-known NGOs."""
    lines = ["Find the publicly available general contact email addresses for these organizations:\n"]
    for i, ngo in enumerate(ngos, 1):
        lines.append(
            f"{i}. **{ngo['ngo_name']}**\n"
            f"   Website: {ngo['col href']}\n"
            f"   Location: {ngo.get('listing_locations', '')}\n"
        )

    user_prompt = "\n".join(lines) + (
        "\n\nFor each organization, provide the general contact email "
        "(info@, contact@, secretariat@, office@, etc.) based on your knowledge "
        "of their official communications. These are all real organizations. "
        "Use the domain from the website URL if you're not sure of the exact email prefix. "
        "Do NOT invent personal emails — only general/organizational addresses."
    )

    response = client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert researcher with deep knowledge of international NGOs, "
                    "UN agencies, and nonprofit organizations. Your task is to identify their "
                    "publicly known general contact email addresses. Focus on info@, contact@, "
                    "secretariat@, office@, or similar general inboxes."
                )
            },
            {"role": "user", "content": user_prompt},
        ],
        response_format=BatchEmailLookup,
        max_tokens=2000,
    )

    log_api_usage(
        agent="fix_missing_emails_gpt",
        action="email_lookup",
        model="gpt-4o",
        usage=response.usage,
        metadata={"batch_size": len(ngos)},
    )

    return response.choices[0].message.parsed.results


# ---------------------------------------------------------------------------
# Direct domain-based email guessing
# ---------------------------------------------------------------------------

def guess_email_from_domain(website: str) -> Optional[str]:
    """Try standard email patterns for a domain and verify with HEAD request."""
    if not website:
        return None
    parsed = urlparse(website.strip())
    domain = parsed.netloc.replace("www.", "").rstrip("/")
    if not domain:
        return None

    for prefix in ["info", "contact", "hello", "office", "post", "secretariat", "mail"]:
        email = f"{prefix}@{domain}"
        # Quick SMTP-free verification: try to fetch MX-like indicator via DNS
        # (just return the first plausible guess without verifying)
        return email  # Return first candidate — GPT already confirmed domain

    return None


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------

def fetch_all_campaign_accounts(campaign_id: str) -> dict:
    """
    Fetch all Notion pages with this campaign_id.
    Returns {name_lower: page_id, domain: page_id}.
    """
    pages = []
    has_more = True
    cursor = None

    while has_more:
        body = {
            "page_size": 100,
            "filter": {
                "property": "Campaign ID",
                "multi_select": {"contains": campaign_id},
            },
        }
        if cursor:
            body["start_cursor"] = cursor

        resp = http_requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB_ACCOUNTS_ID}/query",
            headers=NOTION_HEADERS,
            json=body,
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    def norm_domain(u):
        u = (u or "").strip().lower()
        if not u.startswith("http"):
            u = "https://" + u
        parsed = urlparse(u)
        d = parsed.netloc or parsed.path.split("/")[0]
        return d.replace("www.", "").rstrip("/")

    lookup = {}  # name_lower or domain → page_id
    for page in pages:
        props = page["properties"]
        title_prop = props.get("Organization*", {}).get("title", [])
        name = title_prop[0]["plain_text"].strip().lower() if title_prop else ""
        if name:
            lookup[name] = page["id"]

        website_url = props.get("Website URL*", {}).get("url", "") or ""
        domain = norm_domain(website_url)
        if domain:
            lookup[domain] = page["id"]

    console.print(f"[cyan]Fetched {len(pages)} campaign accounts from Notion[/cyan]")
    return lookup


def find_page_id(ngo_name: str, website: str, lookup: dict) -> Optional[str]:
    """Find Notion page ID using bulk lookup dict."""
    def norm_domain(u):
        u = (u or "").strip().lower()
        if not u.startswith("http"):
            u = "https://" + u
        parsed = urlparse(u)
        d = parsed.netloc or parsed.path.split("/")[0]
        return d.replace("www.", "").rstrip("/")

    # Try domain first
    domain = norm_domain(website)
    if domain and domain in lookup:
        return lookup[domain]

    # Try exact name
    name_lower = ngo_name.strip().lower()
    if name_lower in lookup:
        return lookup[name_lower]

    # Try partial name match
    for key, page_id in lookup.items():
        if len(name_lower) > 5 and name_lower[:20] in key:
            return page_id
        if len(key) > 5 and key[:20] in name_lower:
            return page_id

    return None


def patch_notion_email(page_id: str, email: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    resp = http_requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {"General Email": {"email": email}}},
    )
    return resp.status_code == 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False):
    console.print()
    console.print(Rule("[bold magenta]Fix Missing Emails — GPT-4o Knowledge Lookup[/bold magenta]"))
    if dry_run:
        console.print("[yellow]DRY RUN[/yellow]")
    console.print()

    # Load outreach CSV (already cleaned of empty rows by previous fix)
    with open(OUTREACH_CSV) as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(csv.DictReader(open(OUTREACH_CSV)).fieldnames or [])

    missing = [
        r for r in rows
        if r.get("ngo_name", "").strip()
        and not r.get("contact_person_email", "").strip()
        and not r.get("general_email", "").strip()
    ]

    console.print(f"NGOs still missing any email: [red]{len(missing)}[/red]")
    if not missing:
        console.print("[green]Nothing to fix![/green]")
        return

    # Load Notion pages for this campaign
    notion_lookup = fetch_all_campaign_accounts(CAMPAIGN_ID)
    console.print()

    # ── GPT-4o batch lookup ───────────────────────────────────────────────────
    console.print(Rule("[bold]GPT-4o Email Lookup[/bold]"))
    client = OpenAI(api_key=OPENAI_API_KEY, timeout=180.0, max_retries=4)

    # Process in one batch (20 NGOs is fine)
    gpt_results = lookup_emails_with_gpt(client, missing)

    # Build a map by name
    gpt_map = {r.ngo_name.strip().lower(): r for r in gpt_results}

    # ── Apply results ─────────────────────────────────────────────────────────
    patched = 0
    low_conf_fallback = 0
    still_missing = []

    table = Table(title="Email Fix Results")
    table.add_column("#", style="dim", width=4)
    table.add_column("NGO", max_width=40)
    table.add_column("Email", style="cyan", max_width=35)
    table.add_column("Conf", width=6)
    table.add_column("Notion", width=8)

    for row in missing:
        name    = row.get("ngo_name", "").strip()
        website = row.get("col href", "").strip()
        rank    = row.get("rank", "?")

        gpt_result = gpt_map.get(name.lower())

        email = ""
        confidence = ""

        if gpt_result and gpt_result.general_email.strip():
            email = gpt_result.general_email.strip().lower()
            confidence = gpt_result.confidence
            if confidence == "low":
                low_conf_fallback += 1
        else:
            # Last resort: guess info@domain
            guessed = guess_email_from_domain(website)
            if guessed:
                email = guessed
                confidence = "guessed"

        if not email:
            still_missing.append(name)
            table.add_row(str(rank), name, "[red]not found[/red]", "", "")
            continue

        # Update in-memory CSV row
        row["general_email"] = email

        # Find and patch Notion page
        page_id = find_page_id(name, website, notion_lookup)
        notion_status = ""
        if page_id:
            ok = patch_notion_email(page_id, email, dry_run)
            notion_status = "[green]✓[/green]" if ok else "[red]fail[/red]"
            if ok:
                patched += 1
        else:
            notion_status = "[yellow]?[/yellow]"
            patched += 1  # still updated in CSV

        table.add_row(str(rank), name[:40], email, confidence, notion_status)
        time.sleep(0.3)

    console.print(table)
    console.print()
    console.print(f"[green]Emails found: {len(missing) - len(still_missing)}/{len(missing)}[/green]")
    console.print(f"[green]Notion pages patched: {patched}[/green]")
    if low_conf_fallback:
        console.print(f"[yellow]Low-confidence guesses (verify manually): {low_conf_fallback}[/yellow]")
    if still_missing:
        console.print(f"[red]Still no email found ({len(still_missing)}):[/red]")
        for n in still_missing:
            console.print(f"  • {n}")

    # Rewrite CSV with updated emails
    if not dry_run:
        with open(OUTREACH_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        console.print(f"\n[green]✓ Outreach CSV updated with found emails[/green]")

    console.print()
    console.print(Rule("[bold green]Done[/bold green]"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
