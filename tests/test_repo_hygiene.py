"""Secret and repository hygiene. Never prints a key: failures name files only."""

import re
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
GOOGLE_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
PLACEHOLDER = "your_api_key_here"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def committable_files():
    """Tracked plus untracked-but-not-ignored files: everything a commit could include."""
    out = git("ls-files", "--cached", "--others", "--exclude-standard")
    if out.returncode != 0:
        pytest.skip("not a git repository")
    return [ROOT / line for line in out.stdout.splitlines() if (ROOT / line).is_file()]


@pytest.mark.parametrize("path", [".env", ".venv/x", "reports/Northstar_Atlas_MA_Model.xlsx",
                                  ".streamlit/secrets.toml", "__pycache__/x.pyc",
                                  ".research_cache/sec/abc.json"])
def test_sensitive_and_generated_paths_are_ignored(path):
    assert git("check-ignore", "-q", path).returncode == 0, path


def test_env_example_is_committable_and_has_only_a_placeholder():
    files = {p.relative_to(ROOT).as_posix() for p in committable_files()}
    assert ".env.example" in files and ".env" not in files
    assert dotenv_values(ROOT / ".env.example")["GEMINI_API_KEY"] == PLACEHOLDER


def test_no_google_api_key_in_committable_files():
    offenders = [p.relative_to(ROOT).as_posix() for p in committable_files()
                 if p.suffix not in {".xlsx", ".png", ".jpg"}
                 and GOOGLE_KEY_PATTERN.search(p.read_text(errors="ignore"))]
    assert offenders == [], f"possible API key in: {offenders}"


@pytest.mark.parametrize("name", ["GEMINI_API_KEY", "ALPHA_VANTAGE_API_KEY"])
def test_real_local_key_not_copied_into_committable_files(name):
    env = ROOT / ".env"
    key = (dotenv_values(env).get(name) or "") if env.exists() else ""
    if not key or key == PLACEHOLDER:
        pytest.skip(f"no local {name} configured")
    offenders = [p.relative_to(ROOT).as_posix() for p in committable_files()
                 if key in p.read_text(errors="ignore")]
    assert offenders == [], f"local {name} found in: {offenders}"


# Curated screenshots of real app runs, kept deliberately for the project write-up.
SCREENSHOT_DIRS = ("docs/linkedin/", "ss/")


def test_no_images_or_logs_committed():
    suspicious = [p.relative_to(ROOT).as_posix() for p in committable_files()
                  if p.suffix in {".png", ".jpg", ".jpeg", ".log"}]
    assert [p for p in suspicious
            if not (p.startswith(SCREENSHOT_DIRS) and not p.endswith(".log"))] == []


def test_env_example_sec_user_agent_is_a_placeholder():
    value = dotenv_values(ROOT / ".env.example")["SEC_USER_AGENT"]
    assert value == "AI-MA-Deal-Team your-email@example.com"


def test_env_example_alpha_vantage_key_is_empty():
    assert dotenv_values(ROOT / ".env.example")["ALPHA_VANTAGE_API_KEY"] in ("", None)
