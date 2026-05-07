# TUM Social AI - Social Partnerships

Command-first infrastructure for nonprofit prospect scoring, contact enrichment, sender-aware outreach copy, Notion upload, and project application intake.

The system is not a fixed weekly campaign machine. Campaign actions are run when a teammate deliberately asks for them. Lightweight project application intake can stay scheduled if this laptop owns that workflow.

## Quick Start

```bash
git clone https://github.com/tumsocialai/social-partnerships.git
cd social-partnerships
python3 -m venv venv
source venv/bin/activate   # Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.template .env
python -m agents.run_ngo_pipeline --help
```

## Main Commands

| Task | Command |
|---|---|
| Full campaign run | `python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv"` |
| Full dry run | `python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv" --dry-run` |
| Score/rank prospects | `python -m agents.ngo_partner_agent --csv "data/inputs/my_ngos.csv" --min-score 6 --top 25` |
| Enrich contacts | `python -m agents.ngo_enrichment_agent` |
| Generate outreach copy | `python -m agents.ngo_copywriter_agent --campaign-id "NGOs_DDMMYYYY_Topic"` |
| Preview outreach copy | `python -m agents.ngo_copywriter_agent --dry-run --limit 5` |
| Upload outreach CSV to Notion | `python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv"` |
| Preview Notion upload | `python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv" --dry-run --limit 5` |
| Process project applications manually | `cd project-applications && python requirements_analyzer.py && python enrich_requirements.py` |

## Agent Logic

| Agent / flow | Built on |
|---|---|
| `ngo_partner_agent` | GPT-4o scoring with size/impact, mission + AI applicability, and establishment/track record. |
| `ngo_enrichment_agent` | Website crawl of homepage, impressum, about, team, leadership, contact, digital, and innovation pages; then structured decision-maker extraction. |
| `ngo_copywriter_agent` | German RRR outreach prompt: relevance, reward, request, nonprofit-sensitive tone, no invented facts, 20-minute CTA, sender-specific sign-off. |
| `notion_import_ngos` | Domain/name deduplication and CSV-to-Notion mapping for Accounts, campaign fields, suspected contacts, and outreach copy. |
| Project applications intake | AI GTM analysis of Project Requirements plus Account/Product Owner linking into Notion. |

## Input Requirements

The NGO sourcing agents need a source CSV before they can start. The current safest shape is an NGO.base-style CSV with:

| Column | Requirement | Purpose |
|---|---|---|
| `ngo_name` | Required | Organization identity and dedup. |
| `col href` | Strongly recommended | NGO profile or website URL for enrichment. |
| `work_area` | Strongly recommended | Main mission category for scoring and campaign naming. |
| `sub_work_area` | Helpful | More specific mission context for personalization. |
| `listing_locations` | Helpful | Region/country/city relevance. |
| `account_type` | Optional | Defaults to NGO/nonprofit behavior if missing. |

The current acquisition workflow is: scrape NGO.base by category and region with the Instant Data Scraper Chrome extension, export CSV, save it in `data/inputs/`, normalize headers if needed, then run a dry run.

Other source options still worth exploring: DZI, EU Transparency Register, Council of Europe INGO database, GlobalGiving Atlas, national charity registers, coalition/member pages, grant-winner pages, event participant lists, news/press releases, and LinkedIn Boolean searches.

## Requirements

- Python 3.9+
- OpenAI API key
- Notion integration token with access to Accounts and Contacts databases
- `NOTION_DB_REQUIREMENTS_ID` for the project applications flow
- Optional Gmail app password for completion reports
- Codex, Claude Code, Antigravity, or a normal terminal

Important: the Notion Contacts database must be shared with the active integration. A correct database ID can still return `404` if the integration has not been invited to the database.

## Environment Variables

```bash
OPENAI_API_KEY=
NOTION_TOKEN=
NOTION_DB_ACCOUNTS_ID=
NOTION_DB_CONTACTS_ID=
NOTION_DB_REQUIREMENTS_ID=
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

## Docs

Open [ONBOARDING.md](ONBOARDING.md) for the full teammate guide, or open [ONBOARDING.html](ONBOARDING.html) locally for a cleaner browser version.

Processed copywriter learnings live in [data/prompts/outreach_learnings.md](data/prompts/outreach_learnings.md).

## Automation Policy

Keep scheduled only when useful: project applications and requirements enrichment intake.

Run on demand: campaign ranking, enrichment, copywriting, Notion upload, cleanup, and feedback/learning updates.

## License

Proprietary - TUM Social AI - https://tum-socialaiclub.de
