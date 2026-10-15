from lineage.config import load_settings


def test_defaults():
    s = load_settings({})
    assert (s.falkordb_host, s.falkordb_port, s.falkordb_graph) == ("localhost", 6379, "lineage")
    assert s.falkordb_password is None


def test_env_override():
    s = load_settings({"FALKORDB_PORT": "6380", "FALKORDB_PASSWORD": "x"})
    assert s.falkordb_port == 6380 and s.falkordb_password == "x"
