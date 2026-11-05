"""Desk profiles: separate data per person, a key that only opens the desk, preferences that stick."""

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.alphadesk import profiles, store
from src.api.security import require_auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.delenv("VIBE_TRADING_API_KEY", raising=False)
    return tmp_path


def test_roots_are_separate(home):
    key = profiles.create("ayushi", "Ayushi")
    owner = store.alphadesk_root()
    with profiles.use("ayushi"):
        hers = store.alphadesk_root()
    assert owner == home / ".vibe-trading" / "alphadesk"
    assert hers == home / ".vibe-trading" / "desk_profiles" / "ayushi" / "alphadesk"
    assert owner != hers and key.startswith("dk_")


def test_only_a_hash_is_stored(home):
    key = profiles.create("ayushi", "Ayushi")
    assert key not in profiles.registry_path().read_text()
    assert profiles.profile_for_token(key) == "ayushi"
    assert profiles.profile_for_token(key + "x") is None
    assert profiles.profile_for_token("") is None


def test_bad_and_duplicate_ids(home):
    for bad in ("", "A", "../x", "a/b", "1abc"):
        with pytest.raises(ValueError):
            profiles.create(bad, "x")
    profiles.create("ayushi", "A")
    with pytest.raises(ValueError):
        profiles.create("ayushi", "A")


def test_carry_keeps_profile_on_a_thread(home):
    import threading

    profiles.create("ayushi", "A")
    seen = []
    with profiles.use("ayushi"):
        t = threading.Thread(target=profiles.carry(lambda: seen.append(profiles.current_id())))
    t.start(); t.join()
    t2 = threading.Thread(target=lambda: seen.append(profiles.current_id()))
    t2.start(); t2.join()
    assert seen == ["ayushi", None]


def test_prefs_are_cleaned():
    got = profiles.clean_prefs({"layout": "bogus", "density": "compact", "hidden_rows": ["qty", "Bad Id", "qty", 5],
                                "tiles": ["a", "b"], "evil": 1})
    assert got["layout"] == "tabs" and got["density"] == "compact"
    assert got["hidden_rows"] == ["qty"] and got["tiles"] == ["a", "b"] and "evil" not in got
    assert profiles.clean_prefs("junk") == profiles.default_prefs()


def test_paths_a_profile_key_may_reach():
    assert profiles.path_allowed("/api/desk/board/paper")
    assert profiles.path_allowed("/api/desk/intraday/days")
    for blocked in ("/api/desk/live/unlock", "/api/desk/signal/x/approve", "/api/desk/audit/export",
                    "/api/settings", "/api/runs", "/sessions"):
        assert not profiles.path_allowed(blocked)


def _app():
    app = FastAPI()

    @app.get("/api/desk/who", dependencies=[Depends(require_auth)])
    def who():
        return {"id": profiles.current_id(), "root": str(store.alphadesk_root())}

    @app.get("/api/settings", dependencies=[Depends(require_auth)])
    def settings():
        return {"ok": True}

    return app


def test_profile_key_binds_the_request_and_is_fenced(home, monkeypatch):
    key = profiles.create("ayushi", "Ayushi")
    c = TestClient(_app())
    assert c.get("/api/desk/who").json()["id"] is None  # loopback owner
    r = c.get("/api/desk/who", headers={"Authorization": f"Bearer {key}"})
    assert r.json()["id"] == "ayushi" and "desk_profiles/ayushi" in r.json()["root"]
    assert c.get("/api/desk/who").json()["id"] is None  # nothing leaks to the next request
    assert c.get("/api/settings", headers={"Authorization": f"Bearer {key}"}).status_code == 403


def test_owner_key_still_works_and_profile_key_works_with_it_set(home, monkeypatch):
    key = profiles.create("ayushi", "Ayushi")
    monkeypatch.setenv("API_AUTH_KEY", "owner-secret")
    c = TestClient(_app())
    assert c.get("/api/desk/who").status_code == 401
    assert c.get("/api/desk/who", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/desk/who", headers={"Authorization": "Bearer owner-secret"}).json()["id"] is None
    assert c.get("/api/desk/who", headers={"Authorization": f"Bearer {key}"}).json()["id"] == "ayushi"
    assert c.get("/api/settings", headers={"Authorization": "Bearer owner-secret"}).status_code == 200


def test_trades_and_prefs_do_not_cross(home):
    profiles.create("ayushi", "Ayushi")
    (store.alphadesk_root() / "board_paper").mkdir(exist_ok=True)
    (store.alphadesk_root() / "board_paper" / "mine.json").write_text("{}")
    profiles.save_prefs({"density": "compact"})
    with profiles.use("ayushi"):
        assert not list((store.alphadesk_root() / "board_paper").glob("*.json"))
        assert profiles.load_prefs()["density"] == "comfortable"
        profiles.save_prefs({"layout": "stacked"})
    assert profiles.load_prefs()["layout"] == "tabs" and profiles.load_prefs()["density"] == "compact"
    assert json.loads((store.alphadesk_root() / "prefs.json").read_text())["density"] == "compact"


def test_a_removed_profile_key_does_not_fall_back_to_the_owner(home):
    key = profiles.create("ayushi", "Ayushi")
    profiles.rotate_key("ayushi")  # the old key is now dead
    c = TestClient(_app())
    assert c.get("/api/desk/who", headers={"Authorization": f"Bearer {key}"}).status_code == 401
