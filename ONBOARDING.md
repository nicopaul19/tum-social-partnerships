# TUM Social AI - Social Partnerships Agents
## On-Demand Campaign Onboarding Guide

**Last updated:** May 24, 2026  
**Audience:** TUM Social AI teammates running social partnership campaigns from Codex, Claude Code, Antigravity, a terminal, or Windows PowerShell.

---

## 1. What This Infrastructure Does

This repo helps the Social Partnerships team turn nonprofit source lists and incoming project requests into organized Notion records and ready-to-review outreach.

The main workflow is on demand:

1. Turns nonprofit source CSVs into scored and ranked partnership prospects.
2. Enriches top organizations with websites, contacts, roles, and email addresses.
3. Generates German outreach copy for social partnership campaigns.
4. Uploads applying nonprofits and contacts into Notion Accounts and Contacts from [Project Requirements](https://www.notion.so/311a0c6e61688042bc34ee81e5ebc631?pvs=21).
5. Processes incoming project applications through the project-applications intake flow with an AI GTM expert.
6. Sends a completion report if Gmail credentials are configured.

There is also a project-applications intake flow for organizations that already submitted a Tally form. That intake flow can stay scheduled because it is lightweight and idempotent. Campaign creation, ranking, copywriting, uploads, and maintenance should happen when a teammate deliberately asks for them.

### What Each Agent Is Built On

| Agent / flow | What it does | Criteria and logic |
|---|---|---|
| `ngo_partner_agent` | Scores and ranks nonprofit prospects from source CSVs. | Uses GPT-4o structured output with three weighted criteria: **size/impact** at 35%, **mission + AI applicability** at 35%, and **establishment/track record** at 30%. Size/impact looks at likely staff, budget, reach, and public scale signals. Mission + AI applicability asks whether the mission is socially meaningful and whether AI could realistically help. Establishment looks at age, reliability, legal/public signals, DZI/Spendenrat-style trust signals when known, and public familiarity. |
| `ngo_enrichment_agent` | Finds the best partnership contact and best available email. | Crawls the organization's homepage and likely subpages such as `impressum`, `about`, `team`, `leadership`, `contact`, `digital`, and `innovation`. It prefers senior decision-makers for small/mid-sized nonprofits, then digital/IT/innovation/partnerships leads for larger organizations. It avoids press, fundraising, volunteer, and generic office contacts unless nothing better exists. |
| `ngo_copywriter_agent` | Generates German outreach drafts for the campaign sender. | Uses the RRR structure: relevance, reward, request. The prompt enforces German language, short paragraphs, nonprofit-sensitive tone, no hype, no hallucinated facts, a low-friction 20-minute CTA, and sender-specific sign-off. Processed learnings from `data/prompts/outreach_learnings.md` are injected into future runs. |
| `notion_import_ngos` | Uploads reviewed campaign rows into Notion Accounts and contact-related fields. | Deduplicates by normalized domain and organization name. Maps CSV fields into Notion properties such as organization, website, city, work area, mission, campaign ID, lead score, estimated employees, suspect contact, email subject/body, and AI partnership angle. |
| `campaign_tracker` | Maintains the shared Notion Campaign Tracker entry for every social partnership campaign. | The NGO Notion import creates or updates one Campaign Tracker page per `Campaign ID`, relates it back to the targeted Accounts, and records trigger, target audience, targeting reasoning, outreach summary, and performance placeholders for later feedback analysis. |
| Project applications intake | Processes nonprofits that applied through the Project Requirements flow. | `requirements_analyzer.py` acts as an AI GTM + forward-deployed-engineering reviewer. It reads problem statement, current effort, usage frequency, benefits, data readiness, data language, and tech stack. It writes a concise proposed AI/ML solution, engineering blockers, and clarification questions into Project Requirements. `enrich_requirements.py` then fuzzy-matches or creates the Account and Product Owner contact, links them back to the requirement, and moves the application to review. |

### Campaign Tracker Rule

`Campaign ID` in the Accounts database is not enough. Every social partnership campaign must also create or update one entry in the shared Campaign Tracker database and relate that entry back to all targeted Accounts. The entry must capture the campaign trigger, target audience, targeting reasoning, outreach summary, and the A/B/performance fields that the feedback agent or future reporting flow will update later.

The normal NGO Notion import now syncs this automatically. If a teammate changes campaign membership manually in Notion, rerun the import as a dry run first, then apply the corrected Campaign Tracker sync or ask Codex to repair the Campaign Tracker entry so the Accounts database and Campaign Tracker stay aligned.

### Scoring Criteria Details

`ngo_partner_agent` returns `size_score`, `mission_score`, `establishment_score`, `total_score`, `reasoning`, and `ai_partnership_angle`.

| Criterion | Weight | High score means | Low score means |
|---|---:|---|---|
| Size / Impact | 35% | Major national or regional organization, strong reach, visible operations, likely budget/staff capacity, broad beneficiary base. | Very small initiative, unclear operations, little public footprint, likely volunteer-only, or cannot determine. |
| Mission + AI Applicability | 35% | Compelling social/environmental mission with plausible AI use cases such as triage, matching, reporting, accessibility, knowledge management, impact measurement, or data analysis. | Weak mission fit, unclear social value, little AI leverage, or a use case that feels forced. |
| Establishment / Track Record | 30% | Long-running, reliable organization with public trust signals, strong reputation, known legal form, DZI/Spendenrat-style signals when available. | New/unknown organization, little evidence, unclear legal status, or too little public information. |

The ranking is a shortlist tool, not a final truth. Human review should still remove organizations that are politically sensitive, unreachable, off-mission, too small, too large, or poor timing for the campaign.

---

## 2. Needed Input Before Agents Can Start

The agents do not discover a whole campaign from nothing. They need a source list first.

### Minimal CSV Requirements

For the NGO sourcing pipeline, the safest input is a CSV with one row per organization and these columns:

| Column | Required | Purpose |
|---|---:|---|
| `ngo_name` | Yes | Organization name. This is the main identity field and dedup anchor. |
| `col href` | Strongly recommended | NGO profile or website URL. The enrichment agent uses it to find contact pages and evidence. |
| `work_area` | Strongly recommended | Main mission category, used for scoring and campaign ID mission naming. |
| `sub_work_area` | Helpful | More specific mission category, improves personalization. |
| `listing_locations` | Helpful | Country, city, or region, used for relevance and filtering. |
| `account_type` | Optional | Defaults to `nonprofit`; legacy `NGO` values are mapped to `nonprofit` during Notion import. |

The current pipeline was built around NGO.base exports, so it expects NGO.base-style column names. If another source uses different headers, rename them before running the agents or ask Codex to normalize the CSV.

### Main Focus For A Good Campaign

Prioritize lists that are:

- Mission-filtered: health, environment, education, disability, migration, humanitarian aid, climate, animal welfare, human rights, civic tech, or similar.
- Region-filtered: Germany, DACH, EU, or a specific city/region that matters to the campaign.
- Operationally plausible: organizations with a real website, visible activity, and enough team capacity to respond.
- AI-relevant: organizations likely to benefit from process automation, data analysis, reporting, matching, triage, accessibility, knowledge management, or impact measurement.
- Not too huge and not too tiny: the sweet spot is often established small-to-mid-sized nonprofits that have real operations but are not unreachable global institutions.

### Limitations

- The agents are only as good as the input list. Dirty source data creates weak enrichment and generic copy.
- Contact discovery is best-effort. Some organizations hide staff pages or only publish general inboxes.
- Scoring uses model judgment and public context; it should guide review, not replace human review.
- A CSV with only names can be processed, but quality drops sharply without website, mission, and region fields.
- The tools do not send outreach automatically. They prepare records and copy for teammates to review and send.
- Always respect source website terms, privacy expectations, and sensible scraping limits.

---

## 3. Current Source Acquisition: NGO.base + Instant Data Scraper

This is the current working approach.

1. Open NGO.base and search by country, region, work area, or keyword.
2. Use filters for the campaign's main focus, for example `Germany + Environment`, `Switzerland + Health`, or `EU + Disability Support`.
3. Scroll or paginate until the visible result set matches the campaign scope.
4. Open the **Instant Data Scraper** Chrome extension.
5. Let it auto-detect the repeated organization cards/table.
6. Check the preview before export. Keep at least organization name, profile/website URL, work area, sub-work area, and location.
7. Use the extension's pagination/next-page option only when the detected rows stay consistent.
8. Export as CSV.
9. Save the file in `data/inputs/` with a clear campaign-oriented name, for example:

```bash
data/inputs/ngobase_germany_environment_2026-05.csv
```

10. Normalize headers if needed:

```text
name -> ngo_name
url/profile_url/website -> col href
category -> work_area
subcategory -> sub_work_area
location/country/city -> listing_locations
```

11. Run a dry run first:

```bash
python -m agents.run_ngo_pipeline --csv "data/inputs/ngobase_germany_environment_2026-05.csv" --dry-run
```

### NGO.base Notes

NGO.base is useful because it already exposes NGO directories by country/work area and supports search/filter workflows. Treat it as a starting list, not as verified truth. The agents still need to score, enrich, and deduplicate the resulting organizations.

### Instant Data Scraper Notes

Instant Data Scraper is useful for repeated-card/table pages. Before exporting, check:

- Row count matches what you expected from the page.
- Organization names are not mixed with navigation labels.
- URLs are real organization/profile URLs, not just icons or repeated category URLs.
- Pagination did not duplicate the first page.
- The CSV opens cleanly in Numbers, Excel, or a text editor.

If the scraper mis-detects columns, try selecting a different repeated element, reducing active filters, scraping one category at a time, or using another extension such as Data Miner/Tabon/Squirrel Snap.

---

## 4. Other Source Options To Explore

These are promising sources, but not yet fully integrated into the pipeline.

| Source | Good for | Status |
|---|---|---|
| NGO.base | Broad NGO discovery by country, region, and work area. | Tried and currently used. |
| DZI donation advice/search | German charities by work area, country, and seal status. | Worth testing for higher-trust German charity campaigns. |
| EU Transparency Register | EU-facing civil society, advocacy, policy, association, and NGO actors. | Worth testing for policy/European campaigns; may include many non-NGO interest representatives. |
| Council of Europe INGO database | INGOs with participatory status, searchable by competence area and country. | Worth testing for established European/international civil society. |
| GlobalGiving Atlas | Large global nonprofit database. | Worth exploring if the team needs global coverage or API-style enrichment. |
| National charity registers | Country-specific nonprofit verification and discovery. | Left to explore per country. |
| Network member pages | Members of umbrella organizations, alliances, coalitions, and sector networks. | High-quality but source-specific scraping work remains. |
| Grant/funding winner pages | Organizations that recently received funding or awards. | Promising trigger-event source; needs a repeatable collection workflow. |
| Event speaker/participant pages | NGOs active in conferences, hackathons, climate/health/social impact events. | Promising trigger-event source; needs manual validation. |
| News and press releases | NGOs reacting to crises, new programs, or policy windows. | Promising but noisy; best for targeted campaign ideas. |
| LinkedIn Boolean search | People/org discovery by mission, role, and geography. | Useful for contact discovery, less reliable as the primary organization list. |

### What Has Been Tried Already

- NGO.base category/region scraping with Instant Data Scraper.
- Pipeline processing of NGO.base-style CSVs with `ngo_name`, `col href`, `work_area`, `sub_work_area`, and `listing_locations`.
- Scoring, enrichment, copywriting, and Notion upload from the generated CSVs.

### What Is Left To Explore

- A reliable CSV normalization step for non-NGO.base sources.
- A small benchmark comparing NGO.base, DZI, EU Transparency Register, Council of Europe INGO database, and GlobalGiving Atlas for reply quality.
- A trigger-event sourcing workflow based on grant winners, conference participants, recent reports, or crisis/program launches.
- A source-quality score so campaigns can prioritize clean, fresh, reachable organizations.
- Country-specific source playbooks for Germany, Austria, Switzerland, and broader EU campaigns.

---

## 5. What Teammates Need

You do **not** need coding knowledge for normal operation. You need:

- One vibe-coding or agentic environment: **Codex**, **Claude Code**, **Antigravity**, or a normal terminal.
- A local clone of this repository.
- Python 3.9+.
- Access to the shared TUM Social AI Notion workspace.
- A Notion integration token with access to the Accounts database and Contacts database.
- An OpenAI API key or an environment/license that lets you use the required OpenAI calls.
- Optional: a Gmail app password if you want the pipeline to send completion reports from your laptop.

### API Credit Basics

- Running existing agents is usually cheaper. Credits are mainly used when the code calls OpenAI for scoring, enrichment reasoning, and outreach copy.
- Building, changing, or debugging agents costs more because the coding environment itself uses model calls while editing and testing.
- Notion writes and local CSV work are not the expensive part. The costly moments are AI calls, especially broad campaign scoring and full copywriting batches.

---

## 6. Setup From GitHub

### macOS / Linux

```bash
git clone https://github.com/nicopaul19/tum-social-partnerships.git
cd tum-social-partnerships
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.template .env
```

### Windows PowerShell

```powershell
git clone https://github.com/nicopaul19/tum-social-partnerships.git
cd tum-social-partnerships
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.template .env
```

### Required `.env` Values

```bash
OPENAI_API_KEY=
NOTION_TOKEN=
NOTION_DB_ACCOUNTS_ID=
NOTION_DB_CONTACTS_ID=
NOTION_DB_REQUIREMENTS_ID=
```

Optional Gmail report delivery:

```bash
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

Use a Gmail app password, not your normal Gmail password. Google requires 2-step verification before app passwords are available.

### Important Notion Access Note

The Notion Contacts database must be shared with the active Notion integration. The current API access can return `404` for the Contacts DB even when the database ID is correct. If that happens, open the Contacts DB in Notion, share it with the integration connected to `NOTION_TOKEN`, and retry.

### Verify Setup

```bash
python -c "from utils.config import validate_config; ok, missing = validate_config(); print('OK' if ok else 'Missing: ' + ', '.join(missing))"
python -m agents.run_ngo_pipeline --help
```

Open the local docs:

- Markdown: [ONBOARDING.md](ONBOARDING.md)
- HTML: [ONBOARDING.html](ONBOARDING.html)

---

## 7. Environment Variables

| Variable | Required | Used for |
|---|---:|---|
| `OPENAI_API_KEY` | Yes | Scoring, enrichment reasoning, and outreach copy generation. |
| `NOTION_TOKEN` | Yes | Writing Accounts, Contacts, and project application data to Notion. |
| `NOTION_DB_ACCOUNTS_ID` | Yes | Social partner organization records. |
| `NOTION_DB_CONTACTS_ID` | Yes | Contact records linked to Accounts. Must be shared with the integration. |
| `NOTION_DB_CAMPAIGNS_ID` | Yes | Shared Campaign Tracker database. Required so campaign creation is not limited to Account `Campaign ID` tags. |
| `NOTION_DB_REQUIREMENTS_ID` | For project applications | Incoming project/application records from the intake workflow. |
| `GMAIL_ADDRESS` | Optional | Sender address for completion reports. |
| `GMAIL_APP_PASSWORD` | Optional | Gmail app password for sending completion reports. |

The config loader reads a shared workspace `.env` first, then this repo's `.env` with override priority.

---

## 8. Command-First Campaign Lifecycle

The social partnerships campaign flow uses the module commands below.

| Step | When | Command |
|---|---|---|
| Full campaign run | You have a clean nonprofit CSV and want the end-to-end flow | `python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv"` |
| Full dry run | You want to test the flow without Notion writes or email reports | `python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv" --dry-run` |
| Score/rank only | You want a shortlist before enrichment | `python -m agents.ngo_partner_agent --csv "data/inputs/my_ngos.csv" --min-score 6 --top 25` |
| Enrich contacts | You already have `data/ngo_partnerships/ngo_ranked.csv` | `python -m agents.ngo_enrichment_agent` |
| Generate outreach | You already have `data/ngo_partnerships/ngo_enriched.csv` | `python -m agents.ngo_copywriter_agent --campaign-id "NGOs_DDMMYYYY_Topic"` |
| Preview outreach | You want to inspect copy before saving | `python -m agents.ngo_copywriter_agent --dry-run --limit 5` |
| Upload to Notion | You already have an outreach CSV | `python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv"` |
| Preview Notion upload | You want to check creates/updates without writing | `python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv" --dry-run --limit 5` |
| Project applications intake | You want to process new Tally applications manually | `cd project-applications && python requirements_analyzer.py && python enrich_requirements.py` |

The full pipeline creates a campaign ID like `NGOs_DDMMYYYY_Mission`, assigns owners across the ranked list, writes CSV outputs in `data/ngo_partnerships/`, uploads to Notion unless `--dry-run` is set, creates or updates the Campaign Tracker entry with Account relations, and sends the completion email only when Gmail credentials exist.

---

## 9. Campaign Lifecycle

1. **Prepare input.** Export a CSV from ngobase or another approved source. Keep columns such as `ngo_name`, `col href`, `work_area`, `sub_work_area`, `listing_locations`, and `account_type`.
2. **Run a dry run first.** Use `--dry-run` on the full pipeline when the campaign is new or the CSV source changed.
3. **Review the ranked CSV.** Check `data/ngo_partnerships/ngo_ranked.csv` for relevance and score quality.
4. **Generate and inspect outreach.** Use the copywriter dry run or review `data/ngo_partnerships/ngo_outreach.csv`.
5. **Upload intentionally.** Run the full pipeline without `--dry-run`, or run `notion_import_ngos` on an already-reviewed outreach CSV.
6. **Send and maintain.** Teammates send outreach from their own accounts, record outcomes in Notion, and add copywriter examples to the improvement log.

---

## 10. Operating Schedule

No one needs to maintain a fixed weekly report rhythm. Use recurring calendar blocks as reminders, then run campaign actions only when useful.

| Cadence | Calendar block | Action |
|---|---|---|
| Continuous | Partner signal capture | Save strong nonprofit prospects, referrals, and source CSVs. |
| On campaign start | 60 min shortlist and enrichment block | Clean the CSV, run a dry run, review ranked prospects, and decide whether to continue. |
| Before outreach | 30 min copy review block | Review generated email copy and update weak examples in the improvement log. |
| During active campaign | 20 min follow-up block | Check replies, update Notion statuses, and decide next actions. |
| After enough examples | 30 min learning block | Move processed examples into `data/prompts/outreach_learnings.md`. |

Recommended calendar CTA: create one recurring **Social Partnerships Campaign Maintenance** block every 1-2 weeks, plus one campaign-specific block whenever a new source list is ready.

---

## 11. Automation Policy

Campaign jobs should be manual/on demand. The only scheduled jobs that should stay active are lightweight intake jobs that process new applications or saved inputs.

| Job | Policy |
|---|---|
| `com.tumsocialai.project-applications` | Keep if this laptop owns project application intake. |
| `com.tumsocialai.requirements-enrichment` | Keep if this laptop owns application enrichment. |
| Ranking, enrichment, copywriter, upload, cleanup, feedback, and LinkedIn campaign jobs | Run manually when a teammate asks. |

On macOS, inspect active jobs with:

```bash
launchctl print gui/$(id -u) | grep tumsocialai
```

If an old campaign job appears active, unload it intentionally after confirming ownership:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tumsocialai.sales-ranking.plist
```

Do not delete plist files unless the team explicitly decides they are no longer needed.

---

## 12. Copywriter Improvement Log

Use the lightweight Notion toggle style inside the Partnerships Agents page:

- `not yet processed`
- `processed`

Each item should capture:

- Bad/generated snippet.
- Improved version.
- Reason for improvement.
- Campaign/contact context.
- Processed status.

When a learning is processed, distill the general rule into:

```bash
data/prompts/outreach_learnings.md
```

Keep the file as reusable guidance, not a graveyard of full emails. A good processed learning sounds like: "When the AI angle is speculative, use 'konnte zum Beispiel spannend sein' and ask for the organization's real bottlenecks, instead of stating the use case as fact."

---

## 13. Troubleshooting

### Notion Contacts DB returns 404

The DB ID may be correct but hidden from the integration.

Fix:

1. Open the Contacts database in Notion.
2. Click **Share**.
3. Invite the active Notion integration used by `NOTION_TOKEN`.
4. Re-run a dry run upload.

### `OPENAI_API_KEY not set`

Check that `.env` exists in this repo or in the shared workspace root:

```bash
python -c "from utils.config import OPENAI_API_KEY; print(bool(OPENAI_API_KEY))"
```

### Virtual environment is not active

macOS / Linux:

```bash
source venv/bin/activate
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

### Gmail report fails

The pipeline can still complete without email reports. If reports should send, confirm `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`, and make sure the password is a Gmail app password.

### Google Drive path problems on macOS

If the repo lives in Google Drive and launchd jobs cannot read files, grant Full Disk Access to the shell used by the wrapper, usually Terminal, zsh, bash, or the automation runner.

---

## 14. Commands Reference

```bash
# Full flow
python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv"
python -m agents.run_ngo_pipeline --csv "data/inputs/my_ngos.csv" --dry-run

# Score and rank
python -m agents.ngo_partner_agent --csv "data/inputs/my_ngos.csv"
python -m agents.ngo_partner_agent --csv "data/inputs/my_ngos.csv" --min-score 6 --top 25
python -m agents.ngo_partner_agent --csv "data/inputs/my_ngos.csv" --append

# Enrich
python -m agents.ngo_enrichment_agent
python -m agents.ngo_enrichment_agent --input "data/ngo_partnerships/ngo_ranked.csv" --limit 10

# Copywrite
python -m agents.ngo_copywriter_agent
python -m agents.ngo_copywriter_agent --dry-run --limit 5
python -m agents.ngo_copywriter_agent --campaign-id "NGOs_DDMMYYYY_Topic"

# Upload
python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv"
python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv" --dry-run --limit 5

# Project applications
cd project-applications
python requirements_analyzer.py
python enrich_requirements.py
python enrich_requirements.py --dry-run
```

---

## 15. Test Plan

Use this checklist after changing the infrastructure:

- Run static/syntax checks for changed Python files: `python -m compileall agents utils project-applications`.
- Dry-run available runner commands:
  - `python -m agents.run_ngo_pipeline --csv "data/ngo_partnerships/NGOs_Health_Environment_Animals_DE_12032026.csv" --dry-run`
  - `python -m agents.notion_import_ngos --csv "data/ngo_partnerships/ngo_outreach.csv" --dry-run --limit 1`
  - `python -m agents.ngo_copywriter_agent --dry-run --limit 1`
- If a rank-only dry-run is added later, run it before broad scoring. Today `ngo_partner_agent` does not expose `--dry-run`.
- This repo does not currently expose `agent.py rank/upload/copywrite/linkedin` commands; use the module commands above.
- Verify disabled launchd campaign jobs no longer appear as active scheduled jobs.
- Verify kept intake jobs still have valid wrappers and logs.
- Verify Notion page/database creation by reading the resulting page blocks after creation.
- Open `ONBOARDING.html` locally and confirm the layout is readable and does not contain outdated fixed campaign framing.
