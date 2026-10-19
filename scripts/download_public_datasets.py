"""
Télécharge 10 reçus SROIE et 10 reçus CORD avec leur ground truth.

Sources :
  - SROIE : https://github.com/zzzDavid/ICDAR-2019-SROIE  (data/img/NNN.jpg + data/key/NNN.json)
  - CORD  : https://huggingface.co/datasets/naver-clova-ix/cord-v2  (split test, via l'API datasets-server)
            dépôt d'origine : https://github.com/clovaai/cord

Usage :
    python scripts/download_public_datasets.py            # 10 + 10 par défaut
    python scripts/download_public_datasets.py --n 20

Si le réseau est bloqué : télécharger manuellement les fichiers ci-dessus et les placer dans
data/sroie/ (sroie_NNN.jpg + sroie_NNN.json) et data/cord/ (cord_NNN.jpg + cord_NNN.json),
puis relancer generate_dataset.py qui les intègre au manifest.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

SROIE_RAW = "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data"
CORD_ROWS = ("https://datasets-server.huggingface.co/rows?dataset=naver-clova-ix/cord-v2"
             "&config=default&split=test&offset={offset}&length={length}")


def _get(url, **kw):
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == 2:
                raise
            print(f"  retry ({e})")
            time.sleep(2)


def sroie_date_to_iso(s):
    s = (s or "").strip()
    for sep in ("/", "-", "."):
        parts = s.split(sep)
        if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
            d, m, y = (int(p) for p in parts)
            if y < 100:
                y += 2000
            return f"{y:04d}-{m:02d}-{d:02d}"
    return None


def download_sroie(n):
    out = DATA / "sroie"
    out.mkdir(parents=True, exist_ok=True)
    got = 0
    idx = 0
    while got < n and idx < n + 20:
        stem = f"{idx:03d}"
        idx += 1
        try:
            key = _get(f"{SROIE_RAW}/key/{stem}.json").json()
            img = _get(f"{SROIE_RAW}/img/{stem}.jpg").content
        except Exception as e:
            print(f"  SROIE {stem} ignoré : {e}")
            continue
        gt = {
            "company": key.get("company"),
            "date": key.get("date"),
            "address": key.get("address"),
            "total": key.get("total"),
        }
        (out / f"sroie_{stem}.jpg").write_bytes(img)
        (out / f"sroie_{stem}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
        got += 1
        print(f"  SROIE {stem} OK")
    return got


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def cord_to_gt(gt_parse):
    items = []
    for m in _as_list(gt_parse.get("menu")):
        items.append({
            "description": m.get("nm"),
            "quantity": m.get("cnt"),
            "unit_price": m.get("unitprice"),
            "amount": m.get("price"),
        })
    sub = gt_parse.get("sub_total") or {}
    tot = gt_parse.get("total") or {}
    if isinstance(sub, list):
        sub = sub[0] if sub else {}
    if isinstance(tot, list):
        tot = tot[0] if tot else {}
    return {
        "line_items": items,
        "subtotal": sub.get("subtotal_price"),
        "tax": sub.get("tax_price"),
        "service": sub.get("service_price"),
        "discount": sub.get("discount_price"),
        "total": tot.get("total_price"),
        "currency": "IDR",
    }


def download_cord(n):
    out = DATA / "cord"
    out.mkdir(parents=True, exist_ok=True)
    rows = _get(CORD_ROWS.format(offset=0, length=n)).json()["rows"]
    got = 0
    for row in rows:
        i = row["row_idx"]
        stem = f"{i:03d}"
        try:
            img = _get(row["row"]["image"]["src"]).content
        except Exception as e:
            print(f"  CORD {stem} ignoré : {e}")
            continue
        gt_parse = json.loads(row["row"]["ground_truth"])["gt_parse"]
        (out / f"cord_{stem}.jpg").write_bytes(img)
        (out / f"cord_{stem}.json").write_text(
            json.dumps({**cord_to_gt(gt_parse), "raw_gt_parse": gt_parse}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        got += 1
        print(f"  CORD {stem} OK")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    ok = True
    print("SROIE…")
    try:
        ok &= download_sroie(args.n) == args.n
    except Exception as e:
        ok = False
        print(f"SROIE échec : {e}")
    print("CORD…")
    try:
        ok &= download_cord(args.n) == args.n
    except Exception as e:
        ok = False
        print(f"CORD échec : {e}")
    if not ok:
        print(__doc__)
        sys.exit(1)
    print("Terminé.")


if __name__ == "__main__":
    main()
