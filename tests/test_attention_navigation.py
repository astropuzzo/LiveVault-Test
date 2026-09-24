from pathlib import Path

from tests.css_contract import has_css, stylesheet


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_attention_opens_and_marks_problem_recordings():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "app/static/operations.js").read_text(encoding="utf-8")
    css = stylesheet()
    sw = (ROOT / "app/static/sw.js").read_text(encoding="utf-8")

    assert '<script src="/static/operations.js?v=' in html
    assert '<link rel="stylesheet" href="/static/style.css?v=' in html
    assert "recordings = await api('/api/recordings?limit=2000')" in js
    assert "recording.has_audio === false" in js
    assert "option.value = 'attention'" in js
    assert "openSystemAttention = async function openSystemAttentionTargeted" in js
    assert "lv-attention" in js
    assert has_css(css, ".rec-card.lv-attention")
    assert "/static/operations.js" in sw
    assert "/static/style.css" in sw
