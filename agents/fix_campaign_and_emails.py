"""
Fix script for NGOs_19032026_Health campaign:
  1. Rename campaign ID → NGOs_19032026_Health_CH in all Notion accounts
  2. Find missing general emails for NGOs that have neither contact nor general email
  3. Update Notion pages + outreach CSV with found emails

Usage:
    python fix_campaign_and_emails.py
    python fix_campaign_and_emails.py --dry-run
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests as http_requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from rich.console import Console
from rich.rule import Rule

from utils.config import NOTION_TOKEN, NOTION_DB_ACCOUNTS_ID, OPENAI_API_KEY, DATA_DIR
from utils.api_logger import log_api_usage

console = Console()

OLD_CAMPAIGN = "NGOs_19032026_Health"
NEW_CAMPAIGN = "NGOs_19032026_Health_CH"

NOTION_API_VERSION = "2022-06-28"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_API_VERSION,
    "Content-Type": "application/json",
}

NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
OUTREACH_CSV   = NGO_OUTPUT_DIR / "ngo_outreach.csv"
ENRICHED_CSV   = NGO_OUTPUT_DIR / "ngo_enriched.csv"

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_emails_from_html(html: str) -> list[str]:
    """Extract email addresses from HTML."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("%40", "@")
    emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    clean = []
    for e in emails:
        e = e.strip().lower().rstrip(".")
        if e not in clean and not e.endswith((".png", ".jpg", ".gif", ".svg")):
            clean.append(e)
    return clean


def fetch_page(url: str, timeout: int = 12) -> Optional[str]:
    try:
        r = http_requests.get(url, headers=WEB_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def find_contact_subpages(homepage_html: str, base_url: str) -> list[str]:
    """Find contact/impressum/about subpage URLs from homepage."""
    keywords = ["contact", "kontakt", "impressum", "imprint", "about", "über", "reach", "get-in-touch", "info"]
    soup = BeautifulSoup(homepage_html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if any(kw in href or kw in text for kw in keywords):
            full = urljoin(base_url, a["href"])
            if full not in found and full.startswith("http"):
                found.append(full)
    return found[:8]  # limit to avoid too many requests


def find_email_for_ngo(website: str) -> Optional[str]:
    """Crawl an NGO website and return the best general contact email found."""
    if not website or not website.startswith("http"):
        return None

    # Parse base URL
    parsed = urlparse(website)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    all_emails = []

    # 1. Try homepage
    html = fetch_page(website)
    if html:
        all_emails.extend(extract_emails_from_html(html))
        subpages = find_contact_subpages(html, base_url)
    else:
        subpages = []

    # 2. Try /contact, /kontakt, /impressum explicitly
    for path in ["/contact", "/kontakt", "/impressum", "/about", "/get-in-touch"]:
        subpages.append(base_url + path)

    # 3. Crawl subpages (skip duplicates)
    visited = {website}
    for url in subpages:
        if url in visited:
            continue
        visited.add(url)
        time.sleep(0.5)
        sub_html = fetch_page(url)
        if sub_html:
            all_emails.extend(extract_emails_from_html(sub_html))
        if len(all_emails) >= 3:
            break  # enough found

    if not all_emails:
        return None

    # Prefer emails that look like general contact (info@, contact@, hello@, post@, office@)
    priority_prefixes = ["info@", "contact@", "hello@", "post@", "office@", "general@", "mail@", "team@"]
    for email in all_emails:
        for prefix in priority_prefixes:
            if email.startswith(prefix):
                return email

    # Otherwise return first found
    return all_emails[0]


# ---------------------------------------------------------------------------
# Step 1: Rename campaign ID in Notion
# ---------------------------------------------------------------------------

def fetch_accounts_with_campaign(campaign_id: str) -> list[dict]:
    """Fetch all Notion account pages with the given campaign ID."""
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
            console.print(f"[red]Notion query failed: {resp.status_code} {resp.text[:200]}[/red]")
            break

        data = resp.json()
        pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    return pages


def rename_campaign_in_notion(page: dict, old_campaign: str, new_campaign: str, dry_run: bool) -> bool:
    """Replace old_campaign with new_campaign in a page's Campaign ID multi_select."""
    page_id = page["id"]
    props = page.get("properties", {})
    campaign_prop = props.get("Campaign ID", {})
    current_values = [opt["name"] for opt in campaign_prop.get("multi_select", [])]

    # Build new list: replace old with new
    new_values = []
    for v in current_values:
        if v == old_campaign:
            new_values.append(new_campaign)
        else:
            new_values.append(v)

    if set(new_values) == set(current_values):
        return True  # nothing to change

    # Get org name for logging
    title_prop = props.get("Organization*", {}).get("title", [])
    org_name = title_prop[0]["plain_text"] if title_prop else page_id

    if dry_run:
        console.print(f"[dim]DRY RUN: would rename campaign for '{org_name}'[/dim]")
        return True

    resp = http_requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {
            "Campaign ID": {"multi_select": [{"name": v} for v in new_values]}
        }},
    )

    if resp.status_code == 200:
        return True
    else:
        console.print(f"[red]Failed to rename for '{org_name}': {resp.status_code}[/red]")
        return False


# ---------------------------------------------------------------------------
# Step 2: Find & patch missing emails
# ---------------------------------------------------------------------------

def update_notion_general_email(page_id: str, email: str, dry_run: bool) -> bool:
    """Patch General Email on a Notion page."""
    if dry_run:
        return True
    resp = http_requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {
            "General Email": {"email": email}
        }},
    )
    return resp.status_code == 200


def get_page_id_for_ngo(name: str, website: str) -> Optional[str]:
    """Find Notion page ID by org name or website domain."""
    from urllib.parse import urlparse

    def norm_domain(u):
        u = u.strip().lower()
        if not u.startswith("http"):
            u = "https://" + u
        parsed = urlparse(u)
        d = parsed.netloc or parsed.path.split("/")[0]
        return d.replace("www.", "").rstrip("/")

    # Try domain match first
    domain = norm_domain(website) if website else ""

    body = {"page_size": 100}
    if name:
        body["filter"] = {
            "property": "Organization*",
            "title": {"contains": name[:50]},
        }

    resp = http_requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ACCOUNTS_ID}/query",
        headers=NOTION_HEADERS,
        json=body,
    )
    if resp.status_code != 200:
        return None

    for page in resp.json().get("results", []):
        props = page["properties"]
        # Check domain
        if domain:
            page_url = props.get("Website URL*", {}).get("url", "") or ""
            if domain and norm_domain(page_url) == domain:
                return page["id"]
        # Check name
        title_prop = props.get("Organization*", {}).get("title", [])
        page_name = title_prop[0]["plain_text"].strip().lower() if title_prop else ""
        if page_name and name and page_name == name.strip().lower():
            return page["id"]

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool = False):
    console.print()
    console.print(Rule(f"[bold magenta]NGO Campaign Fix — {OLD_CAMPAIGN} → {NEW_CAMPAIGN}[/bold magenta]"))
    if dry_run:
        console.print("[yellow]DRY RUN — no writes[/yellow]")
    console.print()

    # ── Step 1: Rename campaign ID in Notion ─────────────────────────────────
    console.print(Rule("[bold]Step 1/2 · Renaming Campaign ID in Notion[/bold]"))
    pages = fetch_accounts_with_campaign(OLD_CAMPAIGN)
    console.print(f"Found [cyan]{len(pages)}[/cyan] accounts with campaign '{OLD_CAMPAIGN}'")

    renamed = 0
    for page in pages:
        ok = rename_campaign_in_notion(page, OLD_CAMPAIGN, NEW_CAMPAIGN, dry_run)
        if ok:
            renamed += 1
        time.sleep(0.35)

    console.print(f"[green]✓ Renamed {renamed}/{len(pages)} accounts → '{NEW_CAMPAIGN}'[/green]")
    console.print()

    # ── Step 2: Find missing emails ───────────────────────────────────────────
    console.print(Rule("[bold]Step 2/2 · Finding Missing Emails[/bold]"))

    # Load outreach CSV, skip empty rows
    with open(OUTREACH_CSV) as f:
        outreach_rows = list(csv.DictReader(f))
    fieldnames = list(csv.DictReader(open(OUTREACH_CSV)).fieldnames or [])

    real_rows     = [r for r in outreach_rows if r.get("ngo_name", "").strip()]
    empty_rows    = [r for r in outreach_rows if not r.get("ngo_name", "").strip()]
    missing_email = [
        r for r in real_rows
        if not r.get("contact_person_email", "").strip()
        and not r.get("general_email", "").strip()
    ]

    console.print(f"Total rows: {len(outreach_rows)} ({len(empty_rows)} empty garbage rows stripped)")
    console.print(f"Real NGOs missing any email: [red]{len(missing_email)}[/red]")
    console.print()

    patched = 0
    still_missing = []

    for i, row in enumerate(missing_email, 1):
        name    = row.get("ngo_name", "").strip()
        website = row.get("col href", "").strip()
        rank    = row.get("rank", "?")

        console.print(f"[cyan]#{rank} {name}[/cyan] ({website})")

        email = find_email_for_ngo(website)
        if email:
            console.print(f"  → Found: [green]{email}[/green]")
            # Update in-memory row
            row["general_email"] = email

            # Find page in Notion and patch it
            page_id = get_page_id_for_ngo(name, website)
            if page_id:
                ok = update_notion_general_email(page_id, email, dry_run)
                if ok:
                    console.print(f"  → Notion updated ✓")
                    patched += 1
                else:
                    console.print(f"  → [red]Notion update failed[/red]")
            else:
                console.print(f"  → [yellow]Page not found in Notion[/yellow]")
        else:
            console.print(f"  → [red]No email found on website[/red]")
            still_missing.append(name)

        time.sleep(0.5)

    console.print()
    console.print(f"[green]Emails found & patched: {patched}/{len(missing_email)}[/green]")
    if still_missing:
        console.print(f"[yellow]Still missing ({len(still_missing)}):[/yellow]")
        for n in still_missing:
            console.print(f"  • {n}")

    # ── Rewrite outreach CSV (clean empty rows + updated emails) ─────────────
    if not dry_run:
        with open(OUTREACH_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(real_rows)  # only real rows, empty stripped

        # Also update campaign_id column in the CSV
        updated_rows = []
        for r in real_rows:
            if r.get("campaign_id", "") == OLD_CAMPAIGN:
                r["campaign_id"] = NEW_CAMPAIGN
            updated_rows.append(r)
        with open(OUTREACH_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(updated_rows)

        console.print(f"\n[green]✓ Outreach CSV rewritten: {len(updated_rows)} rows (empty rows stripped, campaign renamed)[/green]")

    console.print()
    console.print(Rule("[bold green]Fix Complete[/bold green]"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
