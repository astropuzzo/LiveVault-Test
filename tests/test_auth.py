from types import SimpleNamespace

import app.auth as auth


def test_session_token(monkeypatch):
    monkeypatch.setattr(auth, "settings", SimpleNamespace(app_secret="x" * 64, app_password="test", app_password_hash=""))
    token = auth.create_session_token()
    assert auth.verify_session_token(token)
    assert not auth.verify_session_token(token + "broken")
