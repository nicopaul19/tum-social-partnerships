"""
Configuration management for TUM Social AI — NGO Partnerships Agent.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Project Paths
PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent  # AI Projects & Agents/

# Load environment variables (cascade: root → project)
load_dotenv(WORKSPACE_ROOT / ".env")  # Shared keys (fallback)
load_dotenv(PROJECT_ROOT / ".env", override=True)  # Project-specific (overrides)

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ACCOUNTS_ID = os.getenv("NOTION_DB_ACCOUNTS_ID")
NOTION_DB_CONTACTS_ID = os.getenv("NOTION_DB_CONTACTS_ID")

# Email Delivery
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# NGO Pipeline report recipients (hardcoded in run_ngo_pipeline.py — kept here for reference)
NGO_REPORT_RECIPIENTS = [
    "nicopaul19@gmail.com",
    "leon1.koerbs@gmail.com",
    "jschurer27@gmail.com",
    "carlo.rn02@gmail.com",
    "lisa.gavrilova@tum.de",
]

# Data Paths
DATA_DIR = PROJECT_ROOT / "data"
TABLES_DIR = DATA_DIR / "ngo_partnerships"   # canonical NGO output folder
LOGS_DIR = DATA_DIR / "logs"
REPORTS_DIR = DATA_DIR / "reports"
API_USAGE_LOG = LOGS_DIR / "api_usage.jsonl"


def validate_config():
    """Check that required API keys are set."""
    missing = []
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not NOTION_TOKEN:
        missing.append("NOTION_TOKEN")
    if not NOTION_DB_ACCOUNTS_ID:
        missing.append("NOTION_DB_ACCOUNTS_ID")
    if missing:
        return False, missing
    return True, []
