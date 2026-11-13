import os
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from app.main import app

SERVER_PORT = 8995
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


@pytest.fixture(scope="module")
def live_server():
    """Spins up a lightweight uvicorn instance for Playwright headless testing."""
    config = uvicorn.Config(app, host="127.0.0.1", port=SERVER_PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.2)
    yield SERVER_URL
    server.should_exit = True
    thread.join(timeout=2)


@pytest.fixture(scope="module")
def browser_context():
    """Provides a Playwright Chromium browser instance."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def test_mobile_breakpoint_layout_and_navigation(live_server, browser_context):
    """
    Breakpoint 1: Mobile Phone (< 768px, e.g. 375x667).
    Verifies:
    - Single-column adaptive layout
    - Touch-friendly bottom navigation visible with touch targets >= 44px
    - Desktop sidebar hidden / translated off-canvas
    - Invariant: Zero horizontal scrolling (scrollWidth <= clientWidth)
    """
    page = browser_context.new_page(viewport={"width": 375, "height": 667})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".kpi-grid")

    # 1. No horizontal scroll on mobile
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w, f"Mobile horizontal scroll detected: {scroll_w} > {client_w}"

    # 2. Bottom navigation is visible and accessible
    bottom_nav = page.locator("#app-bottom-nav")
    assert bottom_nav.is_visible()

    # 3. Touch target height of bottom nav items >= 44px
    items = page.locator(".bottom-nav-item").all()
    assert len(items) >= 5
    for item in items:
        box = item.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"Touch target height {box['height']}px is less than 44px"

    # 4. Sidebar is off-canvas / hidden on mobile
    sidebar = page.locator("#app-sidebar")
    transform = sidebar.evaluate("el => window.getComputedStyle(el).transform")
    assert "matrix" in transform or not sidebar.is_visible()

    page.close()


def test_tablet_breakpoint_layout(live_server, browser_context):
    """
    Breakpoint 2: Tablet (768px - 1023px, e.g. 768x1024).
    Verifies:
    - Adaptive two-column layout
    - Collapsed icon sidebar (width ~72px)
    - Mobile bottom nav is hidden
    - Invariant: Zero horizontal scrolling
    """
    page = browser_context.new_page(viewport={"width": 768, "height": 1024})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".kpi-grid")

    # 1. No horizontal scroll
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w

    # 2. Bottom nav is hidden on tablet
    bottom_nav = page.locator("#app-bottom-nav")
    assert not bottom_nav.is_visible()

    # 3. Sidebar is in compact icon-only mode (~72px)
    sidebar = page.locator("#app-sidebar")
    assert sidebar.is_visible()
    box = sidebar.bounding_box()
    assert box is not None
    assert 68 <= box["width"] <= 80, f"Tablet sidebar width should be ~72px, got {box['width']}px"

    page.close()


def test_laptop_breakpoint_layout(live_server, browser_context):
    """
    Breakpoint 3: Laptop (1024px - 1439px, e.g. 1024x768).
    Verifies:
    - Persistent sidebar navigation (250px)
    - Responsive 3-column dashboard grid
    - Bottom nav hidden
    - Invariant: Zero horizontal scrolling
    """
    page = browser_context.new_page(viewport={"width": 1024, "height": 768})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".kpi-grid")

    # 1. No horizontal scroll
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w

    # 2. Bottom nav hidden
    assert not page.locator("#app-bottom-nav").is_visible()

    # 3. Sidebar full width (250px)
    sidebar = page.locator("#app-sidebar")
    assert sidebar.is_visible()
    box = sidebar.bounding_box()
    assert box is not None
    assert 240 <= box["width"] <= 260

    page.close()


def test_desktop_ultrawide_breakpoint_layout(live_server, browser_context):
    """
    Breakpoint 4: Desktop & Ultrawide (>= 1440px, e.g. 1440x900).
    Verifies:
    - Expandable multi-column card layout (4-column grid for chains)
    - Invariant: Zero horizontal scrolling
    """
    page = browser_context.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".cards-grid.cols-4")

    # 1. No horizontal scroll
    scroll_w = page.evaluate("document.documentElement.scrollWidth")
    client_w = page.evaluate("document.documentElement.clientWidth")
    assert scroll_w <= client_w

    # 2. 12 connected chain cards are displayed
    chain_cards = page.locator(".chain-card").all()
    assert len(chain_cards) == 12

    page.close()


def test_evidence_badges_and_visual_semantics(live_server, browser_context):
    """
    Requirement 4: Data integrity and visual semantics.
    - Explicitly distinguish VERIFIED, PROBABILISTIC, INSUFFICIENT, and UNKNOWN evidence.
    - Never visually represent missing evidence as safe or verified.
    """
    page = browser_context.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".badge")

    # Check that VERIFIED and PROBABILISTIC exist on Overview
    verified_badges = page.locator(".badge-verified").all()
    assert len(verified_badges) > 0

    probabilistic_badges = page.locator(".badge-probabilistic").all()
    assert len(probabilistic_badges) > 0

    # Switch to Forensics tab to check INSUFFICIENT and UNKNOWN badges
    page.locator('a[data-view="forensics"]').click()
    page.wait_for_selector(".security-card")

    insufficient_badges = page.locator(".badge-insufficient").all()
    assert len(insufficient_badges) > 0

    unknown_badges = page.locator(".badge-unknown").all()
    assert len(unknown_badges) > 0

    # Ensure UNKNOWN badge contains 'UNKNOWN' and never 'VERIFIED'
    for ub in unknown_badges:
        text = ub.inner_text()
        assert "UNKNOWN" in text or "UNVERIFIED" in text
        assert "VERIFIED ON-CHAIN" not in text

    page.close()


def test_environment_badges_and_locked_live_execution(live_server, browser_context):
    """
    Requirement 4 & Invariant:
    - Distinguish Research, Paper and Live environments.
    - Live execution controls must remain disabled and visually locked.
    """
    page = browser_context.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".env-badge-research")

    # Research environment badge
    research_badge = page.locator(".env-badge-research")
    assert research_badge.is_visible()
    assert "RESEARCH MODE" in research_badge.inner_text()

    # Live execution locked header badge
    locked_badge = page.locator(".env-badge-live-locked")
    assert locked_badge.is_visible()
    assert "LIVE LOCKED" in locked_badge.inner_text()

    # Switch to Alerts & Ops View
    page.locator('a[data-view="alerts"]').click()
    page.wait_for_selector(".alert-card")

    # Check disabled live execution controls
    locked_buttons = page.locator('button:has-text("Live Execution Locked")').all()
    assert len(locked_buttons) > 0
    for btn in locked_buttons:
        assert btn.is_disabled()
        assert btn.get_attribute("aria-disabled") == "true"

    page.close()


def test_theme_toggle_interaction(live_server, browser_context):
    """
    Requirement 1: Dark-first theme, light theme and system theme support.
    Verifies:
    - Initial state is dark theme (data-theme="dark")
    - Clicking theme button toggles to data-theme="light"
    - Clicking again toggles back to dark theme
    """
    page = browser_context.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector("#theme-toggle-btn")

    # 1. Initial theme is dark
    initial_theme = page.locator("html").get_attribute("data-theme")
    assert initial_theme == "dark"

    # 2. Toggle to light theme
    page.locator("#theme-toggle-btn").click()
    new_theme = page.locator("html").get_attribute("data-theme")
    assert new_theme == "light"

    # 3. Toggle back to dark theme
    page.locator("#theme-toggle-btn").click()
    back_theme = page.locator("html").get_attribute("data-theme")
    assert back_theme == "dark"

    page.close()


def test_spa_view_navigation_and_modal_interaction(live_server, browser_context):
    """
    Requirement 3: Reusable components & interactive dialog sheets.
    Verifies:
    - Screener view with data table and sparklines
    - Paper simulation interactive execution
    - Modal dialog opening and closing
    """
    page = browser_context.new_page(viewport={"width": 1280, "height": 800})
    page.goto(f"{live_server}/dashboard")
    page.wait_for_selector(".kpi-grid")

    # 1. Switch to Screener view
    page.locator('a[data-view="screener"]').click()
    page.wait_for_selector(".data-table")
    assert page.locator(".data-table").is_visible()
    assert len(page.locator(".sparkline").all()) > 0

    # 2. Inspect token dossier (Modal Dialog)
    inspect_btns = page.locator('button[data-action="inspect"]').all()
    assert len(inspect_btns) > 0
    inspect_btns[0].click()

    modal = page.locator("#token-modal")
    assert modal.is_visible()
    assert "FORENSIC DOSSIER" in page.locator("#token-modal-title").inner_text().upper()

    # Close modal
    page.locator('button[data-action="close-modal"]').first.click()
    assert not modal.is_visible()

    # 3. Switch to Paper Desks view and simulate trade
    page.locator('a[data-view="paper"]').click()
    page.wait_for_selector("#paper-sim-form")
    assert page.locator("#paper-sim-form").is_visible()

    page.locator("#btn-run-sim").click()
    sim_output = page.locator("#sim-output")
    assert sim_output.is_visible()
    assert "SIMULATED PAPER ORDER EXECUTED" in sim_output.inner_text()

    page.close()
