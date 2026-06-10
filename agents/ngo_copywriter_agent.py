"""
NGO Copywriter Agent — Generates personalized German cold outreach emails
for TUM Social AI Club's NGO AI partnership campaign.

For each enriched NGO, generates a short, personalized email in German
using the RRR framework (Relevance, Reward, Request).

Sender: assigned owner (Carlo Renner or Lisa Gavrilova, 50/50 per campaign)
Language: German
CTA: 20-minute exploratory call

Usage:
    python -m "agents.NGO Outreach.ngo_copywriter_agent"
    python -m "agents.NGO Outreach.ngo_copywriter_agent" --dry-run
    python -m "agents.NGO Outreach.ngo_copywriter_agent" --limit 10
    python -m "agents.NGO Outreach.ngo_copywriter_agent" --start-from 20
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# NGO Outreach/ → agents/ → tum_sales_agent/
sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
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
OUTREACH_LEARNINGS_MD = DATA_DIR / "prompts" / "outreach_learnings.md"

# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------

class NGOEmail(BaseModel):
    email_subject: str = Field(description="Email subject line in German. Short, specific, no clickbait. Max 10 words.")
    email_body: str = Field(description="Full email body in German. 90-170 words. No signature block.")


# ---------------------------------------------------------------------------
# System prompt — the full copywriting brief
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = r"""You are an elite NGO outreach copywriter for TUM Social AI Club.

## ABOUT TUM SOCIAL AI CLUB
- Germany's first AI for Good student initiative, based at the Technical University of Munich.
- Helps nonprofits and social organizations adopt AI to make their mission more effective and efficient.
- Hands-on, implementation-oriented — not theoretical.
- Builds AI-for-good projects with nonprofits to create measurable social and environmental impact.
- Has collaborated with organizations such as the Red Cross and other mission-driven partners.
- Nonprofit-friendly, practical AI implementation partner — not a commercial agency.

## YOUR TASK
Write one short, personalized cold outreach email in German for the given NGO.

## RRR FRAMEWORK
- **Relevance**: Show clearly why this NGO was chosen and why the email is specifically about them.
- **Reward**: Make the value of a conversation tangible and easy to understand.
- **Request**: End with a low-friction call to action for a 20-minute chat.

## EMAIL STRUCTURE (4 short paragraphs)

**Paragraph 1: Greeting + Intro + Appreciation**
- Greeting (see rules below)
- Short intro: {SENDER_FIRST_NAME} from TUM Social AI Club, Germany's first AI for Good student initiative at TUM
- ONE sentence of genuine appreciation for the NGO's work, tied to their work area. Keep it precise and concise. Do NOT add a second flattery sentence. One is enough.
- WRONG (too much/generic): "Ich bewundere Ihre wichtige Arbeit im Bereich X. Der Beitrag Ihres Teams zum Wohlergehen vulnerabler Gruppen ist von unschätzbarem Wert."
- WRONG (inflated): "Ihre umfangreiche Arbeit in der Gesundheitsförderung in Hamburg finden wir äußerst inspirierend."
- RIGHT (concise, specific): "Ihre Arbeit in der Gesundheitsförderung in Hamburg finden wir sehr inspirierend!"
- Use "sehr inspirierend!" as the default appreciative phrase. Avoid "äußerst", "umfangreich", "von unschätzbarem Wert".

**Paragraph 2: What we do**
- One sentence on what TUM Social AI Club does for nonprofits
- Mention both internal AI use (processes, reporting) AND mission-related AI support

**Paragraph 3: Personalized AI angle**
- Present the ai_partnership_angle as an EXAMPLE, not a definitive statement. Use framing like "könnte zum Beispiel", "eine Zusammenarbeit könnte beispielsweise", etc.
- THEN explicitly mention openness to hear about OTHER pressing challenges or bottlenecks the NGO is currently experiencing. This is important: the AI angle is just one idea, but we genuinely want to learn what their real pain points are.
- WRONG: "KI kann ein wirksames Mittel sein, um X zu optimieren."
- WRONG: "Gleichzeitig würden wir sehr gerne von Ihnen hören, welche anderen dringenden Herausforderungen oder Engpässe bei Ihrer Arbeit bestehen."
- RIGHT: "Eine Zusammenarbeit könnte zum Beispiel spannend sein, um KI für X einzusetzen. Gleichzeitig möchten wir gerne erfahren, wo aktuell die größten Herausforderungen oder Engpässe in Ihrem Alltag liegen."

**Paragraph 4: CTA**
- Ask for a short 20-minute conversation using: "Hätten Sie Interesse an einem kurzen 20-minütigen Austausch?"
- Mention: if this topic sits better with a colleague (IT, Digitalisierung, Innovation, Projekte), please forward
- Closing line: "Beste Grüße," or "Viele Grüße," (always with comma), then sender's first name "{SENDER_FIRST_NAME}" on the very next line

## GREETING RULES
- If contact person exists: "Hallo Herr/Frau [Nachname]," (infer gender from first name)
- If gender unclear: "Hallo [Vollständiger Name],"
- If no contact person: "Liebes Team von [NGO Name],"
- Never "Sehr geehrte Damen und Herren"
- Never English greetings

## SENDER
- Sender is {SENDER_NAME}
- Do NOT include a signature block, title, phone, website
- End with ONLY a short closing line like "Beste Grüße" or "Viele Grüße" followed by the sender's first name "{SENDER_FIRST_NAME}" on a new line — nothing else after that
- Example closing: "Beste Grüße\n\n{SENDER_FIRST_NAME}"

## LENGTH RULES
- 90 to 150 words ideal
- Hard maximum: 170 words
- 4 short paragraphs max
- No bullet points, no long intro, no fluff

## STYLE RULES
- German language throughout
- Tone: thoughtful, concise, warm, intellectually credible, nonprofit-sensitive
- NOT: startup-bro, corporate salesy, overhyped
- AVOID: "revolutionieren", "disrupten", "bahnbrechend", "Gamechanger", corporate filler
- NEVER use em dashes (—) or en dashes (–). Use commas, periods, or "und" instead.
- SPELLING: "missionsbezogen" (with Fugen-s), never "missionbezogen"
- GRAMMAR: "Deutschlands erster studentischen Initiative" (Dativ), never "erster studentischer Initiative"
- PREFER: practical wording, concrete but light-touch examples, modest confidence, mission-first framing

## TRANSLATION RULES
Translate English fields naturally into German:
- "Environment and Climate" → "Umwelt- und Klimaschutz"
- "Vulnerable Groups" → "Unterstützung vulnerabler Gruppen"
- "Animal Welfare and Rescue" → "Tierschutz"
- "Blind - Visually Impaired" → "Unterstützung blinder und sehbehinderter Menschen"
- "Disability Support" → "Unterstützung von Menschen mit Behinderung"
- "Mental Health" → "psychische Gesundheit"
- "Disaster Relief" → "Katastrophenhilfe"
Never translate literally if it sounds awkward. Rewrite naturally.

## SUBJECT LINE RULES
- Format: "TUM Social AI <> [NGO short name]: Pro-bono KI [optional: 1-2 word AI angle keyword]"
- The AI angle keyword is optional: only add it if the ai_partnership_angle translates into a crisp 1-2 word German label
- No clickbait, no ALL CAPS, no question marks
- Examples:
  - "TUM Social AI <> WWF: Pro-bono KI Artenschutz"
  - "TUM Social AI <> NABU: Pro-bono KI Biodiversität"
  - "TUM Social AI <> Caritas Köln: Pro-bono KI"
  - "TUM Social AI <> IOM Germany: Pro-bono KI Integration"
- Use the shortest recognizable version of the NGO name (e.g. "WWF" not "WWF Deutschland")

## CRITICAL RULES
- Never hallucinate that TUM Social AI already worked with this NGO
- Never make up facts not in the input
- Never sound like a mass email
- Every email must feel written specifically for this NGO
- If the contact is board-level/executive, keep wording strategic
- If the contact is digital/IT-oriented, slightly emphasize implementation feasibility

## QUALITY BAR
The recipient should feel:
- This is clearly about our organization
- These people understand nonprofits
- This is not a random AI pitch
- This sounds lightweight and worth replying to
- Forwarding this internally would be easy"""


# Default owners — 50/50 split assigned by rank index in the pipeline
NGO_OWNERS = ["Carlo Renner", "Lisa Gavrilova"]


def load_outreach_learnings() -> str:
    """Load processed copywriting guidance from the shared learnings file."""
    if not OUTREACH_LEARNINGS_MD.exists():
        return ""
    text = OUTREACH_LEARNINGS_MD.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    return (
        "\n\n## PROCESSED OUTREACH LEARNINGS\n"
        "Apply these processed learnings when they are relevant, while still "
        "following all higher-priority rules above:\n\n"
        f"{text}"
    )


def build_system_prompt(sender_name: str) -> str:
    """Render the system prompt template for a specific sender."""
    first_name = sender_name.split()[0]
    prompt = SYSTEM_PROMPT_TEMPLATE.replace("{SENDER_NAME}", sender_name).replace("{SENDER_FIRST_NAME}", first_name)
    return prompt + load_outreach_learnings() + load_humanized_guidance("Social Partnerships")


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_ngo_prompt(ngo: dict) -> str:
    """Build the user prompt with all available NGO fields."""
    lines = [
        "Write a personalized German cold outreach email for this NGO.\n",
        "## NGO INFORMATION",
        f"- NGO Name: {ngo.get('ngo_name', '')}",
        f"- Work Area: {ngo.get('work_area', '')}",
        f"- Sub Work Area: {ngo.get('sub_work_area', '')}",
        f"- Location: {ngo.get('listing_locations', '')}",
        f"- Website: {ngo.get('col href', '')}",
        f"- Estimated Employees: {ngo.get('estimated_employees', 'unknown')}",
        f"- AI Partnership Angle: {ngo.get('ai_partnership_angle', '')}",
    ]

    reasoning = ngo.get("reasoning", "")
    if reasoning:
        lines.append(f"- Context/Reasoning: {reasoning}")

    lines.append("\n## CONTACT PERSON")
    contact_name = ngo.get("selected_contact_name", "").strip()
    if contact_name:
        lines.append(f"- Name: {contact_name}")
        lines.append(f"- Role: {ngo.get('selected_contact_role', '')}")
        lines.append(f"- Type: {ngo.get('decision_maker_type', '')}")
    else:
        lines.append("- No named contact available. Use team greeting.")

    email = ngo.get("contact_person_email", "").strip() or ngo.get("general_email", "").strip()
    if email:
        lines.append(f"- Email: {email}")

    lines.append("\n## INSTRUCTIONS")
    lines.append("- Follow all system prompt rules exactly")
    lines.append("- Return the email subject and email body")
    lines.append("- Email body: 90-170 words, German, 4 paragraphs max")
    lines.append("- Subject: short, specific, German")

    return "\n".join(lines)


def validate_ngo_email(email: NGOEmail) -> list[str]:
    """Return quality issues for German NGO outreach."""
    issues = []
    combined = f"{email.email_subject}\n{email.email_body}"
    lowered = combined.lower()

    if "—" in combined or "–" in combined or " -- " in combined:
        issues.append("contains an em/en dash or double-hyphen aside")
    for phrase in [
        "sehr geehrte damen und herren",
        "äußerst",
        "umfangreiche arbeit",
        "von unschätzbarem wert",
        "revolutionieren",
        "disrupten",
        "bahnbrechend",
        "gamechanger",
        "innovativ",
        "wertvoll",
    ]:
        if phrase in lowered:
            issues.append(f"contains stiff or AI-coded phrase: {phrase}")
    if "dürfen wir" in lowered and "termin" in lowered:
        issues.append("CTA is too formal/pressure-heavy; use the lighter 20-minute exchange ask")
    if "hätten sie interesse an einem kurzen 20-minütigen austausch" not in lowered:
        issues.append("CTA should use the approved low-friction 20-minute exchange wording")
    if "missionbezogen" in lowered:
        issues.append("uses misspelling missionbezogen; use missionsbezogen")
    if len(email.email_body.split()) > 170:
        issues.append("email body is over 170 words")

    return issues

# ---------------------------------------------------------------------------
# Email generation
# ---------------------------------------------------------------------------

def generate_email(client: OpenAI, ngo: dict, sender_name: str = "Carlo Renner") -> NGOEmail:
    """Generate a personalized outreach email for one NGO."""
    user_prompt = build_ngo_prompt(ngo)
    system_prompt = build_system_prompt(sender_name)

    last_result = None
    quality_feedback = ""
    for attempt in range(1, 4):
        response = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt + quality_feedback},
            ],
            response_format=NGOEmail,
            max_tokens=1000,
        )
        result = response.choices[0].message.parsed
        last_result = result
        # Log every attempt, not just the last one — quality-gate retries
        # are real GPT-4o spend and must show up in cost reports.
        log_api_usage(
            agent="ngo_copywriter_agent",
            action="email_generation",
            model="gpt-4o",
            usage=response.usage,
            metadata={"ngo_name": ngo.get("ngo_name", ""), "attempt": attempt},
        )
        issues = validate_ngo_email(result)
        if not issues:
            break
        quality_feedback = (
            "\n\n## QUALITY GATE FAILED\n"
            "Rewrite the subject and body before returning final copy. Fix:\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\nKeep the German natural, specific, modest, and nonprofit-sensitive."
        )
        console.print(f"  [yellow]Quality retry {attempt}/3: {len(issues)} issue(s)[/yellow]")

    if last_result is None:
        raise RuntimeError("OpenAI returned no NGO email")

    final_issues = validate_ngo_email(last_result)
    if final_issues:
        raise ValueError("Generated NGO email failed quality gate: " + "; ".join(final_issues))

    return last_result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_copywriter(dry_run: bool = False, limit: int = 0, start_from: int = 1,
                   input_csv: str = "", campaign_id: str = "NGOs_Health_Environment_Animals_DE"):
    """Main copywriting pipeline."""
    console.print("\n" + "=" * 60)
    console.print("[bold magenta]NGO Outreach Copywriter Agent[/bold magenta]")
    console.print(f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
    if dry_run:
        console.print("[yellow]DRY RUN — preview only, no CSV output[/yellow]")
    console.print("=" * 60)

    if not OPENAI_API_KEY:
        console.print("[red]OPENAI_API_KEY not set[/red]")
        return

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=180.0, max_retries=4)

    # Load enriched CSV
    csv_path = Path(input_csv) if input_csv else NGO_ENRICHED_CSV
    if not csv_path.exists():
        console.print(f"[red]Input CSV not found: {csv_path}[/red]")
        console.print("[dim]Run the enrichment agent first.[/dim]")
        return

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_headers = list(reader.fieldnames or [])
        rows = list(reader)

    console.print(f"[cyan]Loaded {len(rows)} enriched NGOs from {csv_path.name}[/cyan]")

    # Filter by start_from
    rows_to_process = [r for r in rows if int(r.get("rank", 0)) >= start_from]
    if limit > 0:
        rows_to_process = rows_to_process[:limit]

    console.print(f"[cyan]Generating emails for {len(rows_to_process)} NGOs[/cyan]\n")

    # Determine best email for each NGO
    def best_email(ngo: dict) -> str:
        return (ngo.get("contact_person_email", "").strip()
                or ngo.get("general_email", "").strip()
                or "")

    # Process
    results = []
    generated = 0
    errors = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Writing emails...", total=len(rows_to_process))

        for ngo in rows_to_process:
            name = ngo.get("ngo_name", "?")
            rank = ngo.get("rank", "?")
            contact = ngo.get("selected_contact_name", "")
            progress.update(task, description=f"[cyan]#{rank} {name[:40]}")

            # Determine owner: use row value if set, else alternate by index
            sender_name = ngo.get("owner", "").strip() or NGO_OWNERS[len(results) % 2]

            try:
                email = generate_email(client, ngo, sender_name=sender_name)
                generated += 1

                word_count = len(email.email_body.split())

                # Show preview
                progress.console.print(Panel(
                    f"[cyan]Subject:[/cyan] {email.email_subject}\n\n"
                    f"{email.email_body}\n\n"
                    f"[dim]({word_count} words | Owner: {sender_name} | To: {contact or 'Team'} | "
                    f"Email: {best_email(ngo) or 'none'})[/dim]",
                    title=f"#{rank} {name}",
                    border_style="green" if word_count <= 170 else "red",
                ))

                results.append({
                    **ngo,
                    "account_type": "nonprofit",
                    "owner": sender_name,
                    "campaign_id": campaign_id,
                    "email_subject": email.email_subject,
                    "email_body": email.email_body,
                    "email_word_count": word_count,
                    "send_to_email": best_email(ngo),
                })

            except Exception as e:
                progress.console.print(f"  [red]Error for {name}: {e}[/red]")
                errors += 1
                results.append({
                    **ngo,
                    "account_type": "nonprofit",
                    "campaign_id": campaign_id,
                    "email_subject": "",
                    "email_body": "",
                    "email_word_count": 0,
                    "send_to_email": best_email(ngo),
                })

            progress.advance(task)

    # Write output CSV
    if not dry_run and results:
        outreach_headers = input_headers + [
            h for h in ["account_type", "owner", "campaign_id", "email_subject", "email_body", "email_word_count", "send_to_email"]
            if h not in input_headers
        ]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        timestamped_path = NGO_OUTPUT_DIR / f"ngo_outreach_{timestamp}.csv"

        for dest in [NGO_OUTREACH_CSV, timestamped_path]:
            with open(dest, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=outreach_headers, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(results)

        console.print(f"\n[green]Saved to:[/green] {timestamped_path}")
        console.print(f"[green]Latest:[/green]  {NGO_OUTREACH_CSV}")

    # Summary
    console.print("\n" + "=" * 40)
    summary = Table(title="NGO Copywriter Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green")
    summary.add_row("NGOs processed", str(len(rows_to_process)))
    summary.add_row("Emails generated", str(generated))
    summary.add_row("Errors", str(errors))

    if results:
        word_counts = [r["email_word_count"] for r in results if r["email_word_count"] > 0]
        if word_counts:
            summary.add_row("Avg word count", str(round(sum(word_counts) / len(word_counts))))
            summary.add_row("Over 170 words", str(sum(1 for w in word_counts if w > 170)))
        with_email = sum(1 for r in results if r.get("send_to_email"))
        summary.add_row("With email address", str(with_email))

    console.print(summary)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate NGO outreach emails")
    parser.add_argument("--input", type=str, default="", help="Input CSV (default: ngo_enriched.csv)")
    parser.add_argument("--dry-run", action="store_true", help="Preview emails without saving")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N NGOs")
    parser.add_argument("--start-from", type=int, default=1, help="Start from rank N")
    parser.add_argument("--campaign-id", type=str, default="NGOs_Health_Environment_Animals_DE", help="Campaign ID for all rows")
    args = parser.parse_args()

    run_copywriter(
        dry_run=args.dry_run,
        limit=args.limit,
        start_from=args.start_from,
        input_csv=args.input,
        campaign_id=args.campaign_id,
    )
