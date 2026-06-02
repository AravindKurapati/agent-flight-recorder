from agent_flight_recorder.db import get_connection, init_db, get_config, set_config, get_all_config


def test_config_roundtrip(tmp_path):
    conn = get_connection(tmp_path / "afr.db")
    init_db(conn)
    assert get_config(conn, "timezone") is None
    assert get_config(conn, "timezone", "UTC") == "UTC"
    set_config(conn, "timezone", "America/New_York")
    assert get_config(conn, "timezone") == "America/New_York"
    set_config(conn, "timezone", "Europe/London")  # upsert
    assert get_config(conn, "timezone") == "Europe/London"
    set_config(conn, "weekly_reset_weekday", "2")
    assert get_all_config(conn) == {
        "timezone": "Europe/London",
        "weekly_reset_weekday": "2",
    }
    conn.close()
