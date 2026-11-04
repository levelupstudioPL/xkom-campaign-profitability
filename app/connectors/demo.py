"""Dane syntetyczne (APP_MODE=demo) — te same tabele co w trybie live, bez połączeń z Google."""
from __future__ import annotations

import random
from datetime import date, timedelta

import pandas as pd

CATS = {
    "Laptopy": (7, 0.012, [("LAP-1001", 'Laptop Lenovo IdeaPad Slim 5 16"', 3499), ("LAP-1002", "Laptop ASUS Vivobook 15 i5", 2799), ("LAP-1003", "Laptop MacBook Air 13 M3", 5299), ("LAP-1004", "Laptop HP Victus 16 RTX 4060", 4599), ("LAP-1005", "Laptop Dell Latitude 5450", 4999), ("LAP-1006", "Laptop Acer Aspire 3 Ryzen 5", 2199)]),
    "Smartfony": (5, 0.018, [("SMF-2001", "Smartfon Samsung Galaxy S25 256GB", 4199), ("SMF-2002", "Smartfon iPhone 16 128GB", 4299), ("SMF-2003", "Smartfon Xiaomi Redmi Note 14", 999), ("SMF-2004", "Smartfon Motorola edge 50", 1599), ("SMF-2005", "Smartfon Google Pixel 9a", 2199)]),
    "Karty graficzne": (6, 0.015, [("GPU-3001", "Karta graficzna RTX 5070 12GB", 2899), ("GPU-3002", "Karta graficzna RTX 5060 Ti 16GB", 2199), ("GPU-3003", "Karta graficzna RX 9070 XT", 3299), ("GPU-3004", "Karta graficzna RTX 5090 32GB", 11999)]),
    "Monitory": (10, 0.016, [("MON-4001", 'Monitor LG UltraGear 27" 180Hz', 899), ("MON-4002", 'Monitor Dell 24" P2425H', 749), ("MON-4003", 'Monitor Samsung Odyssey G5 32"', 1399), ("MON-4004", "Monitor AOC 24G4X", 549)]),
    "Słuchawki": (22, 0.024, [("AUD-5001", "Słuchawki Sony WH-1000XM5", 1299), ("AUD-5002", "Słuchawki JBL Tune 670NC", 299), ("AUD-5003", "Słuchawki SteelSeries Arctis Nova 5", 549), ("AUD-5004", "Słuchawki Apple AirPods 4", 649)]),
    "Akcesoria komputerowe": (32, 0.03, [("ACC-6001", "Mysz Logitech G502 X", 299), ("ACC-6002", "Klawiatura SteelSeries Apex 3", 279), ("ACC-6003", "Podkładka SteelSeries QcK XXL", 129), ("ACC-6004", "Hub USB-C 7w1 Unitek", 149), ("ACC-6005", "Kabel HDMI 2.1 2m", 59)]),
    "Konsole i gry": (8, 0.02, [("CON-7001", "Konsola PlayStation 5 Slim", 2299), ("CON-7002", "Konsola Nintendo Switch 2", 1999), ("CON-7003", "Pad Sony DualSense", 299), ("CON-7004", "Gra EA Sports FC 26 PS5", 249)]),
    "Smartwatche": (15, 0.017, [("SMW-8001", "Smartwatch Samsung Galaxy Watch 8", 1299), ("SMW-8002", "Smartwatch Huawei Watch GT 5", 899), ("SMW-8003", "Opaska Xiaomi Smart Band 9", 179)]),
}
ALL = list(CATS)
# nazwa, kanał (enum Google Ads), koszt / 30 dni, kategorie, mnożnik CR, udział kosztu w raporcie produktowym
CAMPAIGNS = [
    ("PL | Search | Brand x-kom", "SEARCH", 86000, ALL, 1.0, 0),
    ("PL | Search | Laptopy generic", "SEARCH", 64000, ["Laptopy"], 1.4, 0),
    ("PL | Search | Smartfony generic", "SEARCH", 52000, ["Smartfony"], 1.3, 0),
    ("PL | Search | Karty graficzne", "SEARCH", 33000, ["Karty graficzne"], 1.2, 0),
    ("PL | Search | Monitory DSA", "SEARCH", 14000, ["Monitory"], 0.8, 0),
    ("PL | Shopping | Laptopy", "SHOPPING", 58000, ["Laptopy"], 1.0, 1.0),
    ("PL | PMax | Elektronika", "PERFORMANCE_MAX", 142000, ["Smartfony", "Laptopy", "Karty graficzne", "Monitory", "Konsole i gry", "Smartwatche"], 1.1, 0.78),
    ("PL | PMax | Akcesoria i audio", "PERFORMANCE_MAX", 38000, ["Akcesoria komputerowe", "Słuchawki"], 1.0, 0.85),
    ("PL | Display | Remarketing 30d", "DISPLAY", 21000, ALL, 0.5, 0),
    ("PL | YouTube | Konsole Q4", "VIDEO", 27000, ["Konsole i gry"], 0.35, 0),
    ("PL | Demand Gen | Test kreacji", "DEMAND_GEN", 4200, [], 0, 0),
]
DEMO_MARGINS = {c: v[0] for c, v in CATS.items()}


def generate(d_from: date, d_to: date, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = random.Random(seed)
    pop, prod_cat, price = {}, {}, {}
    for c, (_, _, items) in CATS.items():
        for iid, _, p in items:
            pop[iid] = 0.35 + base.random() * 1.3 * (1.6 if p < 600 else 1)
            prod_cat[iid] = c
            price[iid] = p

    ads, shop, ga = [], [], []
    d = d_from
    while d <= d_to:
        R = random.Random(f"{seed}-{d.isoformat()}")
        season = 1.0 + (0.35 if d.month == 11 else 0) + (0.15 if d.weekday() >= 5 else 0)
        for idx, (name, ch, cost30, targets, cr, shop_share) in enumerate(CAMPAIGNS):
            cid = str(20_000_000 + idx)
            cost = cost30 / 30 * season * (0.8 + R.random() * 0.4)
            ads.append((d, "1234567890", cid, name, ch, round(cost, 2), int(cost / (0.9 + R.random() * 1.4)), int(cost * 30)))
            items = [it for c in targets for it in CATS[c][2]]
            w = [pop[it[0]] * (0.6 + R.random() * 0.8) for it in items]
            W = sum(w) or 1
            spp = 1.6 if ch in ("DISPLAY", "VIDEO") else 0.62
            noise = [0.75 + R.random() * 0.5 for _ in items]
            shop_w = [wi * n for wi, n in zip(w, noise, strict=False)]
            SW = sum(shop_w) or 1
            for (iid, nm, p), wi, sw in zip(items, w, shop_w, strict=False):
                c = prod_cat[iid]
                views = round(cost * spp * wi / W * (0.8 + R.random() * 0.4))
                crp = CATS[c][1] * cr * (0.5 + R.random()) * (0.6 if p > 4000 else 1)
                units = sum(1 for _ in range(views) if R.random() < crp) if views < 4000 else round(views * crp)
                ga.append((d, cid if ch != "DEMAND_GEN" else "", name, iid, nm, c, float(views), float(units), round(units * p * (0.93 + R.random() * 0.07), 2)))
                if shop_share > 0:
                    shop.append((d, "1234567890", cid, name, iid.lower(), nm, c, round(cost * shop_share * sw / SW, 2), int(views * 0.9), int(views * 40)))
        for src, f in (("(organic)", 1.9), ("(direct)", 1.4), ("(referral)", 0.35), ("newsletter_listopad", 0.25)):
            for c, (_, ccr, items) in CATS.items():
                for iid, nm, p in items:
                    views = round(4200 / 30 * f * pop[iid] * (0.7 + R.random() * 0.6))
                    units = sum(1 for _ in range(views) if R.random() < ccr * (0.6 if p > 4000 else 1) * 1.2)
                    ga.append((d, "", src, iid, nm, c, float(views), float(units), round(units * p * (0.93 + R.random() * 0.07), 2)))
        d += timedelta(days=1)

    ads_df = pd.DataFrame(ads, columns=["date", "customer_id", "campaign_id", "campaign_name", "channel_type", "cost", "clicks", "impressions"])
    shop_df = pd.DataFrame(shop, columns=["date", "customer_id", "campaign_id", "campaign_name", "item_id", "product_title", "product_type_l1", "cost", "clicks", "impressions"])
    ga_df = pd.DataFrame(ga, columns=["date", "campaign_id", "campaign_name", "item_id", "item_name", "item_category", "items_viewed", "items_purchased", "item_revenue"])
    return ads_df, shop_df, ga_df
