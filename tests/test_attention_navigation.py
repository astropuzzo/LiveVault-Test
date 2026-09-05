from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_attention_opens_and_marks_problem_recordings():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "app/static/operations.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    sw = (ROOT / "app/static/sw.js").read_text(encoding="utf-8")

    assert '<script src="/static/operations.js" defer></script>' in html
    assert '<link rel="stylesheet" href="/static/style.css">' in html
    assert "recordings = await api('/api/recordings?limit=2000')" in js
    assert "recording.has_audio === false" in js
    assert "option.value = 'attention'" in js
    assert "openSystemAttention = async function openSystemAttentionTargeted" in js
    assert "lv-attention" in js
    assert ".rec-card.lv-attention" in css
    assert "/static/operations.js" in sw
    assert "/static/style.css" in sw
