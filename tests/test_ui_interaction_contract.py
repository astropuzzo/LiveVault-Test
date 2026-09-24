"""Interaction contracts for the LiveVault panel.

These pin behaviour that previously regressed: native browser dialogs, row
menus that never closed and periodic refreshes rebuilding the list under the
pointer.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
UI = (STATIC / "ui.js").read_text(encoding="utf-8")


def test_no_native_confirm_or_prompt_dialogs():
    for name in ("app.js", "ui.js", "operations.js", "workspace.js", "pulse-tuning.js", "creator-hover.js"):
        js = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"(?<![\w.])(confirm|prompt|alert)\(", js), name
    assert "function lvDialog(" in APP
    assert "dialog.showModal()" in APP
    assert "danger-solid" in APP


def test_row_menus_close_and_stay_exclusive():
    assert "function bindRowMenus()" in UI
    assert "closeRowMenus(menu)" in UI
    assert "document.addEventListener('pointerdown'" in UI
    assert "event.key !== 'Escape'" in UI


def test_periodic_refresh_does_not_rebuild_under_open_menu():
    assert "function viewInteractionActive()" in APP
    assert "if (viewInteractionActive()) pendingViewRender = true;" in APP
    assert "function setMarkup(root, html)" in APP
    for renderer in ("setMarkup(root, `<div class=\"monitor-summary\">", "setMarkup(root, visible.map(profile =>", "setMarkup(root,groups.slice("):
        assert renderer in UI
    # The product refresh wrapper must not render a second time per tick.
    wrapper = UI[UI.index("refresh = async function refreshProduct"):UI.index("function closeRowMenus")]
    assert "renderSources()" not in wrapper and "renderStatistics()" not in wrapper


def test_destructive_library_action_lives_in_the_overflow_menu():
    card = UI[UI.index("renderLibrary = function renderLibraryProduct"):UI.index("function attentionReasonsProduct")]
    assert 'class="danger-menu" type="button" data-lib-action="delete-profile"' in card
    assert "actionButton('trash','Elimina creator'" not in card
