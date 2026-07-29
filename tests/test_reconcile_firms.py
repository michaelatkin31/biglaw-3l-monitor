import yaml

import reconcile_firms


def test_recipient_source_reconciles_without_unresolved_names():
    result = reconcile_firms.reconcile(
        reconcile_firms.HERE / "firms.yaml",
        reconcile_firms.DEFAULT_SOURCE,
    )
    assert result["source_count"] == 163
    assert result["registry_count"] == 226
    assert result["ats_polled"] == 154
    assert result["entry_page_firms"] == 28
    assert result["effectively_polled"] == 182
    assert result["missing"] == []
    assert result["alias_errors"] == []
    assert result["duplicates"] == []
    assert result["resolved"]["Lane Powell"] == "Ballard Spahr"
    assert result["resolved"]["Lewis Roca"] == "Womble Bond Dickinson (US)"
    assert result["resolved"]["Ulmer & Berne"] == "UB Greensfelder"


def test_check_finds_a_missing_name(tmp_path):
    registry = tmp_path / "firms.yaml"
    source = tmp_path / "source.yaml"
    registry.write_text(yaml.safe_dump({"firms": [{"name": "Existing Firm"}]}))
    source.write_text(yaml.safe_dump({"firms": ["Existing Firm", "Missing Firm"]}))

    result = reconcile_firms.reconcile(registry, source)
    assert result["missing"] == ["Missing Firm"]
