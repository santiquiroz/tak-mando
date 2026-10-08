from datetime import datetime, timezone

from mando.roster import Roster

NOW = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)


def _event(uid="a1", typ="a-f-G-U-C", callsign="Recon", lat=5.0, lon=-75.0):
    return {
        "uid": uid,
        "type": typ,
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "stale": None,
    }


def test_valid_player_stored():
    roster = Roster()
    player = roster.update(_event(), NOW)
    assert player is not None
    assert player.callsign == "Recon"
    assert player.last_seen == NOW
    assert roster.get("a1") is player


def test_ignored_events():
    roster = Roster(ignore_uids=("skip-me",))
    assert roster.update(_event(typ="a-u-G"), NOW) is None
    assert roster.update(_event(callsign=None), NOW) is None
    assert roster.update(_event(callsign=""), NOW) is None
    assert roster.update(_event(lat=None, lon=-75.0), NOW) is None
    assert roster.update(_event(lat=0, lon=0), NOW) is None
    assert roster.update(_event(uid="skip-me"), NOW) is None
    assert roster.update(_event(uid="overlay-123"), NOW) is None
    assert roster.players() == []


def test_find_exact_then_unique_prefix():
    roster = Roster()
    roster.update(_event(uid="1", callsign="Recon"), NOW)
    roster.update(_event(uid="2", callsign="Thomas"), NOW)
    roster.update(_event(uid="3", callsign="Thor"), NOW)
    assert roster.find("recon").uid == "1"
    assert roster.find("RECON").uid == "1"
    assert roster.find("reco").uid == "1"
    assert roster.find("th") is None
    assert roster.find("thom").uid == "2"
    assert roster.find("nobody") is None


def test_players_sorted_case_insensitive():
    roster = Roster()
    roster.update(_event(uid="1", callsign="zulu"), NOW)
    roster.update(_event(uid="2", callsign="Alpha"), NOW)
    roster.update(_event(uid="3", callsign="mike"), NOW)
    assert [p.callsign for p in roster.players()] == ["Alpha", "mike", "zulu"]


def test_update_ignores_replayed_positions_whose_stale_time_passed():
    from datetime import datetime, timedelta, timezone

    from mando.roster import Roster

    now = datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc)
    roster = Roster()
    replayed = {"uid": "u1", "type": "a-f-G-U-C", "callsign": "Recon", "lat": 5.16, "lon": -75.49,
                "stale": now - timedelta(minutes=30)}
    fresh = dict(replayed, uid="u2", callsign="Thomas", stale=now + timedelta(seconds=30))
    assert roster.update(replayed, now) is None
    assert roster.update(fresh, now) is not None
    assert [p.callsign for p in roster.players()] == ["Thomas"]
