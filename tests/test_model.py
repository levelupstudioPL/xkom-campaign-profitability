"""Testy logiki alokacji na małym, ręcznie policzonym przykładzie."""
from datetime import date

import pandas as pd
import pytest

from app.config import get_settings
from app.model import Params, compute

D = date(2026, 10, 1)


def frames():
    camp = pd.DataFrame([
        # kampania Search 1000 zł, Shopping 600 zł (koszt produktowy 500 → 100 resztowy), Display 300 zł bez ruchu w GA4
        {"campaign_id": "1", "campaign_name": "Search A", "channel_type": "SEARCH", "cost": 1000.0, "clicks": 100},
        {"campaign_id": "2", "campaign_name": "Shopping B", "channel_type": "SHOPPING", "cost": 600.0, "clicks": 50},
        {"campaign_id": "3", "campaign_name": "Display C", "channel_type": "DISPLAY", "cost": 300.0, "clicks": 10},
    ])
    prod = pd.DataFrame([
        {"campaign_id": "2", "campaign_name": "Shopping B", "item_id": "SKU-1", "product_title": "P1", "product_type_l1": "Kat X", "cost": 400.0, "clicks": 40},
        {"campaign_id": "2", "campaign_name": "Shopping B", "item_id": "sku-2", "product_title": "P2", "product_type_l1": "Kat Y", "cost": 100.0, "clicks": 10},
    ])
    ga = pd.DataFrame([
        # Search: dopasowanie po ID; widoki 75/25 → 750/250 zł
        {"campaign_id": "1", "campaign_name": "Search A", "item_id": "SKU-1", "item_name": "P1", "item_category": "Kat X", "views": 75.0, "units": 5.0, "revenue": 1230.0},
        {"campaign_id": "1", "campaign_name": "Search A", "item_id": "SKU-2", "item_name": "P2", "item_category": "Kat Y", "views": 25.0, "units": 0.0, "revenue": 0.0},
        # Shopping: dopasowanie po nazwie (brak ID); reszta 100 zł → cała na SKU-2
        {"campaign_id": "", "campaign_name": "shopping b", "item_id": "SKU-2", "item_name": "P2", "item_category": "Kat Y", "views": 10.0, "units": 2.0, "revenue": 246.0},
        # ruch nieopłacany
        {"campaign_id": "", "campaign_name": "(organic)", "item_id": "SKU-1", "item_name": "P1", "item_category": "Kat X", "views": 100.0, "units": 5.0, "revenue": 1230.0},
    ])
    return camp, prod, ga


def run(**kw):
    p = Params(D, D, **kw)
    return compute(p, get_settings(), frames=frames(), margins={"Kat X": 50.0, "Kat Y": 10.0})


def item(r, iid):
    df = r["products"]
    return df[df.id.str.lower() == iid.lower()].iloc[0]


def test_allocation_direct_and_by_views():
    r = run()
    p1, p2 = item(r, "SKU-1"), item(r, "SKU-2")
    assert p1.cost_direct == pytest.approx(400)
    assert p1.cost_alloc == pytest.approx(750)
    assert p2.cost_direct == pytest.approx(100)
    assert p2.cost_alloc == pytest.approx(250 + 100)
    # koszt nieprzypisany = Display bez wyświetleń
    cats = {c["category"]: c for c in r["categories"]}
    assert cats["Nieprzypisane"]["cost"] == pytest.approx(300)
    assert r["totals"]["total_cost"] == pytest.approx(1900)


def test_margin_and_unit_economics_attributed():
    r = run(basis="attr", revenue_gross=True)
    p1 = item(r, "SKU-1")
    # przychód atrybuowany 1230 brutto → 1000 netto, marża 50% → 500 zł; koszt 1150
    assert p1.revenue == pytest.approx(1000)
    assert p1.margin == pytest.approx(500)
    assert p1.cost_unit == pytest.approx(1150 / 5)
    # śr. cena netto z całej sprzedaży (2460/1.23/10 = 200) × 50% = 100 zł marży na szt.
    assert p1.margin_unit == pytest.approx(100)
    assert p1.status == "Strata"


def test_blended_basis_uses_all_sales():
    r = run(basis="all", revenue_gross=True)
    p1 = item(r, "SKU-1")
    assert p1.units == 10
    assert p1.revenue == pytest.approx(2000)


def test_channel_filter():
    r = run(channel="Search")
    assert r["totals"]["total_cost"] == pytest.approx(1000)
    assert r["types"] == ["Search"]


def test_item_id_normalization_joins_case_insensitive():
    r = run()
    df = r["products"]
    assert len(df) == 2
    assert set(df.id.str.lower()) == {"sku-1", "sku-2"}
