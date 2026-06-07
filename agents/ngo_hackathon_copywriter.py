"""
NGO Hackathon Copywriter — Generates personalized German outreach emails
for the IMPACT HACK / AI-for-Good Hackathon Africa (27-28 June 2026).

Uses a fixed hackathon template, personalizing only:
  - Greeting (Hallo Herr/Frau [Nachname] | Liebes [Org] Team)
  - Personalized hook (from ai_partnership_angle field)
  - Org name in the CTA paragraph
  - Sender (Carlo Renner / Lisa Gavrilova, 50/50)
  - Subject line

Usage:
    python -m agents.ngo_hackathon_copywriter --campaign-id NGOs_2805_HackathonAfrica
    python -m agents.ngo_hackathon_copywriter --campaign-id NGOs_2805_HackathonAfrica --dry-run
    python -m agents.ngo_hackathon_copywriter --campaign-id NGOs_2805_HackathonAfrica --limit 5
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from utils.config import OPENAI_API_KEY, DATA_DIR
from utils.api_logger import log_api_usage
from utils.copywriting_guidance import load_humanized_guidance

console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
NGO_OUTPUT_DIR = DATA_DIR / "ngo_partnerships"
NGO_ENRICHED_CSV = NGO_OUTPUT_DIR / "ngo_enriched.csv"
NGO_OUTREACH_CSV = NGO_OUTPUT_DIR / "ngo_outreach.csv"

# ---------------------------------------------------------------------------
# Owners — 50/50 split by rank index
# ---------------------------------------------------------------------------
NGO_OWNERS = ["Carlo Renner", "Lisa Gavrilova"]


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------
class HackathonEmail(BaseModel):
    email_subject: str = Field(
        description="Subject line in German. Format: 'Impact Hack Afrika <> [Short NGO name]: Challenge-Partner gesucht'. Max 12 words."
    )
    greeting: str = Field(
        description="Opening greeting only. E.g. 'Hallo Frau Müller,' or 'Liebes Team von Misereor,'"
    )
    personalized_hook_sentence: str = Field(
        description=(
            "ONE precise phrase in German completing the sentence: "
            "'Ich schreibe Ihnen, weil [ORG_NAME] genau das tut, was wir für unseren Impact Hackathon suchen: ___'. "
            "Your output fills the blank — it describes what the NGO does in Africa, e.g. "
            "'Sie begleiten äthiopische Dorfgemeinschaften mit integrierter Entwicklungshilfe in Bildung, Wasser und Landwirtschaft.' "
            "Start with 'Sie' or 'Ihr [noun]' — NOT with the org name (already in the template). "
            "Must reflect the ai_partnership_angle. No flattery, just facts. Max 25 words."
        )
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a precise German email copywriter for TUM Social AI Club.

Your only job: fill in 3 small personalized fields for a pre-written hackathon outreach email.

## GREETING RULES
- If contact_name is provided: "Hallo Herr/Frau [Nachname]," (infer gender from first name — use "Herr" for male, "Frau" for female)
- If gender is unclear or ambiguous: "Hallo [Full Name],"
- If no contact_name: "Liebes Team von [Org Name],"
- Never "Sehr geehrte Damen und Herren"
- Never English greetings

## PERSONALIZED HOOK
- Completes the sentence: "...weil [ORG] genau das tut, was wir für unseren Impact Hackathon suchen: ___"
- Start with "Sie" or "Ihr [Noun]" (the org name is ALREADY in the template before the colon)
- Factual, concrete, no hyperbole. Must reflect the ai_partnership_angle.
- Max 25 words
- Example: "Sie unterstützen äthiopische Gemeinden seit Jahrzehnten mit Wasserversorgung, Bildung und Landwirtschaft."

## SUBJECT LINE
- Format: "Impact Hack Afrika <> [Short NGO Name]: Challenge-Partner gesucht"
- Use shortest recognizable org name (e.g. "Misereor" not "Misereor e.V.")
- Keep it under 12 words total
- No clickbait, no question marks

## STYLE
- German throughout
- No em dashes (—) or en dashes (–): use commas or "und"
- GRAMMAR: "Deutschlands erster Studentenorganisation" (Dativ, gen.)
- Tone: warm, precise, nonprofit-sensitive, not salesy"""


def build_user_prompt(ngo: dict) -> str:
    contact_name = ngo.get("selected_contact_name", "").strip()
    lines = [
        f"NGO: {ngo.get('ngo_name', '')}",
        f"Work Area: {ngo.get('work_area', '')} — {ngo.get('sub_work_area', '')}",
        f"Location: {ngo.get('listing_locations', '')}",
        f"AI/Hackathon angle (use this for the hook): {ngo.get('ai_partnership_angle', '')}",
    ]
    if contact_name:
        lines.append(f"Contact name: {contact_name}")
        lines.append(f"Contact role: {ngo.get('selected_contact_role', '')}")
    else:
        lines.append("Contact: not found — use team greeting")
    return "\n".join(lines)


def build_system_prompt() -> str:
    """Return the hackathon prompt plus live social copywriting guidance."""
    return SYSTEM_PROMPT + load_humanized_guidance("Social Partnerships")


def validate_hackathon_email(fields: HackathonEmail) -> list[str]:
    """Return quality issues for hackathon email fields."""
    issues = []
    combined = f"{fields.email_subject}\n{fields.greeting}\n{fields.personalized_hook_sentence}"
    lowered = combined.lower()

    if "—" in combined or "–" in combined or " -- " in combined:
        issues.append("contains an em/en dash or double-hyphen aside")
    for phrase in [
        "sehr geehrte damen und herren",
        "äußerst",
        "umfangreich",
        "von unschätzbarem wert",
        "revolutionieren",
        "disrupten",
        "bahnbrechend",
        "gamechanger",
        "inspirierend",
        "wertvoll",
    ]:
        if phrase in lowered:
            issues.append(f"contains stiff or AI-coded phrase: {phrase}")
    if "?" in fields.email_subject:
        issues.append("subject line contains a question mark")
    if len(fields.personalized_hook_sentence.split()) > 25:
        issues.append("personalized hook is over 25 words")

    return issues


def build_full_email(greeting: str, hook: str, org_name: str, sender_first: str, sender_full: str) -> str:
    """Assemble the fixed hackathon template with the 3 dynamic fields."""
    return (
        f"{greeting}\n\n"
        f"ich bin {sender_first} vom TUM Social AI Club, Deutschlands erster Studentenorganisation, "
        f"die sich an der TU München ausschließlich dem Bau von KI-Lösungen für Nonprofits verschrieben hat.\n\n"
        f"Ich schreibe Ihnen, weil {org_name} genau das tut, was wir für unseren Impact Hackathon suchen: {hook}\n\n"
        f"Was wir vorhaben: Am 27.-28. Juni veranstalten wir unseren ersten "
        f"\"Impact Hackathon: Afrika\", einen AI-for-Good Hackathon mit über 100 Studierenden "
        f"aus TUM, LMU und HM, der sich vollständig auf Afrika konzentriert: Entwicklungshilfe, "
        f"Naturschutz und Tierschutz. Partnerorganisationen wie die UN und VENRO stellen spannende "
        f"Challenges bereit. OpenAI und Lovable unterstützen uns bei der technischen Infrastruktur.\n\n"
        f"Warum ich Ihnen schreibe: Die stärksten Hackathon-Projekte entstehen, wenn sie echte "
        f"Herausforderungen lösen, nicht erfundene Business-Cases. Wir suchen deshalb "
        f"Challenge-Partner: Organisationen, die uns ein reales operatives Problem schildern, "
        f"das mit KI lösbar sein könnte.\n\n"
        f"Gerne können wir über eine mögliche Teilnahme von {org_name} beim Impact Hack sprechen. "
        f"Teilen Sie mir einfach Ihre Verfügbarkeit nächste Woche für einen 20-minütigen Austausch mit.\n\n"
        f"Viele Grüße,\n{sender_full}\nTUM Social AI Club"
    )


def generate_email_fields(client: OpenAI, ngo: dict) -> HackathonEmail:
    user_prompt = build_user_prompt(ngo)
    system_prompt = build_system_prompt()
    last_result = None
    last_usage = None
    quality_feedback = ""
    for attempt in range(1, 4):
        response = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt + quality_feedback},
            ],
            response_format=HackathonEmail,
            temperature=0.4,
        )
        result = response.choices[0].message.parsed
        last_result = result
        last_usage = response.usage
        issues = validate_hackathon_email(result)
        if not issues:
            break
        quality_feedback = (
            "\n\n## QUALITY GATE FAILED\n"
            "Rewrite the generated fields before returning final copy. Fix:\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\nKeep the German natural, factual, specific, and nonprofit-sensitive."
        )
        console.print(f"  [yellow]Quality retry {attempt}/3: {len(issues)} issue(s)[/yellow]")

    if last_result is None:
        raise RuntimeError("OpenAI returned no hackathon email fields")
    log_api_usage(
        agent="ngo_hackathon_copywriter",
        action="generate_hackathon_email",
        model="gpt-4o",
        usage=last_usage,
        metadata={"ngo_name": ngo.get("ngo_name", "")},
    )
    final_issues = validate_hackathon_email(last_result)
    if final_issues:
        raise ValueError("Generated hackathon email failed quality gate: " + "; ".join(final_issues))
    return last_result


def run_copywriter(
    input_csv: str = "",
    campaign_id: str = "NGOs_2805_HackathonAfrica",
    limit: int = 0,
    dry_run: bool = False,
):
    csv_path = Path(input_csv) if input_csv else NGO_ENRICHED_CSV
    if not csv_path.exists():
        console.print(f"[red]Input CSV not found: {csv_path}[/red]")
        sys.exit(1)

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if limit:
        rows = rows[:limit]

    client = OpenAI(api_key=OPENAI_API_KEY)
    results = []
    input_headers = list(rows[0].keys()) if rows else []

    extra_headers = ["account_type", "owner", "campaign_id", "email_subject", "email_body",
                     "email_word_count", "send_to_email"]
    out_headers = input_headers + [h for h in extra_headers if h not in input_headers]

    console.print(f"\n[bold cyan]IMPACT HACK Copywriter[/bold cyan] — {len(rows)} NGOs | campaign: {campaign_id}\n")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(), console=console
    ) as progress:
        task = progress.add_task("Generating hackathon emails...", total=len(rows))

        for i, ngo in enumerate(rows):
            name = ngo.get("ngo_name", "?")
            progress.update(task, description=f"[cyan]{name[:45]}[/cyan]")

            sender_name = ngo.get("owner", "").strip() or NGO_OWNERS[i % 2]
            sender_first = sender_name.split()[0]
            org_name = ngo.get("ngo_name", "")

            try:
                fields = generate_email_fields(client, ngo)
                body = build_full_email(
                    greeting=fields.greeting,
                    hook=fields.personalized_hook_sentence,
                    org_name=org_name,
                    sender_first=sender_first,
                    sender_full=sender_name,
                )
                word_count = len(body.split())
                contact = ngo.get("selected_contact_name", "")
                send_to = ngo.get("contact_person_email", "") or ngo.get("general_email", "")

                merged = dict(ngo)
                merged.update({
                    "account_type": ngo.get("account_type", "nonprofit"),
                    "owner": sender_name,
                    "campaign_id": campaign_id,
                    "email_subject": fields.email_subject,
                    "email_body": body,
                    "email_word_count": word_count,
                    "send_to_email": send_to,
                })
                results.append(merged)

                if dry_run:
                    console.print(f"\n[bold]{name}[/bold] | Owner: {sender_name} | To: {contact or 'Team'} | {word_count}w")
                    console.print(f"  [dim]Subject:[/dim] {fields.email_subject}")
                    console.print(f"  [dim]Greeting:[/dim] {fields.greeting}")
                    console.print(f"  [dim]Hook:[/dim] {fields.personalized_hook_sentence}")

            except Exception as e:
                console.print(f"[red]  ERROR {name}: {e}[/red]")
                merged = dict(ngo)
                merged.update({
                    "account_type": ngo.get("account_type", "nonprofit"),
                    "owner": sender_name,
                    "campaign_id": campaign_id,
                    "email_subject": "",
                    "email_body": "",
                    "email_word_count": 0,
                    "send_to_email": "",
                })
                results.append(merged)

            progress.advance(task)

    if not dry_run and results:
        NGO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(NGO_OUTREACH_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(results)
        console.print(f"\n[green]Saved {len(results)} emails to {NGO_OUTREACH_CSV}[/green]")

    # Summary table
    table = Table(title=f"Campaign: {campaign_id}", show_header=True)
    table.add_column("NGO", style="cyan", max_width=35)
    table.add_column("Contact", max_width=25)
    table.add_column("Owner", max_width=15)
    table.add_column("Words", justify="right")
    table.add_column("Send To", max_width=35)

    for r in results:
        table.add_row(
            r.get("ngo_name", "")[:35],
            r.get("selected_contact_name", "") or "— Team",
            r.get("owner", "").split()[0],
            str(r.get("email_word_count", "")),
            r.get("send_to_email", "") or "—",
        )
    console.print(table)

    with_email = sum(1 for r in results if r.get("send_to_email"))
    with_contact = sum(1 for r in results if r.get("selected_contact_name"))
    console.print(f"\n[green]Done:[/green] {len(results)} emails | {with_contact} named contacts | {with_email} with email address")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hackathon-specific NGO email copywriter")
    parser.add_argument("--input", type=str, default="", help="Input CSV (default: ngo_enriched.csv)")
    parser.add_argument("--campaign-id", type=str, default="NGOs_2805_HackathonAfrica")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_copywriter(
        input_csv=args.input,
        campaign_id=args.campaign_id,
        limit=args.limit,
        dry_run=args.dry_run,
    )
