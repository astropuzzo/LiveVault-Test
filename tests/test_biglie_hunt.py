from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("biglie_control_panel", ROOT / "control-panel/server.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
hunt = panel.biglie_hunt
push = panel.web_push

# Real hunt.sh status lines: from the ASIAIR on 3 Nov 2026, and the shape before any run.
SEARCHING = '{"alive": true, "time": "2026-11-03T11:41:58+0000", "state": "searching", "round": 4}\n'
NEVER = '{"alive": false}\n'


def status(state, alive=True, machine=None, error=None, round_=3):
    data = {"alive": alive, "time": "2026-11-03T12:00:00+0000", "state": state, "round": round_}
    if machine:
        data.update(name="biglie-regia", ocpus=machine[0], gb=machine[1], ip=machine[2])
    if error:
        data["error"] = error
    return hunt.parse_status(json.dumps(data))


def test_status_reads_the_real_hunt_sh_lines():
    live = hunt.parse_status(SEARCHING)
    assert live == {"alive": True, "state": "searching", "round": 4, "machine": None, "error": None,
                    "updated": int(datetime(2026, 11, 3, 11, 41, 58, tzinfo=timezone.utc).timestamp())}
    assert hunt.parse_status(NEVER) == {"alive": False, "state": None, "round": None, "updated": None, "machine": None, "error": None}
    found = status("running", machine=(2, 12, "141.144.200.7"))
    assert found["machine"] == {"name": "biglie-regia", "ocpus": 2, "gb": 12, "ip": "141.144.200.7"}
    assert status("running", machine=(2, 12, "-"))["machine"]["ip"] is None


def test_status_drops_unknown_values_and_control_characters():
    raw = {"alive": "yes", "state": "<b>", "round": "4", "name": "biglie regia", "ocpus": True, "gb": 12,
           "ip": "1.2.3.4", "error": "\x1b[31mServiceError\x07: " + "x" * 400, "time": "ieri"}
    parsed = hunt.parse_status(json.dumps(raw))
    assert parsed["alive"] is False and parsed["state"] == "unknown" and parsed["round"] is None
    assert parsed["machine"] is None and parsed["updated"] is None
    assert parsed["error"].startswith("ServiceError: x") and len(parsed["error"]) == 300
    with pytest.raises(ValueError):
        hunt.parse_status("hunt.sh: not found\n")


class FakeHunt:
    """Stands in for hunt.sh and openastro-action, recording each call."""

    def __init__(self, line=SEARCHING, start_code=0):
        self.line, self.start_code, self.calls = line, start_code, []

    def __call__(self, args, timeout):
        if args[:2] == ["sudo", "-n"]:
            self.calls.append("helper:" + args[-1])
            return self.start_code, "Job failed" if self.start_code else "Ricerca Biglie avviata."
        command = args[1]
        self.calls.append(command)
        if command == "status":
            return 0, self.line
        if command == "log":
            return 0, "11:40:28 round 3: no room for any size, again in 90 s\n11:41:58 round 4: no room for any size, again in 90 s\n"
        if command == "stop":
            self.line = self.line.replace('"alive": true', '"alive": false')
        return 0, ""


@pytest.fixture
def hunt_dir(tmp_path, monkeypatch):
    (tmp_path / "hunt.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(hunt, "HUNT_DIR", tmp_path)
    monkeypatch.setattr(hunt, "_probe", {})
    monkeypatch.setattr(hunt, "_probe_at", 0.0)
    return tmp_path


def test_viewers_and_watcher_share_one_probe(hunt_dir, monkeypatch):
    fake = FakeHunt()
    monkeypatch.setattr(hunt, "_run", fake)
    first = hunt.snapshot()
    assert hunt.snapshot() == first
    assert fake.calls == ["status", "log"]
    assert first["log"][-1].startswith("11:41:58 round 4") and first["paused"] is False
    (hunt_dir / "paused").write_text("", encoding="utf-8")
    assert hunt.snapshot(force=True)["paused"] is True
    assert fake.calls == ["status", "log", "status", "log"]


def test_missing_folder_reads_as_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt, "HUNT_DIR", tmp_path / "absent")
    monkeypatch.setattr(hunt, "_probe", {})
    assert hunt.snapshot(force=True) == {"ok": True, "installed": False, "checked": pytest.approx(time.time(), abs=5)}
    assert hunt.action("start")[1] == 409


def test_start_goes_through_the_root_helper_only_when_the_hunt_is_down(hunt_dir, monkeypatch):
    fake = FakeHunt(NEVER)
    monkeypatch.setattr(hunt, "_run", fake)
    result, code = hunt.action("start")
    assert code == 200 and result["message"] == "Ricerca avviata."
    assert "helper:biglie_hunt_start" in fake.calls
    fake.calls.clear(); fake.line = SEARCHING
    result, code = hunt.action("start")
    assert code == 200 and "già attiva" in result["message"]
    assert "helper:biglie_hunt_start" not in fake.calls


def test_restart_stops_directly_then_starts_through_the_helper(hunt_dir, monkeypatch):
    fake = FakeHunt()
    monkeypatch.setattr(hunt, "_run", fake)
    before = time.time()
    result, code = hunt.action("restart")
    assert code == 200 and result["message"] == "Ricerca riavviata."
    assert fake.calls.index("stop") < fake.calls.index("helper:biglie_hunt_start")
    assert "restart" not in fake.calls and hunt._panel_stop_at >= before


def test_pause_resume_and_stop_run_hunt_sh(hunt_dir, monkeypatch):
    fake = FakeHunt()
    monkeypatch.setattr(hunt, "_run", fake)
    assert hunt.action("pause") == ({"ok": True, "message": "Pausa attivata: vale entro 5 secondi."}, 200)
    assert hunt.action("resume")[0]["message"] == "Ricerca ripresa."
    assert hunt.action("stop")[0]["message"] == "Ricerca fermata."
    assert hunt.action("stop")[0]["message"] == "La ricerca era già ferma."
    assert [call for call in fake.calls if call not in ("status", "log")] == ["pause", "resume", "stop"]
    assert hunt.action("rm -rf")[1] == 400


def test_helper_failure_is_reported(hunt_dir, monkeypatch):
    monkeypatch.setattr(hunt, "_run", FakeHunt(NEVER, start_code=1))
    result, code = hunt.action("start")
    assert code == 500 and result == {"ok": False, "error": "Job failed"}


def seen_after(*steps, boot="boot-a", panel_stop_at=None):
    """Runs the watcher over (status, seconds) steps and returns every notice in order."""
    base = 1_762_170_000
    stop = 0.0 if panel_stop_at is None else base + panel_stop_at
    seen, notes = None, []
    for current, seconds in steps:
        due, seen = hunt.notices(seen, current, boot, base + seconds, stop)
        notes += due
    return notes, seen


def test_first_check_only_remembers():
    notes, seen = seen_after((status("running", machine=(2, 12, "1.2.3.4")), 0))
    assert notes == [] and seen["machine"] == ["biglie-regia", 2, 12, "1.2.3.4"]


def test_found_grown_and_top_each_notify_once():
    notes, _ = seen_after(
        (status("searching"), 0),
        (status("running", machine=(2, 12, "1.2.3.4")), 30),
        (status("growing", machine=(2, 12, "1.2.3.4")), 60),
        (status("running", machine=(3, 18, "1.2.3.4")), 90),
        (status("running", machine=(4, 24, "1.2.3.4")), 120),
        (status("top", alive=False, machine=(4, 24, "1.2.3.4")), 420))
    assert [title for title, _, _ in notes] == ["Biglie: trovata la macchina Arm", "Biglie: la macchina Arm è cresciuta",
                                                "Biglie: la macchina Arm è cresciuta"]
    assert notes[0] == ("Biglie: trovata la macchina Arm", "2 OCPU e 12 GB. IP 1.2.3.4.", "high")
    assert notes[2][1] == "Ora ha 4 OCPU e 24 GB. IP 1.2.3.4."


def test_a_running_state_missed_between_checks_still_counts():
    notes, _ = seen_after((status("searching"), 0), (status("growing", machine=(1, 6, "1.2.3.4")), 30))
    assert notes == [("Biglie: trovata la macchina Arm", "1 OCPU e 6 GB. IP 1.2.3.4.", "high")]
    notes, _ = seen_after((status("searching"), 0), (status("top", machine=(4, 24, "1.2.3.4")), 30))
    assert notes[0][1].endswith("È la taglia massima gratuita: la ricerca è finita.")


def test_errors_notify_once_an_hour_or_when_the_text_changes():
    oops = "ServiceError: NotAuthenticated"
    notes, seen = seen_after(
        (status("searching"), 0),
        (status("error", error=oops, round_=0), 30),
        (status("searching", round_=1), 120),
        (status("error", error=oops, round_=0), 210),
        (status("error", error=oops, round_=0), 3700),
        (status("error", error="ConnectionError: timed out", round_=0), 3730))
    assert [body for _, body, _ in notes] == ["ServiceError: NotAuthenticated. La ricerca riprova da sola.",
                                              "ServiceError: NotAuthenticated. La ricerca riprova da sola.",
                                              "ConnectionError: timed out. La ricerca riprova da sola."]


def test_an_error_without_machine_fields_keeps_the_known_machine():
    notes, seen = seen_after(
        (status("growing", machine=(2, 12, "1.2.3.4")), 0),
        (status("error", error="x", round_=0), 30),
        (status("growing", machine=(2, 12, "1.2.3.4")), 120))
    assert [title for title, _, _ in notes] == ["Biglie: errore nella ricerca"]
    assert seen["machine"] == ["biglie-regia", 2, 12, "1.2.3.4"]


def test_a_dead_process_notifies_unless_stopped_on_purpose_or_finished():
    notes, _ = seen_after((status("searching"), 0), (status("searching", alive=False), 30))
    assert notes == [("Biglie: la ricerca si è fermata",
                      "Il processo non è più attivo (ultimo stato: in cerca di posto). Premi Avvia nel pannello.", "high")]
    assert seen_after((status("searching"), 0), (status("searching", alive=False), 30), panel_stop_at=10)[0] == []
    assert seen_after((status("searching"), 0), (status("stopped", alive=False), 30))[0] == []
    assert seen_after((status("searching"), 0), (status("error", alive=False, error="x"), 30))[0][0][0] == "Biglie: la ricerca si è fermata"


def test_a_reboot_gets_its_own_notice():
    seen = hunt.notices(None, status("searching"), "boot-a", 0, 0)[1]
    due, _ = hunt.notices(seen, status("searching", alive=False), "boot-b", 600, 0)
    assert due == [("Biglie: ricerca ferma dopo il riavvio",
                    "L’ASIAIR si è riavviata e la ricerca non riparte da sola. Premi Avvia nel pannello.", "normal")]


def test_watcher_persists_and_sends_to_the_panel(tmp_path, monkeypatch):
    sent, current = [], {"ok": True, "installed": True, **status("searching")}
    monkeypatch.setattr(hunt, "STATE_FILE", tmp_path / "biglie-hunt.json")
    monkeypatch.setattr(hunt, "_seen", None)
    monkeypatch.setattr(hunt, "_seen_loaded", False)
    monkeypatch.setattr(hunt, "_boot", lambda: "boot-a")
    monkeypatch.setattr(hunt, "snapshot", lambda force=False: current)
    monkeypatch.setattr(hunt.web_push, "notify", lambda *args: sent.append(args) or 1)
    assert hunt.watch_once(0) == [] and sent == []
    stamp = (tmp_path / "biglie-hunt.json").stat().st_mtime_ns
    hunt.watch_once(30)
    assert (tmp_path / "biglie-hunt.json").stat().st_mtime_ns == stamp
    current.update(status("running", machine=(4, 24, "1.2.3.4")))
    hunt.watch_once(60)
    assert sent == [("Biglie: trovata la macchina Arm", "4 OCPU e 24 GB. IP 1.2.3.4.", "/#apps", "high")]
    assert json.loads((tmp_path / "biglie-hunt.json").read_text(encoding="utf-8"))["machine"][1] == 4
    monkeypatch.setattr(hunt, "_seen_loaded", False)
    hunt.watch_once(90)
    assert len(sent) == 1


@pytest.fixture
def control_server(monkeypatch):
    monkeypatch.setattr(panel.Handler, "log_message", lambda *args: None)
    monkeypatch.setattr(panel, "_sessions", {"hunt-test": {"expires": time.time() + 60, "csrf": "csrf-1"}})
    server = panel.ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def call(url, body=None, csrf="csrf-1", cookie=True):
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = "openastro_session=hunt-test"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    data = None if body is None else json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers)) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as failure:
        return failure.code, json.loads(failure.read())


def test_endpoints_need_the_session_and_actions_need_csrf(control_server, monkeypatch):
    actions = []
    monkeypatch.setattr(hunt, "snapshot", lambda force=False: {"ok": True, "installed": True, "alive": True, "state": "searching"})
    monkeypatch.setattr(hunt, "action", lambda name: actions.append(name) or ({"ok": True, "message": "Fatto"}, 200))
    assert call(control_server + "/api/biglie/hunt", cookie=False)[0] == 401
    assert call(control_server + "/api/biglie/hunt") == (200, {"ok": True, "installed": True, "alive": True, "state": "searching"})
    assert call(control_server + "/api/biglie/hunt", {"action": "pause"}, csrf="wrong")[0] == 403
    assert call(control_server + "/api/biglie/hunt", {"action": "pause"}, cookie=False)[0] == 401
    assert actions == []
    assert call(control_server + "/api/biglie/hunt", {"action": "pause"}) == (200, {"ok": True, "message": "Fatto"})
    assert actions == ["pause"]


def test_push_endpoints(control_server, monkeypatch):
    subscribed = []
    monkeypatch.setattr(push, "public_key", lambda: "public-key")
    monkeypatch.setattr(push, "subscribe", lambda body: subscribed.append(body))
    monkeypatch.setattr(push, "notify", lambda *args, **kwargs: 0)
    assert call(control_server + "/api/push/key", cookie=False)[0] == 401
    assert call(control_server + "/api/push/key") == (200, {"ok": True, "key": "public-key"})
    assert call(control_server + "/api/push/subscribe", {"endpoint": "x"}, csrf=None)[0] == 403
    assert call(control_server + "/api/push/subscribe", {"endpoint": "x"})[0] == 200 and subscribed == [{"endpoint": "x"}]
    assert call(control_server + "/api/push/test", {}) == (502, {"ok": False, "error": "Nessun dispositivo ha ricevuto la prova."})


@pytest.fixture
def push_state(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "STATE_DIR", tmp_path)
    monkeypatch.setattr(push, "_key", None)
    return tmp_path


def browser():
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return private, public, os.urandom(16)


def test_payload_decrypts_on_the_browser_side(push_state):
    private, public, auth = browser()
    record = push.encrypt(push.b64(public), push.b64(auth), b'{"title":"prova"}')
    salt, size, length = record[:16], int.from_bytes(record[16:20], "big"), record[20]
    sender = record[21:21 + length]
    assert size == 4096 and length == 65
    shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender))
    secret = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + public + sender).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(secret)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(secret)
    assert AESGCM(key).decrypt(nonce, record[21 + length:], None) == b'{"title":"prova"}\x02'


def test_vapid_signature_verifies_with_the_published_key(push_state):
    header = push.vapid_header("https://fcm.googleapis.com/fcm/send/abc")
    token, key = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    raw = push.unb64(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64(key))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    body = json.loads(push.unb64(claims))
    assert body["aud"] == "https://fcm.googleapis.com" and body["sub"] == push.SUBJECT
    assert key == push.public_key() and (push_state / "vapid.pem").is_file()
    if os.name != "nt":
        assert (push_state / "vapid.pem").stat().st_mode & 0o777 == 0o600


def subscription(endpoint="https://fcm.googleapis.com/fcm/send/device-1"):
    _, public, auth = browser()
    return {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": push.b64(public), "auth": push.b64(auth)}}


def test_subscriptions_accept_only_browser_push_services(push_state):
    for endpoint in ("http://fcm.googleapis.com/x", "https://example.com/x", "https://fcm.googleapis.com.evil.example/x"):
        with pytest.raises(ValueError):
            push.subscribe(subscription(endpoint))
    broken = subscription()
    broken["keys"]["auth"] = push.b64(b"short")
    with pytest.raises(ValueError):
        push.subscribe(broken)
    push.subscribe(subscription())
    push.subscribe(subscription())
    push.subscribe(subscription("https://updates.push.services.mozilla.com/wpush/v2/x"))
    assert push.count() == 2
    for index in range(push.MAX_SUBSCRIPTIONS + 5):
        push.subscribe(subscription(f"https://web.push.apple.com/device-{index}"))
    assert push.count() == push.MAX_SUBSCRIPTIONS
    push.unsubscribe(f"https://web.push.apple.com/device-{push.MAX_SUBSCRIPTIONS + 4}")
    assert push.count() == push.MAX_SUBSCRIPTIONS - 1


def test_notify_sends_to_each_device_and_drops_the_ones_gone(push_state, monkeypatch):
    push.subscribe(subscription("https://fcm.googleapis.com/fcm/send/kept"))
    push.subscribe(subscription("https://fcm.googleapis.com/fcm/send/gone"))
    requests = []

    class Reply(io.BytesIO):
        status = 201

        def __enter__(self):
            return self

    def deliver(request):
        requests.append(request)
        if request.full_url.endswith("/gone"):
            raise urllib.error.HTTPError(request.full_url, 410, "Gone", {}, io.BytesIO())
        return Reply()

    monkeypatch.setattr(push, "_open", deliver)
    assert push.notify("Biglie", "Trovata", "/#apps", "high") == 1
    headers = {name.lower(): value for name, value in requests[0].header_items()}
    assert headers["authorization"].startswith("vapid t=") and headers["content-encoding"] == "aes128gcm"
    assert headers["urgency"] == "high" and headers["ttl"] == "86400"
    assert push.count() == 1
    monkeypatch.setattr(push, "_open", lambda request: (_ for _ in ()).throw(OSError("offline")))
    assert push.notify("Biglie", "Trovata") == 0 and push.count() == 1
