# -*- coding: utf-8 -*-
"""GeocoderService.geocode_checked の回帰テスト（3ツールで同一のクラスを検証する）。

streamlit などに依存しないよう、ソースから GeocoderService と haversine だけを取り出して実行する。
`-m network` のテストは国土地理院(GSI)に実際に問い合わせる。
実行: pytest tests/test_geocode_checked.py            （通信なしのみ: -m "not network"）
"""
import ast
import math
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pytest
import requests

TOOLS = Path(__file__).resolve().parent.parent / "tools"
FILES = ["薬局出店分析ツール.py", "medical_finder.py", "pharmacy_finder.py"]


def _load(fname):
    tree = ast.parse((TOOLS / fname).read_text(encoding="utf-8"))
    wanted = [n for n in tree.body
              if (isinstance(n, ast.ClassDef) and n.name == "GeocoderService")
              or (isinstance(n, ast.FunctionDef) and n.name == "haversine")]
    ns = {"re": re, "math": math, "time": time, "requests": requests,
          "Optional": Optional, "Tuple": Tuple, "List": List}
    exec(compile(ast.Module(wanted, []), fname, "exec"), ns)
    return ns["GeocoderService"](), ns["haversine"]


@pytest.fixture(params=FILES)
def geo(request):
    return _load(request.param)


KYOTO = "京都市上京区丸太町通河原町東入ル駒之町561-1"


# ── 通信なし ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("京都市上京区駒之町561-1", "駒の町"),   # 之→の に寄せて比較する
    ("東京都千代田区丸の内1-9-1", "丸の内"),
    ("東京都千代田区丸の内一丁目９番", "丸の内一丁目"),
    ("山梨県北杜市白州町白須1234", "白州町白須"),
    ("福島県郡山市堤下町1-1", "堤下町"),
    ("北海道札幌市中央区北1条西2丁目", "北"),
    ("京都市上京区", ""),
])
def test_town_of(geo, text, expected):
    g, _ = geo
    assert g._town_of(text) == expected


def test_kyoto_street_is_removed(geo):
    g, _ = geo
    clean = g._clean(KYOTO)
    assert g._KYOTO_STREET_RE.sub("", clean) == "京都市上京区駒之町561-1"
    assert g._KYOTO_STREET_RE.sub("", g._clean("京都市中京区寺町通御池下ル下本能寺前町1")) \
        == "京都市中京区下本能寺前町1"


def test_street_regex_leaves_normal_addresses(geo):
    g, _ = geo
    for a in ["東京都新宿区高田馬場3丁目", "山梨県北杜市白州町白須1234", "東京都千代田区丸の内1-9-1"]:
        assert g._KYOTO_STREET_RE.sub("", g._clean(a)) == g._clean(a)


def test_title_ok_rejects_the_wrong_town(geo):
    g, _ = geo
    # 今回の不具合：駒之町を探して丸屋町が返ってきた
    assert not g._title_ok(g._clean(KYOTO), "京都府京都市上京区丸屋町")
    # 区止まりの座標も「町まで特定できていない」ので不採用
    assert not g._title_ok("京都市上京区駒之町561-1", "京都府京都市上京区")


def test_title_ok_accepts_notation_variants(geo):
    g, _ = geo
    assert g._title_ok("京都市上京区駒之町561-1", "京都府京都市上京区駒之町５６１番地")
    assert g._title_ok("東京都千代田区丸の内1-9-1", "東京都千代田区丸の内一丁目９番")
    assert g._title_ok("東京都千代田区丸の内1", "東京都千代田区丸の内一丁目")
    assert g._title_ok("京都市上京区", "京都府京都市上京区")       # 町名なしは照合しない


def test_direct_latlon_is_adopted(geo):
    g, _ = geo
    r = g.geocode_checked("35.0171, 135.7701")
    assert r["status"] == "ok" and r["coords"] == (35.0171, 135.7701) and r["source"] == "直接入力"
    assert g.geocode_checked("0.5, 0.5")["status"] != "ok"        # 日本国外は直接入力でも通さない


# ── GSIへ実際に問い合わせる ──────────────────────────────────────────────
@pytest.mark.network
def test_kyoto_street_address_is_not_placed_in_maruya_cho(geo):
    g, hav = geo
    r = g.geocode_checked(KYOTO)
    assert r["status"] == "ok", r
    assert "駒之町" in r["title"]
    # 正しい駒之町561番地(35.01711, 135.77008)から200m以内。誤りの丸屋町は約1.7km西
    assert hav(35.01711, 135.77008, *r["coords"]) < 200


@pytest.mark.network
def test_unmatched_address_is_stopped_not_guessed(geo):
    g, _ = geo
    r = g.geocode_checked("京都市上京区ほげ通河原町東入ル存在しない町1-1")
    assert r["status"] in ("mismatch", "fail")
    assert r["coords"] is None


@pytest.mark.network
@pytest.mark.parametrize("addr", [
    "東京都千代田区丸の内1-9-1",
    "東京都新宿区高田馬場3丁目",
    "山梨県北杜市白州町白須1234",
    "山口県山口市亀山町2-1",
])
def test_ordinary_addresses_still_pass(geo, addr):
    g, _ = geo
    r = g.geocode_checked(addr)
    assert r["status"] == "ok", r
