import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_workflow_copy_in_sync():
    assert read(".github", "workflows", "trade_check.yml") == read("trade_check.yml")


def test_templates_are_valid_and_empty():
    pf = json.loads(read("portfolio.json"))
    assert pf["positions"] == [] and pf["manuel_haber"] == [] and pf["settings"]["sermaye"] == 0
    assert json.loads(read("journal.json")) == {"kayitlar": []}
    assert json.loads(read("state.json")) == {}


def test_no_secrets_or_ta_dependency():
    for name in os.listdir(ROOT):
        if name.endswith((".py", ".yml", ".md", ".txt")):
            txt = read(name)
            assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}", txt), name
            assert not re.search(r"\d{8,10}:[A-Za-z0-9_\-]{30,}", txt), name
    assert "\nta" not in "\n" + read("requirements.txt")


def test_cron_matches_turkey_times():
    y = read(".github", "workflows", "trade_check.yml")
    assert "'15 7,15 * * 1-5'" in y and "'30 9,11,13 * * 1-5'" in y
    assert "contents: write" in y
