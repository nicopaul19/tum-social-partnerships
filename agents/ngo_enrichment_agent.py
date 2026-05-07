"""
NGO Enrichment Agent — Finds the best partnership decision-maker and email
for each ranked NGO by crawling their public website.

For each NGO:
  1. Fetches homepage, impressum, team/about, contact pages
  2. Sends page content to GPT-4o for structured contact extraction
  3. Outputs enriched CSV with contact details, emails, confidence scores

Usage:
    python -m agents.NGO\ Outreach.ngo_enrichment_agent
    python -m agents.NGO\ Outreach.ngo_enrichment_agent --limit 10        # only first N
    python -m agents.NGO\ Outreach.ngo_enrichment_agent --start-from 20   # resume from rank 20
    python -m agents.NGO\ Outreach.ngo_enrichment_agent --input custom.csv
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

# NGO Outreach/ → agents/ → tum_sales_agent/
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from utils.config import OPENAI_API_KEY, DATA_DIR
from utils.api_logger import log_api_usage

console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
NGO_RANKED_CSV = NGO_OUTPUT_DIR / "ngo_ranked.csv"
NGO_ENRICHED_CSV = NGO_OUTPUT_DIR / "ngo_enriched.csv"
PROGRESS_FILE = NGO_OUTPUT_DIR / "enrichment_progress.json"

# ---------------------------------------------------------------------------
# Web fetching
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

# Pages to look for (in priority order)
TARGET_PAGES = [
    # (pattern keywords for link text or href, page label)
    (["impressum", "imprint", "legal"], "impressum"),
    (["ueber-uns", "über uns", "about-us", "about us", "about", "wer-wir-sind", "who-we-are"], "about"),
    (["team", "unser-team", "staff", "people", "mitarbeiter", "menschen"], "team"),
    (["vorstand", "leadership", "management", "geschaeftsfuehrung", "geschäftsführung", "board", "leitung", "gremien"], "leadership"),
    (["kontakt", "contact", "get-in-touch", "ansprechpartner"], "contact"),
    (["digital", "innovation", "it", "technologie", "technology"], "digital"),
    (["organisation", "organization", "struktur", "structure", "organigramm"], "organization"),
]

REQUEST_TIMEOUT = 15
FETCH_DELAY = 1.0  # seconds between requests to be polite


def fetch_page(url: str) -> Optional[str]:
    """Fetch a page and return cleaned text content."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def extract_text_from_html(html: str, max_chars: int = 12000) -> str:
    """Extract readable text from HTML, preserving emails and structure."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script, style, nav, footer noise
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()

    # Get text with some structure
    text = soup.get_text(separator="\n", strip=True)

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text[:max_chars]


_PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.de"}
_PLACEHOLDER_EMAIL_LOCALS = {
    "john.doe", "jane.doe", "max.mustermann", "erika.mustermann",
    "max.muster", "vorname.nachname", "firstname.lastname",
    "name.surname", "your.name", "ihr.name",
}

def _is_placeholder_email(email: str) -> bool:
    """Return True for documentation/form-example emails that are never real contacts."""
    local, _, domain = email.partition("@")
    if domain in _PLACEHOLDER_EMAIL_DOMAINS:
        return True
    if local in _PLACEHOLDER_EMAIL_LOCALS:
        return True
    return False


def extract_emails_from_html(html: str) -> list[str]:
    """Extract email addresses from HTML source."""
    # Standard email pattern
    emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html)
    # Also check mailto: links
    mailto = re.findall(r"mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})", html)
    all_emails = list(set(e.lower() for e in emails + mailto))
    # Filter out image extensions, placeholder/example addresses
    return [
        e for e in all_emails
        if not e.endswith(".png")
        and not e.endswith(".jpg")
        and not _is_placeholder_email(e)
    ]


def find_subpage_urls(html: str, base_url: str) -> dict[str, str]:
    """Find relevant subpage URLs from homepage HTML."""
    soup = BeautifulSoup(html, "html.parser")
    found = {}

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        text = link.get_text(strip=True).lower()
        href_lower = href.lower()

        # Build absolute URL
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("#"):
            continue
        abs_url = urljoin(base_url, href)

        # Only follow same-domain links
        if urlparse(abs_url).netloc != urlparse(base_url).netloc:
            continue

        for keywords, label in TARGET_PAGES:
            if label in found:
                continue
            for kw in keywords:
                if kw in href_lower or kw in text:
                    found[label] = abs_url
                    break

    return found


def crawl_ngo_website(website_url: str) -> dict[str, dict]:
    """
    Crawl an NGO website and return page contents.
    Returns: {page_label: {"url": ..., "text": ..., "emails": [...]}}
    """
    pages = {}

    # 1. Fetch homepage
    homepage_html = fetch_page(website_url)
    if not homepage_html:
        return pages

    pages["homepage"] = {
        "url": website_url,
        "text": extract_text_from_html(homepage_html),
        "emails": extract_emails_from_html(homepage_html),
    }

    # 2. Find subpage URLs
    subpage_urls = find_subpage_urls(homepage_html, website_url)

    # 3. Also try common URL patterns if not found via links
    base = website_url.rstrip("/")
    common_paths = {
        "impressum": ["/impressum", "/impressum/", "/imprint", "/legal-notice"],
        "about": ["/ueber-uns", "/about", "/about-us", "/wer-wir-sind"],
        "team": ["/team", "/unser-team", "/people", "/staff", "/mitarbeiter"],
        "leadership": ["/vorstand", "/leadership", "/management", "/geschaeftsfuehrung", "/board"],
        "contact": ["/kontakt", "/contact", "/get-in-touch"],
    }
    for label, paths in common_paths.items():
        if label not in subpage_urls:
            for path in paths:
                test_url = base + path
                try:
                    resp = requests.head(test_url, headers=HEADERS, timeout=8, allow_redirects=True)
                    if resp.status_code == 200:
                        subpage_urls[label] = test_url
                        break
                except Exception:
                    continue

    # 4. Fetch each subpage
    for label, url in subpage_urls.items():
        time.sleep(FETCH_DELAY)
        html = fetch_page(url)
        if html:
            pages[label] = {
                "url": url,
                "text": extract_text_from_html(html),
                "emails": extract_emails_from_html(html),
            }

    return pages


# ---------------------------------------------------------------------------
# Pydantic model for structured GPT-4o output
# ---------------------------------------------------------------------------

class ContactExtraction(BaseModel):
    selected_contact_name: str = Field(default="", description="Full name of the best decision-maker contact")
    selected_contact_role: str = Field(default="", description="Their role/title as shown on the website")
    decision_maker_type: str = Field(description="One of: ceo_executive, board_chair, managing_director, founder, digital_it_lead, innovation_lead, partnerships_lead, general_management, generic_contact_only, no_valid_contact_found")
    contact_person_email: str = Field(default="", description="Direct email of the selected person, if found")
    general_email: str = Field(default="", description="General organizational email (info@, kontakt@, etc.)")
    source_url_contact: str = Field(default="", description="URL where the contact person was found")
    source_url_email: str = Field(default="", description="URL where the email was found")
    evidence_snippet_contact: str = Field(default="", description="Exact text snippet from page showing the person's name and role")
    evidence_snippet_email: str = Field(default="", description="Exact text snippet showing the email")
    confidence_score: int = Field(ge=0, le=100, description="0-100 confidence score")
    notes: str = Field(default="", description="Brief reasoning for selection")


# ---------------------------------------------------------------------------
# GPT-4o analysis prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a contact research expert for TUM Social AI, a student initiative at TU Munich that builds AI solutions for social impact organizations.

Your job: Given scraped page content from an NGO website, identify the BEST person to approach for an AI partnership conversation, plus the best available email.

## Contact Selection Rules

**For small/mid-sized NGOs (e.V., small Stiftung, <100 employees):**
Prefer in order: Vorstandsvorsitzende:r > Vorsitzende:r > Geschäftsführer:in > Managing Director > CEO > Founder

**For larger NGOs (gGmbH, foundations, federations, hospitals, 100+ employees):**
Prefer in order: Geschäftsführer:in/CEO > Vorstand > Generalsekretär:in > Director Digital > CIO/IT-Leitung > Innovation Lead > Partnerships Lead

**Avoid:** Press contacts, donation/fundraising contacts, volunteer coordinators, generic office managers — unless nothing better exists.

**If the NGO is a local chapter of a larger org:** Choose the person responsible for that local entity.
**If global HQ site but row refers to German branch:** Prioritize Germany-specific leadership.

## Email Rules
- `contact_person_email`: Direct email of selected person ONLY if explicitly shown on site
- `general_email`: General org email (info@, kontakt@, etc.)
- Both can be filled. Never invent emails.
- If email follows pattern (firstname.lastname@domain) but isn't explicitly shown, mention in notes and reduce confidence.
- Normalize emails to lowercase.
- NEVER return placeholder or example emails such as john.doe@example.com, max.mustermann@example.com, vorname.nachname@domain.de, or any address where the local part is clearly a documentation placeholder. Leave the field empty instead.

## Confidence Scoring
- 90-100: Exact senior decision-maker found, with direct email
- 75-89: Exact senior decision-maker found, only general email
- 60-74: Good probable decision-maker, role slightly indirect or email evidence weaker
- 40-59: Only generic contact found, decision-maker unclear
- 0-39: Insufficient evidence

## Critical Rules
- NEVER hallucinate names, titles, or emails. Only return what's explicitly in the provided text.
- If no valid person is found, set decision_maker_type to "no_valid_contact_found"
- Keep evidence_snippet short (1-2 lines, verbatim from the text)
- Strip honorifics (Dr., Prof.) unless part of the displayed name on site"""


def build_analysis_prompt(ngo: dict, pages: dict[str, dict]) -> str:
    """Build the user prompt with NGO info and crawled page content."""
    lines = [
        f"# NGO: {ngo.get('ngo_name', 'Unknown')}",
        f"Website: {ngo.get('col href', ngo.get('website', ''))}",
        f"Work area: {ngo.get('work_area', '')} — {ngo.get('sub_work_area', '')}",
        f"Location: {ngo.get('listing_locations', ngo.get('location', ''))}",
        f"Estimated employees: {ngo.get('estimated_employees', 'unknown')}",
        f"AI partnership angle: {ngo.get('ai_partnership_angle', '')}",
        "",
        "---",
        "",
        "# Scraped Website Content",
        "",
    ]

    all_emails = set()
    for label, page_data in pages.items():
        lines.append(f"## Page: {label} ({page_data['url']})")
        lines.append(page_data["text"][:8000])  # Cap per page
        if page_data["emails"]:
            lines.append(f"\nEmails found on this page: {', '.join(page_data['emails'])}")
            all_emails.update(page_data["emails"])
        lines.append("")

    if all_emails:
        lines.append(f"\n## All emails found across pages: {', '.join(sorted(all_emails))}")

    if not pages:
        lines.append("(No pages could be fetched — website may be down or blocking requests)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_progress() -> set[str]:
    """Load set of already-enriched NGO names."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_progress(completed: set[str]):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(sorted(completed), f)


def enrich_ngo(client: OpenAI, ngo: dict) -> dict:
    """Enrich a single NGO with contact information."""
    website = ngo.get("col href", ngo.get("website", "")).strip()
    if not website:
        return {
            "selected_contact_name": "",
            "selected_contact_role": "",
            "decision_maker_type": "no_valid_contact_found",
            "contact_person_email": "",
            "general_email": "",
            "source_url_contact": "",
            "source_url_email": "",
            "evidence_snippet_contact": "",
            "evidence_snippet_email": "",
            "confidence_score": 0,
            "notes": "No website URL available.",
        }

    # Crawl the website
    pages = crawl_ngo_website(website)

    # Build prompt and call GPT-4o
    user_prompt = build_analysis_prompt(ngo, pages)

    try:
        response = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ContactExtraction,
            max_tokens=1000,
        )

        log_api_usage(
            agent="ngo_enrichment_agent",
            action="contact_extraction",
            model="gpt-4o",
            usage=response.usage,
            metadata={"ngo_name": ngo.get("ngo_name", "")},
        )

        result = response.choices[0].message.parsed
        return {
            "selected_contact_name": result.selected_contact_name,
            "selected_contact_role": result.selected_contact_role,
            "decision_maker_type": result.decision_maker_type,
            "contact_person_email": result.contact_person_email,
            "general_email": result.general_email,
            "source_url_contact": result.source_url_contact,
            "source_url_email": result.source_url_email,
            "evidence_snippet_contact": result.evidence_snippet_contact,
            "evidence_snippet_email": result.evidence_snippet_email,
            "confidence_score": result.confidence_score,
            "notes": result.notes,
        }

    except Exception as e:
        console.print(f"[red]  GPT-4o error: {e}[/red]")
        return {
            "selected_contact_name": "",
            "selected_contact_role": "",
            "decision_maker_type": "no_valid_contact_found",
            "contact_person_email": "",
            "general_email": "",
            "source_url_contact": "",
            "source_url_email": "",
            "evidence_snippet_contact": "",
            "evidence_snippet_email": "",
            "confidence_score": 0,
            "notes": f"GPT-4o analysis failed: {str(e)[:100]}",
        }


ENRICHMENT_COLUMNS = [
    "selected_contact_name",
    "selected_contact_role",
    "decision_maker_type",
    "contact_person_email",
    "general_email",
    "source_url_contact",
    "source_url_email",
    "evidence_snippet_contact",
    "evidence_snippet_email",
    "confidence_score",
    "notes",
]


def run_enrichment(input_csv: str = "", limit: int = 0, start_from: int = 1):
    """Main enrichment pipeline."""
    if not OPENAI_API_KEY:
        console.print("[red]OPENAI_API_KEY not set[/red]")
        sys.exit(1)

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Load input
    csv_path = Path(input_csv) if input_csv else NGO_RANKED_CSV
    if not csv_path.exists():
        console.print(f"[red]Input CSV not found: {csv_path}[/red]")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_headers = list(reader.fieldnames or [])
        rows = list(reader)

    console.print(f"[cyan]Loaded {len(rows)} NGOs from {csv_path.name}[/cyan]")

    # Filter by start_from (1-indexed rank)
    rows_to_process = [r for r in rows if int(r.get("rank", 0)) >= start_from]
    if limit > 0:
        rows_to_process = rows_to_process[:limit]

    console.print(f"[cyan]Processing {len(rows_to_process)} NGOs (rank {start_from}+){f', limit {limit}' if limit else ''}[/cyan]\n")

    # Load existing enriched data for resume capability
    enriched_data = {}
    if NGO_ENRICHED_CSV.exists():
        with open(NGO_ENRICHED_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                enriched_data[row.get("ngo_name", "")] = row

    completed = load_progress()
    total = len(rows_to_process)
    new_count = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Enriching NGOs...", total=total)

        for ngo in rows_to_process:
            name = ngo.get("ngo_name", "").strip()
            rank = ngo.get("rank", "?")

            if name in completed and name in enriched_data:
                progress.update(task, description=f"[dim]#{rank} {name[:30]} (cached)")
                skipped += 1
                progress.advance(task)
                continue

            progress.update(task, description=f"[cyan]#{rank} {name[:40]}")

            contact_info = enrich_ngo(client, ngo)

            # Merge original row + enrichment
            merged = dict(ngo)
            merged.update(contact_info)
            enriched_data[name] = merged

            completed.add(name)
            save_progress(completed)

            # Append to enriched CSV incrementally
            _append_enriched_row(merged, input_headers)

            new_count += 1
            confidence = contact_info.get("confidence_score", 0)
            dm_type = contact_info.get("decision_maker_type", "")
            contact = contact_info.get("selected_contact_name", "")
            emoji = "[green]" if confidence >= 75 else "[yellow]" if confidence >= 50 else "[red]"
            progress.console.print(
                f"  {emoji}#{rank}[/] {name[:40]} → {contact or '(none)'} "
                f"({dm_type}) [dim]conf={confidence}[/dim]"
            )

            progress.advance(task)
            time.sleep(0.5)  # Brief pause between NGOs

    console.print(f"\n[green]Done! {new_count} new, {skipped} cached[/green]")

    # Write final complete enriched CSV (sorted by rank)
    _write_final_csv(rows, enriched_data, input_headers)

    # Print summary table
    _print_summary(enriched_data, rows)

    return enriched_data


def _append_enriched_row(merged: dict, input_headers: list[str]):
    """Append a single enriched row to the CSV (for incremental progress)."""
    all_headers = input_headers + [h for h in ENRICHMENT_COLUMNS if h not in input_headers]
    write_header = not NGO_ENRICHED_CSV.exists() or NGO_ENRICHED_CSV.stat().st_size == 0

    with open(NGO_ENRICHED_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_headers, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(merged)


def _write_final_csv(original_rows: list[dict], enriched_data: dict, input_headers: list[str]):
    """Write the complete enriched CSV, preserving original rank order."""
    all_headers = input_headers + [h for h in ENRICHMENT_COLUMNS if h not in input_headers]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    final_path = NGO_OUTPUT_DIR / f"ngo_enriched_{timestamp}.csv"

    final_rows = []
    for orig in original_rows:
        name = orig.get("ngo_name", "").strip()
        if name in enriched_data:
            final_rows.append(enriched_data[name])
        else:
            final_rows.append(orig)

    for dest in [final_path, NGO_ENRICHED_CSV]:
        with open(dest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(final_rows)

    console.print(f"[green]Final CSV:[/green] {final_path}")
    console.print(f"[green]Latest:[/green]   {NGO_ENRICHED_CSV}")


def _print_summary(enriched_data: dict, original_rows: list[dict]):
    """Print a summary table of results."""
    table = Table(title="Enrichment Results Summary")
    table.add_column("#", style="dim", width=4)
    table.add_column("NGO", style="cyan", max_width=35)
    table.add_column("Contact", style="green", max_width=25)
    table.add_column("Role", style="dim", max_width=25)
    table.add_column("Type", max_width=20)
    table.add_column("Email", style="yellow", max_width=30)
    table.add_column("Conf", justify="center", width=5)

    for orig in original_rows:
        name = orig.get("ngo_name", "").strip()
        data = enriched_data.get(name, {})
        conf = data.get("confidence_score", "")
        conf_style = "green" if str(conf).isdigit() and int(conf) >= 75 else "yellow" if str(conf).isdigit() and int(conf) >= 50 else "red"
        email = data.get("contact_person_email") or data.get("general_email") or ""
        table.add_row(
            str(orig.get("rank", "")),
            name[:35],
            str(data.get("selected_contact_name", ""))[:25],
            str(data.get("selected_contact_role", ""))[:25],
            str(data.get("decision_maker_type", "")),
            email[:30],
            f"[{conf_style}]{conf}[/{conf_style}]" if conf else "",
        )

    console.print()
    console.print(table)

    # Stats
    total = len(original_rows)
    enriched = sum(1 for r in original_rows if r.get("ngo_name", "").strip() in enriched_data)
    with_contact = sum(1 for d in enriched_data.values() if d.get("selected_contact_name"))
    with_direct_email = sum(1 for d in enriched_data.values() if d.get("contact_person_email"))
    with_general_email = sum(1 for d in enriched_data.values() if d.get("general_email"))
    high_conf = sum(1 for d in enriched_data.values()
                    if str(d.get("confidence_score", "0")).isdigit() and int(d.get("confidence_score", 0)) >= 75)

    console.print(f"\n[dim]Stats: {enriched}/{total} enriched, {with_contact} with named contact, "
                  f"{with_direct_email} direct emails, {with_general_email} general emails, "
                  f"{high_conf} high confidence (75+)[/dim]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich ranked NGOs with contact information")
    parser.add_argument("--input", type=str, default="", help="Input CSV (default: ngo_ranked.csv)")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N NGOs")
    parser.add_argument("--start-from", type=int, default=1, help="Start from rank N (for resuming)")
    args = parser.parse_args()

    run_enrichment(
        input_csv=args.input,
        limit=args.limit,
        start_from=args.start_from,
    )
