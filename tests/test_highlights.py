from app import highlights


def test_roundtrip_dedupes_and_keeps_order(tmp_path):
    p = tmp_path / "h.json"
    assert highlights.load(p) == []
    assert highlights.save(p, ["SOL", "BTC", "SOL", " "]) == ["SOL", "BTC"]
    assert highlights.load(p) == ["SOL", "BTC"]


def test_corrupt_file_loads_as_empty(tmp_path):
    p = tmp_path / "h.json"
    p.write_text("{not json")
    assert highlights.load(p) == []
