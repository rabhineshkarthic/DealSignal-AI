"""DealSignal AI branding and the dynamic transaction title. No network, no Gemini."""

import ast
import re
from pathlib import Path

import pytest

import llm_client
import ui_components as uc
import ui_helpers as ui
from config import load_settings
from models import load_deal
from research import registry
from research.market_data import StooqMarketPriceProvider
from research_fixtures import FakeMarketFetch, sec_provider

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = load_deal(ROOT / "data" / "sample_deal.json")
PRESENTATION = ["app.py", "ui_helpers.py", "ui_components.py", "ui_forms.py", "research_ui.py"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network or Gemini access attempted")

    monkeypatch.setattr(llm_client.GeminiClient, "__init__", blocked)
    monkeypatch.setattr("urllib.request.urlopen", blocked)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(ui, "load_settings", lambda: load_settings(env_file=None))


def app_test():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)


def titles(at):
    """Transaction titles currently rendered in the deal header."""
    return [re.sub(r'<span class="arrow">→</span>', " → ",
                   re.search(r'class="deal-title">(.*?)</div>', m.value).group(1))
            for m in at.markdown if 'class="deal-title"' in m.value]


def visible(markup):
    """Rendered text: tags removed, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def all_markdown(at):
    return " ".join(m.value for m in [*at.markdown, *at.sidebar.markdown])


# --- Static branding ------------------------------------------------------------------------

def test_browser_title_is_static():
    tree = ast.parse((ROOT / "app.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "set_page_config"]
    (call,) = calls
    title = next(k.value for k in call.keywords if k.arg == "page_title")
    assert isinstance(title, ast.Constant) and title.value == "DealSignal AI — M&A Decision Intelligence"


def test_sidebar_and_main_branding():
    at = app_test().run()
    assert not at.exception
    sidebar = visible(" ".join(m.value for m in at.sidebar.markdown))
    assert "DealSignal AI M&amp;A Decision Intelligence Platform" in sidebar
    assert 'class="sb-brand"' in at.sidebar.markdown[0].value  # lockup is the first element
    raw_main = " ".join(m.value for m in at.markdown)
    main = visible(raw_main)
    assert "DealSignal AI M&amp;A Decision Intelligence Platform" in main
    assert 'class="brand"' in raw_main and 'class="brand-name"' in raw_main  # hero header
    for part in ("Sourced research", "Deterministic finance", "AI deal-team analysis"):
        assert f"<span>{part}</span>" in raw_main
    assert "Offline demo" in main  # operating mode badge kept


def test_architecture_page_title():
    at = app_test().run()
    assert "### DealSignal AI Architecture" in [m.value for m in at.markdown]


# --- Dynamic transaction title (D4 / D5) --------------------------------------------------------

def test_title_follows_the_active_deal_and_old_names_disappear():
    at = app_test().run()
    assert titles(at) == ["Northstar Software → Atlas Analytics"]
    at.sidebar.radio(key="deal_source").set_value(ui.CUSTOM_DEAL).run()
    at.text_input(key="form_acquirer.name").set_value("Orion Systems")
    at.text_input(key="form_target.name").set_value("Vega Data")
    at.button(key="FormSubmitter:custom_deal_form-Create Deal").click().run()
    assert not at.exception
    assert titles(at) == ["Orion Systems → Vega Data"]
    rendered = all_markdown(at)
    assert "Northstar" not in rendered and "Atlas" not in rendered
    at.sidebar.radio(key="deal_source").set_value(ui.SAMPLE_DEAL).run()
    assert titles(at) == ["Northstar Software → Atlas Analytics"]
    assert "Orion" not in all_markdown(at)


def test_research_title_uses_resolved_company_names(monkeypatch):
    monkeypatch.setitem(registry.PROVIDER_FACTORIES, "sec", lambda: sec_provider())
    monkeypatch.setitem(registry.MARKET_PRICE_FACTORIES, "US",
                        lambda: StooqMarketPriceProvider(fetch=FakeMarketFetch()))
    at = app_test().run()
    at.sidebar.radio(key="deal_source").set_value(ui.RESEARCH_DEAL).run()
    assert titles(at) == []  # no fake title before a deal exists
    assert uc.NO_DEAL_MESSAGE in all_markdown(at)
    at.text_input(key="research_q_acquirer").set_value("ALPH")
    at.text_input(key="research_q_target").set_value("BETA")
    next(b for b in at.button if b.label == "Research Companies").click().run()
    at.number_input(key="rv_target.ebitda").set_value(8900.0)
    at.number_input(key="rv_target.free_cash_flow").set_value(7000.0)
    at.number_input(key="ra_transaction_assumptions.offer_enterprise_value").set_value(60000.0)
    at.button(key="FormSubmitter:research_review_form-Build Deal").click().run()
    assert not at.exception
    assert titles(at) == ["ALPHA SOFTWARE CORP → BETA ANALYTICS INC"]
    assert "Northstar" not in all_markdown(at)


def test_no_deal_state_shows_neutral_message_not_a_title():
    at = app_test().run()
    at.sidebar.radio(key="deal_source").set_value(ui.CUSTOM_DEAL).run()
    assert titles(at) == []
    assert uc.NO_DEAL_MESSAGE in all_markdown(at)


def test_transaction_title_helper_uses_deal_fields():
    assert uc.transaction_title(SAMPLE) == "Northstar Software → Atlas Analytics"


@pytest.mark.parametrize("rel", PRESENTATION)
def test_no_hardcoded_public_company_names_or_tickers(rel):
    source = (ROOT / rel).read_text()
    for term in ["ORCL", "SNOW", "Oracle", "ORACLE", "Snowflake", "MSFT", "Microsoft",
                 "MICROSOFT", "CRM", "Salesforce"]:
        assert not re.search(rf"\b{term}\b", source), (rel, term)


# --- Opt-in hypothetical-transaction disclaimer -------------------------------------------------

def test_hypothetical_notice_is_opt_in_and_generic():
    at = app_test().run()
    assert not any(c.label == "Label as hypothetical transaction" for c in at.sidebar.checkbox)
    at.sidebar.radio(key="deal_source").set_value(ui.CUSTOM_DEAL).run()
    assert uc.HYPOTHETICAL_NOTICE not in all_markdown(at)  # off by default
    at.sidebar.checkbox(key="hypothetical_label").check().run()
    assert not at.exception
    assert uc.HYPOTHETICAL_NOTICE in all_markdown(at)
    assert uc.HYPOTHETICAL_NOTICE == ("Hypothetical transaction for demonstration purposes. "
                                      "Not an announced transaction and not investment advice.")


def test_hypothetical_notice_stays_visible_in_sidebar_and_header():
    at = app_test().run()
    at.sidebar.radio(key="deal_source").set_value(ui.CUSTOM_DEAL).run()
    at.text_input(key="form_acquirer.name").set_value("Orion Systems")
    at.text_input(key="form_target.name").set_value("Vega Data")
    at.button(key="FormSubmitter:custom_deal_form-Create Deal").click().run()
    sidebar = " ".join(m.value for m in at.sidebar.markdown)
    assert uc.HYPOTHETICAL_NOTICE not in sidebar
    at.sidebar.checkbox(key="hypothetical_label").check().run()
    assert not at.exception
    sidebar = " ".join(m.value for m in at.sidebar.markdown)
    assert uc.HYPOTHETICAL_NOTICE in sidebar
    header = next(m.value for m in at.markdown if 'class="deal-title"' in m.value)
    assert ">Hypothetical<" in header
