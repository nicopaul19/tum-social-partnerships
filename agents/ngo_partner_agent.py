"""
NGO Partnership Agent — Scores and ranks NGOs from ngobase exports
for AI partnership potential with TUM Social AI.

Criteria:
  1. Size / Impact (larger = better, more reach)
  2. Mission Alignment (cool, socially impactful mission suited for AI)
  3. Establishment (longer track record = more reliable partner)

Data sources referenced by the model:
  - Vereinsregister (founding year, legal existence)
  - BZSt Zuwendungsempfängerregister (charitable status)
  - Unternehmensregister / Bundesanzeiger (financial statements)
  - DZI-Spendensiegel (trust, governance)
  - Deutscher Spendenrat (transparency)

Usage:
    python -m agents.NGO\ Outreach.ngo_partner_agent --csv <path_to_ngobase_csv>
    python -m agents.NGO\ Outreach.ngo_partner_agent --csv file1.csv file2.csv
    python -m agents.NGO\ Outreach.ngo_partner_agent --csv <path> --min-score 6
    python -m agents.NGO\ Outreach.ngo_partner_agent --csv <path> --top 20
    python -m agents.NGO\ Outreach.ngo_partner_agent --csv <path> --append  # merge with existing ngo_ranked.csv
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

# Allow imports from project root
# NGO Outreach/ → agents/ → tum_sales_agent/
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from utils.config import OPENAI_API_KEY, DATA_DIR, TABLES_DIR
from utils.api_logger import log_api_usage

console = Console()

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------
NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
NGO_RANKED_CSV = NGO_OUTPUT_DIR / "ngo_ranked.csv"

# Columns added by the agent (scoring + enrichment)
AGENT_COLUMNS = [
    "rank",
    "size_score",
    "mission_score",
    "establishment_score",
    "total_score",
    "estimated_founding_year",
    "estimated_employees",
    "has_dzi_seal",
    "reasoning",
    "ai_partnership_angle",
]

# ---------------------------------------------------------------------------
# Pydantic models for structured output
# ---------------------------------------------------------------------------

class NGOAssessment(BaseModel):
    ngo_name: str = Field(description="Name of the NGO as given")
    size_score: float = Field(ge=0, le=10, description="Size/impact score 0-10. 0=tiny/unknown, 10=massive national org")
    mission_score: float = Field(ge=0, le=10, description="Mission coolness & AI-applicability 0-10")
    establishment_score: float = Field(ge=0, le=10, description="Track record score 0-10. 0=brand new/unknown, 10=100+ years established")
    estimated_founding_year: Optional[int] = Field(default=None, description="Best estimate of founding year, null if unknown")
    estimated_employees: Optional[str] = Field(default=None, description="Rough employee count estimate, e.g. '50-100', '1000+', null if unknown")
    has_dzi_seal: Optional[bool] = Field(default=None, description="Whether org has DZI-Spendensiegel, null if unknown")
    reasoning: str = Field(description="Brief reasoning for scores (2-3 sentences)")
    ai_partnership_angle: str = Field(default="", description="How AI could help this NGO (1 sentence)")


class BatchAssessment(BaseModel):
    assessments: List[NGOAssessment]


# ---------------------------------------------------------------------------
# Scoring prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert analyst evaluating German NGOs for AI partnership potential with TUM Social AI, a student initiative at TU Munich that builds AI solutions for social impact organizations.

You will receive a batch of NGOs with their name, work area, website, and location. For each, provide scores and assessment.

## Scoring Criteria

### Size / Impact (0-10)
Score based on organizational size, budget, staff count, and geographic reach.
Use your knowledge of these organizations from German public registries:
- Vereinsregister: legal form, registered offices
- Unternehmensregister / Bundesanzeiger: published financial statements
- General public knowledge of the organization's scale

Scoring guide:
- 9-10: Major national org (e.g. DRK, Caritas, Diakonie) — thousands of employees, hundreds of millions budget
- 7-8: Large regional or well-known national org — hundreds of employees, tens of millions budget
- 5-6: Medium-sized, established regional presence — dozens of employees
- 3-4: Small but operational org — handful of employees
- 1-2: Very small local initiative or volunteer-only
- 0: Cannot determine / appears to be a single project

### Mission Coolness & AI Applicability (0-10)
How compelling is the mission, and how much could AI help?
- 9-10: Fascinating mission where AI could be transformative (e.g. mental health chatbots, disaster prediction, accessibility tech)
- 7-8: Strong social mission with clear AI use cases
- 5-6: Good mission, some AI potential
- 3-4: Standard mission, limited AI applicability
- 1-2: Very niche or unclear mission

### Establishment / Track Record (0-10)
How long has the org existed and how reliable is it?
Reference: Vereinsregister founding dates, DZI-Spendensiegel, Deutscher Spendenrat membership
- 9-10: 50+ years, well-known institution, DZI seal holder
- 7-8: 20-50 years, solid reputation
- 5-6: 10-20 years, established
- 3-4: 5-10 years
- 1-2: Under 5 years or unclear history
- 0: Cannot determine

## Important
- Be honest when you don't know — use null for unknown fields
- German NGOs you don't recognize at all should get LOW scores (not medium)
- Organizations with "e.V." are registered associations (Vereinsregister)
- Organizations with "gGmbH" are charitable limited companies (check Bundesanzeiger)
- Filter aggressively: we want QUALITY over quantity for partnership outreach"""


def build_user_prompt(ngos: list[dict]) -> str:
    """Build the user prompt for a batch of NGOs."""
    lines = ["Evaluate these NGOs for AI partnership potential:\n"]
    for i, ngo in enumerate(ngos, 1):
        lines.append(
            f"{i}. **{ngo['ngo_name']}**\n"
            f"   Work area: {ngo['work_area']} — {ngo['sub_work_area']}\n"
            f"   Website: {ngo['col href']}\n"
            f"   Location: {ngo['listing_locations']}\n"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def load_ngobase_csv(csv_path: str) -> tuple[list[dict], list[str]]:
    """Load NGOs from an ngobase CSV export. Returns (rows, original_headers)."""
    path = Path(csv_path)
    if not path.exists():
        console.print(f"[red]CSV not found: {path}[/red]")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    console.print(f"[cyan]Loaded {len(rows)} NGOs from {path.name}[/cyan]")
    return rows, headers


def load_existing_ranked() -> list[dict]:
    """Load existing ngo_ranked.csv if it exists."""
    if not NGO_RANKED_CSV.exists():
        return []
    with open(NGO_RANKED_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    console.print(f"[cyan]Loaded {len(rows)} existing ranked NGOs from ngo_ranked.csv[/cyan]")
    return rows


def score_batch(client: OpenAI, ngos: list[dict], batch_num: int, total_batches: int) -> list[NGOAssessment]:
    """Score a batch of NGOs using GPT-4o."""
    user_prompt = build_user_prompt(ngos)

    response = client.beta.chat.completions.parse(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=BatchAssessment,
        max_tokens=4000,
    )

    log_api_usage(
        agent="ngo_partner_agent",
        action="ngo_batch_scoring",
        model="gpt-4o",
        usage=response.usage,
        metadata={"batch": batch_num, "batch_size": len(ngos)},
    )

    result = response.choices[0].message.parsed
    return result.assessments


def build_output_row(rank: int, assessment: NGOAssessment, total: float, orig: dict) -> dict:
    """Build a single output row combining original CSV data with agent scores."""
    row = {"rank": rank}
    # Copy ALL original columns from the input CSV
    for key, value in orig.items():
        row[key] = value
    # Add agent scoring columns
    row["size_score"] = assessment.size_score
    row["mission_score"] = assessment.mission_score
    row["establishment_score"] = assessment.establishment_score
    row["total_score"] = total
    row["estimated_founding_year"] = assessment.estimated_founding_year or ""
    row["estimated_employees"] = assessment.estimated_employees or ""
    row["has_dzi_seal"] = assessment.has_dzi_seal if assessment.has_dzi_seal is not None else ""
    row["reasoning"] = assessment.reasoning
    row["ai_partnership_angle"] = assessment.ai_partnership_angle
    return row


def run_scoring(csv_paths: list[str], min_score: float = 5.0, top_n: int = 0,
                batch_size: int = 15, append: bool = False):
    """Main scoring pipeline."""
    if not OPENAI_API_KEY:
        console.print("[red]OPENAI_API_KEY not set in .env[/red]")
        sys.exit(1)

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=180.0, max_retries=4)

    # Load all input CSVs and collect original headers
    all_ngos = []
    all_input_headers = []
    for csv_path in csv_paths:
        ngos, headers = load_ngobase_csv(csv_path)
        all_ngos.extend(ngos)
        for h in headers:
            if h not in all_input_headers:
                all_input_headers.append(h)

    # Deduplicate by ngo_name (keep first occurrence)
    seen_names = set()
    unique_ngos = []
    for ngo in all_ngos:
        name = ngo.get("ngo_name", "").strip().lower()
        if name and name not in seen_names:
            seen_names.add(name)
            unique_ngos.append(ngo)
        elif name in seen_names:
            console.print(f"[dim]Dedup: skipping duplicate '{ngo.get('ngo_name')}'[/dim]")

    # If appending, skip NGOs already in ranked CSV
    existing_rows = []
    existing_names = set()
    if append:
        existing_rows = load_existing_ranked()
        existing_names = {r.get("ngo_name", "").strip().lower() for r in existing_rows}
        before = len(unique_ngos)
        unique_ngos = [n for n in unique_ngos if n.get("ngo_name", "").strip().lower() not in existing_names]
        skipped = before - len(unique_ngos)
        if skipped:
            console.print(f"[yellow]Skipped {skipped} NGOs already in ranked CSV[/yellow]")

    if not unique_ngos:
        console.print("[yellow]No new NGOs to score.[/yellow]")
        return []

    # Split into batches
    batches = [unique_ngos[i:i + batch_size] for i in range(0, len(unique_ngos), batch_size)]
    total_batches = len(batches)
    console.print(f"[cyan]Processing {len(unique_ngos)} NGOs in {total_batches} batches of ~{batch_size}[/cyan]\n")

    all_assessments: list[NGOAssessment] = []

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("[cyan]Scoring NGOs...", total=total_batches)

        for i, batch in enumerate(batches):
            progress.update(task, description=f"[cyan]Batch {i+1}/{total_batches} ({len(batch)} NGOs)")
            try:
                results = score_batch(client, batch, i + 1, total_batches)
                all_assessments.extend(results)
            except Exception as e:
                console.print(f"[red]Batch {i+1} failed: {e}[/red]")
            progress.advance(task)

    console.print(f"\n[green]Scored {len(all_assessments)} NGOs[/green]")

    # Calculate total scores for new results
    new_scored = []
    for assessment in all_assessments:
        total = round(
            assessment.size_score * 0.40
            + assessment.mission_score * 0.35
            + assessment.establishment_score * 0.25,
            2,
        )
        # Find original row to preserve all columns
        orig = next(
            (n for n in unique_ngos if n["ngo_name"].strip().lower() == assessment.ngo_name.strip().lower()),
            next((n for n in unique_ngos if n["ngo_name"] == assessment.ngo_name), {})
        )
        new_scored.append((total, assessment, orig))

    # Filter new results by minimum score
    new_qualified = [(s, a, o) for s, a, o in new_scored if s >= min_score]
    new_filtered = len(new_scored) - len(new_qualified)
    console.print(f"[yellow]New batch: filtered out {new_filtered} NGOs below score {min_score}[/yellow]")

    # Merge with existing if appending
    if append and existing_rows:
        # Normalize existing rows: unify website/location column names
        for row in existing_rows:
            # Ensure col href and listing_locations are populated from website/location
            if not row.get("col href") and row.get("website"):
                row["col href"] = row["website"]
            if not row.get("listing_locations") and row.get("location"):
                row["listing_locations"] = row["location"]
            # And vice versa
            if not row.get("website") and row.get("col href"):
                row["website"] = row["col href"]
            if not row.get("location") and row.get("listing_locations"):
                row["location"] = row["listing_locations"]
        # Keep existing rows as-is, add new qualified ones
        all_output_rows = list(existing_rows)
        for total, assessment, orig in new_qualified:
            row = build_output_row(0, assessment, total, orig)  # rank=0 placeholder
            all_output_rows.append(row)
        # Re-sort all by total_score descending
        all_output_rows.sort(key=lambda r: float(r.get("total_score", 0)), reverse=True)
        # Re-assign ranks
        for i, row in enumerate(all_output_rows, 1):
            row["rank"] = i
    else:
        # Fresh run — only new results
        new_qualified.sort(key=lambda x: x[0], reverse=True)
        if top_n > 0:
            new_qualified = new_qualified[:top_n]
        all_output_rows = []
        for rank, (total, assessment, orig) in enumerate(new_qualified, 1):
            all_output_rows.append(build_output_row(rank, assessment, total, orig))

    console.print(f"[green]Final ranked list: {len(all_output_rows)} NGOs[/green]\n")

    # Determine output headers: rank + all original input columns + agent columns
    # Standardize: use ngobase column names, drop old aliases
    skip_headers = {"website", "location"}  # old aliases for col href / listing_locations
    output_headers = ["rank"]
    for h in all_input_headers:
        if h not in output_headers and h not in skip_headers:
            output_headers.append(h)
    # Also include any headers from existing ranked CSV
    if existing_rows:
        for h in existing_rows[0].keys():
            if h not in output_headers and h not in skip_headers:
                output_headers.append(h)
    # Add agent columns
    for h in AGENT_COLUMNS:
        if h not in output_headers:
            output_headers.append(h)

    # Save timestamped + canonical
    NGO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_path = NGO_OUTPUT_DIR / f"ngo_ranked_{timestamp}.csv"

    for dest in [output_path, NGO_RANKED_CSV]:
        with open(dest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=output_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_output_rows)

    console.print(f"[green]Saved to:[/green] {output_path}")
    console.print(f"[green]Latest:[/green]  {NGO_RANKED_CSV}")

    # Print results table
    table = Table(title=f"Top NGO Partnership Candidates (min score: {min_score})")
    table.add_column("#", style="dim", width=4)
    table.add_column("NGO", style="cyan", max_width=45)
    table.add_column("Area", style="dim", max_width=20)
    table.add_column("Location", style="dim", width=10)
    table.add_column("Size", justify="center", width=5)
    table.add_column("Mission", justify="center", width=7)
    table.add_column("Estab.", justify="center", width=6)
    table.add_column("TOTAL", justify="center", style="bold green", width=6)
    table.add_column("AI Angle", style="yellow", max_width=50)

    for row in all_output_rows:
        table.add_row(
            str(row.get("rank", "")),
            str(row.get("ngo_name", "")),
            str(row.get("work_area", "")),
            str(row.get("listing_locations", row.get("location", ""))),
            str(row.get("size_score", "")),
            str(row.get("mission_score", "")),
            str(row.get("establishment_score", "")),
            str(row.get("total_score", "")),
            str(row.get("ai_partnership_angle", ""))[:50],
        )

    console.print()
    console.print(table)

    console.print(f"\n[dim]Total: {len(all_output_rows)} ranked NGOs[/dim]")

    return all_output_rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score NGOs from ngobase CSV for AI partnership potential")
    parser.add_argument("--csv", type=str, nargs="+", required=True, help="Path(s) to ngobase CSV export(s)")
    parser.add_argument("--min-score", type=float, default=5.0, help="Minimum weighted score to qualify (default: 5.0)")
    parser.add_argument("--top", type=int, default=0, help="Only keep top N results (default: all above min-score)")
    parser.add_argument("--batch-size", type=int, default=15, help="NGOs per API batch (default: 15)")
    parser.add_argument("--append", action="store_true", help="Merge new results with existing ngo_ranked.csv")
    args = parser.parse_args()

    run_scoring(
        csv_paths=args.csv,
        min_score=args.min_score,
        top_n=args.top,
        batch_size=args.batch_size,
        append=args.append,
    )
