from kairo.web.pins import read_pins, toggle_pin, write_pins


def test_read_pins_missing_is_empty(tmp_path):
    assert read_pins(tmp_path) == []


def test_read_pins_skips_dirty_and_non_strings(tmp_path):
    (tmp_path / "pinned.yaml").write_text("not-a-list: 1\n")
    assert read_pins(tmp_path) == []
    (tmp_path / "pinned.yaml").write_text("- alpha\n- 12\n- beta\n- alpha\n")
    assert read_pins(tmp_path) == ["alpha", "beta"]


def test_toggle_pin_prepends_then_removes(tmp_path):
    known = {"alpha", "beta", "gamma"}
    assert toggle_pin(tmp_path, "beta", known) == ["beta"]
    assert toggle_pin(tmp_path, "alpha", known) == ["alpha", "beta"]
    assert read_pins(tmp_path) == ["alpha", "beta"]
    assert toggle_pin(tmp_path, "alpha", known) == ["beta"]
    assert read_pins(tmp_path) == ["beta"]


def test_toggle_pin_drops_ghost_slugs_on_write(tmp_path):
    write_pins(tmp_path, ["gone", "beta"])
    known = {"alpha", "beta"}
    assert toggle_pin(tmp_path, "alpha", known) == ["alpha", "beta"]
