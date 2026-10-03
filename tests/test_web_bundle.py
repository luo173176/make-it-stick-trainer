"""The phone bundle ships its own copy of the demo cards; keep it identical."""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI_SEED = json.loads((REPO / "examples" / "seed.json").read_text(encoding="utf-8"))
WEB_SEED = json.loads((REPO / "web" / "seed.json").read_text(encoding="utf-8"))


def test_seed_cards_are_identical():
    assert [card["question"] for card in WEB_SEED["cards"]] == [
        card["question"] for card in CLI_SEED["cards"]
    ], "web/seed.json 与 examples/seed.json 漂移了，运行 cp examples/seed.json web/seed.json"


def test_seed_card_fields_match():
    for web, cli in zip(WEB_SEED["cards"], CLI_SEED["cards"]):
        assert web["answer"] == cli["answer"], web["question"]
        assert web["topic"] == cli["topic"], web["question"]
        assert web.get("tags") == cli.get("tags"), web["question"]


def test_topics_match():
    assert {topic["name"] for topic in WEB_SEED["topics"]} == {
        topic["name"] for topic in CLI_SEED["topics"]
    }


def test_web_bundle_files_exist():
    for name in (
        "index.html",
        "app.js",
        "core.js",
        "store.js",
        "sw.js",
        "manifest.webmanifest",
        "icon.svg",
        "icon-maskable.svg",
        "seed.json",
    ):
        assert (REPO / "web" / name).exists(), name


def test_manifest_points_at_files_that_exist():
    manifest = json.loads((REPO / "web" / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert (REPO / "web" / manifest["start_url"].lstrip("./")).exists() or manifest["start_url"] == "./"
    for icon in manifest["icons"]:
        assert (REPO / "web" / icon["src"]).exists(), icon["src"]


def test_service_worker_precaches_every_bundle_file():
    source = (REPO / "web" / "sw.js").read_text(encoding="utf-8")
    for name in ("app.js", "core.js", "store.js", "manifest.webmanifest", "icon.svg"):
        assert f"./{name}" in source, f"{name} 没有被预缓存，离线时会白屏"
