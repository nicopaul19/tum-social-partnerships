"""
Campaign Tracker sync for Social Partnerships campaigns.

Every campaign that writes `Campaign ID` to Notion Accounts should also have
one Campaign Tracker entry related back to those Accounts. This keeps social
and strategic partnership campaigns comparable inside the shared CRM.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from utils import resilient_http as http_requests
from rich.console import Console

from utils.config import NOTION_DB_ACCOUNTS_ID, NOTION_DB_CAMPAIGNS_ID, NOTION_TOKEN


console = Console()
CAMPAIGN_PAGE_ICON = "⛺"

TRACKER_PROPS = {
    "campaign_id": "🏷️ Campaign ID",
    "campaign_type": "🤝 Campaign Team",
    "campaign_trigger": "⚡ Campaign Trigger",
    "target_audience": "🎯 Target Audience",
    "targeting_reasoning": "🧠 Targeting Reasoning",
    "outreach_summary": "📧 Outreach Summary",
    "accounts": "🏢 Accounts",
    "accounts_count": "🏢 Accounts Count",
    "contacts_count": "👥 Contacts Count",
    "engaged_contacts": "✅ Engaged Contacts",
    "not_engaged_contacts": "🚫 Not Engaged Contacts",
    "pending_contacts": "⏳ Pending Contacts",
    "ab_winner": "🏆 A/B Winner",
}

NOTION_API_VERSION = "2022-06-28"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_API_VERSION,
    "Content-Type": "application/json",
}


def _normalize_notion_id(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if "/" in value:
        value = value.rstrip("/").split("/")[-1].split("?")[0]
    return value.replace("-", "")


def _notion_request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    url = f"https://api.notion.com{endpoint}"
    response = http_requests.request(
        method,
        url,
        headers=NOTION_HEADERS,
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            message = response.json().get("message", response.text[:300])
        except Exception:
            message = response.text[:300]
        raise RuntimeError(f"Notion {method} {endpoint} failed: {response.status_code} - {message}")
    return response.json()


def _title_property(database: dict) -> str:
    for name, prop in database.get("properties", {}).items():
        if prop.get("type") == "title":
            return name
    return "Name"


def _existing_campaign_page(database_id: str, title_prop: str, campaign_id: str) -> dict | None:
    payload = {
        "filter": {
            "or": [
                {"property": title_prop, "title": {"equals": campaign_id}},
                {"property": TRACKER_PROPS["campaign_id"], "rich_text": {"equals": campaign_id}},
            ]
        },
        "page_size": 1,
    }
    result = _notion_request("POST", f"/v1/databases/{database_id}/query", payload)
    pages = result.get("results", [])
    return pages[0] if pages else None


def _rich_text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value[:2000]}}]} if value else {"rich_text": []}


def _top_value(rows: list[dict], key: str) -> str:
    values = [str(row.get(key, "")).strip() for row in rows if str(row.get(key, "")).strip()]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]


def ensure_campaign_tracker_schema(dry_run: bool = False) -> tuple[str, str] | None:
    """Ensure the shared Campaign Tracker has the fields this repo writes."""
    campaigns_db = _normalize_notion_id(NOTION_DB_CAMPAIGNS_ID)
    accounts_db = _normalize_notion_id(NOTION_DB_ACCOUNTS_ID)

    if not NOTION_TOKEN or not campaigns_db or not accounts_db:
        console.print(
            "[yellow]Campaign Tracker sync skipped: NOTION_TOKEN, "
            "NOTION_DB_CAMPAIGNS_ID, or NOTION_DB_ACCOUNTS_ID is missing.[/yellow]"
        )
        return None

    database = _notion_request("GET", f"/v1/databases/{campaigns_db}")
    title_prop = _title_property(database)
    existing = database.get("properties", {})

    p = TRACKER_PROPS
    missing: dict[str, Any] = {}
    desired = {
        p["campaign_id"]: {"rich_text": {}},
        p["campaign_type"]: {
            "select": {
                "options": [
                    {"name": "Social Partnerships", "color": "green"},
                    {"name": "Strategic Partnerships", "color": "blue"},
                    {"name": "Mixed / Unknown", "color": "gray"},
                ]
            }
        },
        p["campaign_trigger"]: {"rich_text": {}},
        p["target_audience"]: {"rich_text": {}},
        p["targeting_reasoning"]: {"rich_text": {}},
        p["outreach_summary"]: {"rich_text": {}},
        p["accounts_count"]: {"number": {"format": "number"}},
        p["contacts_count"]: {"number": {"format": "number"}},
        p["engaged_contacts"]: {"number": {"format": "number"}},
        p["not_engaged_contacts"]: {"number": {"format": "number"}},
        p["pending_contacts"]: {"number": {"format": "number"}},
        p["ab_winner"]: {"select": {"options": [{"name": "No Data", "color": "gray"}]}},
    }
    for name, spec in desired.items():
        if name not in existing:
            missing[name] = spec
    if p["accounts"] not in existing:
        missing[p["accounts"]] = {"relation": {"database_id": accounts_db, "single_property": {}}}

    if missing:
        if dry_run:
            console.print(f"[yellow]Dry run - would add {len(missing)} Campaign Tracker properties[/yellow]")
        else:
            _notion_request("PATCH", f"/v1/databases/{campaigns_db}", {"properties": missing})
            console.print(f"[green]Campaign Tracker schema ensured ({len(missing)} property/properties added)[/green]")

    return campaigns_db, title_prop


def sync_campaign_tracker_entry(
    campaign_id: str,
    account_page_ids: list[str],
    rows: list[dict] | None = None,
    dry_run: bool = False,
) -> str | None:
    """Create/update one Campaign Tracker entry and relate it to campaign Accounts."""
    campaign_id = (campaign_id or "").strip()
    unique_account_ids = list(dict.fromkeys([page_id for page_id in account_page_ids if page_id]))
    rows = rows or []

    if not campaign_id:
        console.print("[yellow]Campaign Tracker sync skipped: no campaign ID.[/yellow]")
        return None

    schema = ensure_campaign_tracker_schema(dry_run=dry_run)
    if not schema:
        return None
    campaigns_db, title_prop = schema

    work_area = _top_value(rows, "work_area") or "mission-fit nonprofit"
    location = _top_value(rows, "listing_locations")
    trigger = _top_value(rows, "ai_partnership_angle") or (
        f"Social partnership outreach to nonprofits around {work_area}; relevance is based on mission fit "
        "and plausible AI-for-good collaboration potential."
    )
    audience = f"{len(unique_account_ids) or len(rows)} nonprofit/NGO accounts"
    if work_area:
        audience += f" focused on {work_area}"
    if location:
        audience += f" in/around {location}"

    reasoning = (
        "Accounts were selected through the Social Partnerships NGO scoring workflow: size/impact, "
        "mission plus AI applicability, and establishment/track record. The Campaign Tracker entry "
        "is mandatory so future social and strategic copywriters can see what was targeted and why."
    )
    summary = (
        "German RRR outreach generated for social partnerships; teammates review and send manually. "
        "Campaign performance should be updated from Account/Lead statuses and later feedback runs."
    )

    page = _existing_campaign_page(campaigns_db, title_prop, campaign_id)
    existing_account_ids: list[str] = []
    if page:
        existing_account_ids = [
            rel.get("id")
            for rel in page.get("properties", {}).get(TRACKER_PROPS["accounts"], {}).get("relation", [])
            if rel.get("id")
        ]
    relation_ids = list(dict.fromkeys(existing_account_ids + unique_account_ids))

    p = TRACKER_PROPS
    properties: dict[str, Any] = {
        title_prop: {"title": [{"text": {"content": campaign_id}}]},
        p["campaign_id"]: _rich_text(campaign_id),
        p["campaign_type"]: {"select": {"name": "Social Partnerships"}},
        p["campaign_trigger"]: _rich_text(trigger),
        p["target_audience"]: _rich_text(audience),
        p["targeting_reasoning"]: _rich_text(reasoning),
        p["outreach_summary"]: _rich_text(summary),
        p["accounts_count"]: {"number": len(relation_ids) or len(rows)},
        p["contacts_count"]: {"number": len(rows)},
        p["pending_contacts"]: {"number": len(rows)},
        p["ab_winner"]: {"select": {"name": "No Data"}},
    }
    if relation_ids:
        properties[p["accounts"]] = {"relation": [{"id": page_id} for page_id in relation_ids]}

    if dry_run:
        console.print(
            f"[yellow]Dry run - would sync Campaign Tracker entry {campaign_id} "
            f"with {len(relation_ids)} account relation(s)[/yellow]"
        )
        return page.get("id") if page else None

    if page:
        _notion_request(
            "PATCH",
            f"/v1/pages/{page['id']}",
            {"icon": {"type": "emoji", "emoji": CAMPAIGN_PAGE_ICON}, "properties": properties},
        )
        console.print(f"[green]Campaign Tracker updated: {campaign_id} ({len(relation_ids)} accounts)[/green]")
        return page["id"]

    created = _notion_request(
        "POST",
        "/v1/pages",
        {
            "parent": {"database_id": campaigns_db},
            "icon": {"type": "emoji", "emoji": CAMPAIGN_PAGE_ICON},
            "properties": properties,
        },
    )
    console.print(f"[green]Campaign Tracker created: {campaign_id} ({len(relation_ids)} accounts)[/green]")
    return created.get("id")
