# TUM Social AI — Project Applications Pipeline

Automated processing pipeline for Tally form submissions landing in the Notion **Requirements DB**.

## What it does

Two agents run automatically whenever new project applications arrive:

| Agent | Script | What it writes to Notion |
|---|---|---|
| **AI GTM Expert** | `requirements_analyzer.py` | `Sugg. AI Solution` + `Remarks & Questions` — precise technical AI/ML solution proposal + follow-up questions |
| **Enrichment** | `enrich_requirements.py` | Links `Account` + `Product Owner` contact in Notion DBs, sets status → `Under Review` |

## Setup

```bash
cd project-applications
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

Create a `.env` file here (or in the parent `TUM Social AI/` folder) with:

```env
NOTION_DB_REQUIREMENTS_ID=<your-id>
NOTION_DB_ACCOUNTS_ID=<your-id>
NOTION_DB_CONTACTS_ID=<your-id>
```

API keys (`OPENAI_API_KEY`, `NOTION_TOKEN`) are loaded from the workspace root `.env`.

## Usage

```bash
# GTM Expert — analyze all new applications
python requirements_analyzer.py

# Enrichment — link contacts/accounts for new applications
python enrich_requirements.py

# Dry run (enrichment only)
python enrich_requirements.py --dry-run

# Reprocess all entries (not just new)
python enrich_requirements.py --all
```

## Scheduled execution

A launchd job (`com.tumsocialai.project-applications`) runs both scripts automatically:
- **On laptop wake** (every 15 minutes via `StartInterval`)
- Idempotent — skips entries already processed

To install/reload the launchd job:
```bash
launchctl bootout gui/$(id -u)/com.tumsocialai.project-applications 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tumsocialai.project-applications.plist
```

## Architecture

Both scripts are fully self-contained:
- No imports from other TUM Social AI packages
- Own `.env` loading (root shared keys + `TUM Social AI/.env` for DB IDs)
- Safe to upload to GitHub independently (never commit `.env`)
