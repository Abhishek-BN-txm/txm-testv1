"""atc.recv as a list: one TrLim per (case, send, recv) with keys case|send>recv."""
from tara_automation.study_pipeline import _atc_key, _recv_list, _split_atc_key


def test_recv_list_single_and_many():
    assert _recv_list({"recv": "CAISO"}) == ["CAISO"]
    assert _recv_list({"recv": ["LL_A", "LL_B"]}) == ["LL_A", "LL_B"]
    assert _recv_list({}) == [] and _recv_list(None) == []


def test_keys_round_trip():
    assert _atc_key("pre", "ED5", "CAISO", multi=False) == "pre|ED5"
    assert _split_atc_key("pre|ED5", "CAISO") == ("pre", "ED5", "CAISO")
    k = _atc_key("pre", "HUBS", "LL_TSMC", multi=True)
    assert k == "pre|HUBS>LL_TSMC"
    assert _split_atc_key(k, None) == ("pre", "HUBS", "LL_TSMC")
