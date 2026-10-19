"""
Compare ce que n8n a écrit dans Postgres (email_log / processing_log) au manifest.

    python scripts/evaluate.py --run-id run-20260930-101500
    Connexion : variable EXTRACTION_DSN ou --dsn, ex. Supabase (Session pooler) :
    postgresql://postgres.<ref>:<mot_de_passe>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require

Mesures : filtrage (Step 2), éclatement des pièces jointes (Step 3), classification (Step 5),
exactitude des champs extraits (Step 6), auto-approbation vs revue humaine (Steps 7-9), couverture (Step 10).
Rapport écrit dans reports/<run_id>.md
"""
import argparse
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]

# Variables du fichier .env (sans écraser celles déjà définies dans le terminal)
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            if _v.strip():
                os.environ.setdefault(_k.strip(), _v.strip())
DATA = ROOT / "data"
TYPES = ["invoice", "supplier_sheet", "manual", "irrelevant"]


# ------------------------------------------------------------------ normalisation
def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def fuzzy(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def to_num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,\-]", "", str(v))
    if not s:
        return None
    lc, ld = s.rfind(","), s.rfind(".")
    if lc > -1 and ld > -1:
        s = s.replace(".", "").replace(",", ".") if lc > ld else s.replace(",", "")
    elif lc > -1:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) and s.count(",") == 1 else s.replace(",", "")
    elif ld > -1 and (s.count(".") > 1 or re.search(r"\.\d{3}$", s)):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_iso(v):
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
        if not m:
            return None
        d, mo, y = m.groups()
        y = "20" + y if len(y) == 2 else y
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def num_ok(a, b):
    a, b = to_num(a), to_num(b)
    return a is not None and b is not None and abs(a - b) <= max(0.02, abs(b) * 0.01)


def list_f1(pred, gold, thr=0.7):
    pred = [p for p in (pred or []) if p]
    gold = [g for g in (gold or []) if g]
    if not gold:
        return None
    if not pred:
        return 0.0
    tp = sum(1 for g in gold if any(fuzzy(g, p) >= thr for p in pred))
    prec = sum(1 for p in pred if any(fuzzy(g, p) >= thr for g in gold)) / len(pred)
    rec = tp / len(gold)
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


# ------------------------------------------------------------------ comparaison des champs
def compare(true_type, gt, x):
    """Retourne {champ: bool|float} pour les champs présents dans le ground truth."""
    x = x or {}
    r = {}
    if true_type == "invoice":
        vendor_gt = gt.get("vendor") or gt.get("company")
        if vendor_gt:
            r["vendor"] = fuzzy(vendor_gt, x.get("vendor")) >= 0.8
        if gt.get("invoice_number"):
            r["invoice_number"] = norm(gt["invoice_number"]).replace(" ", "") == norm(x.get("invoice_number")).replace(" ", "")
        if gt.get("date"):
            r["date"] = to_iso(gt["date"]) == to_iso(x.get("date"))
        if gt.get("total") is not None:
            r["total"] = num_ok(x.get("total"), gt["total"])
        if gt.get("currency") and gt.get("source") != "cord":
            r["currency"] = norm(gt["currency"]) == norm(x.get("currency"))
        if gt.get("subtotal") is not None:
            r["subtotal"] = num_ok(x.get("subtotal"), gt["subtotal"])
        if gt.get("tax") is not None:
            r["tax"] = num_ok(x.get("tax"), gt["tax"])
        if gt.get("line_items"):
            r["line_items_count"] = len(x.get("line_items") or []) == len(gt["line_items"])
    elif true_type == "supplier_sheet":
        c_gt, c = gt.get("contact") or {}, x.get("contact") or {}
        if not isinstance(c, dict):
            c = {"name": c}
        r["company_name"] = fuzzy(gt.get("company_name"), x.get("company_name")) >= 0.85
        r["contact_name"] = fuzzy(c_gt.get("name"), c.get("name")) >= 0.85
        r["contact_email"] = norm(c_gt.get("email")) == norm(c.get("email"))
        r["address"] = fuzzy(gt.get("address"), x.get("address")) >= 0.7
        r["products_offered_f1"] = list_f1(x.get("products_offered"), gt.get("products_offered"))
    elif true_type == "manual":
        r["title"] = fuzzy(gt.get("title"), x.get("title")) >= 0.8
        r["equipment_reference"] = norm(gt.get("equipment_reference")) == norm(x.get("equipment_reference"))
        r["page_count"] = to_num(x.get("page_count")) == to_num(gt.get("page_count"))
    return r


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dsn", default=os.getenv("EXTRACTION_DSN"), help="chaîne de connexion Supabase / Postgres")
    args = ap.parse_args()
    if not args.dsn:
        ap.error("indiquez --dsn ou la variable EXTRACTION_DSN (chaîne de connexion Supabase)")

    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    docs = {d["doc_id"]: d for d in manifest["documents"]}
    by_file = {Path(d["path"]).name: d for d in manifest["documents"]}
    run_file = DATA / "sent_runs" / f"{args.run_id}.json"
    if run_file.exists():
        cases = json.loads(run_file.read_text(encoding="utf-8"))["cases"]
    else:  # ex. exécution headless : on suppose les emails mixtes
        cases = [dict(case_id=e["id"], message_id=None, subject=e["subject"], attachments=e["attachments"],
                      expected_filter=e["expected_filter"]) for e in manifest["mixed_emails"]]

    with psycopg.connect(args.dsn, row_factory=dict_row) as con:
        emails = con.execute("SELECT * FROM email_log WHERE test_run_id = %s", (args.run_id,)).fetchall()
        procs = con.execute("SELECT * FROM processing_log WHERE test_run_id = %s ORDER BY id", (args.run_id,)).fetchall()
        # repli si les en-têtes X-Test-* n'ont pas été transmis par le serveur IMAP
        mids = [c["message_id"].strip("<>") for c in cases if c.get("message_id")]
        if not emails and not procs and mids:
            like = [f"%{m}%" for m in mids]
            emails = con.execute("SELECT * FROM email_log WHERE email_id LIKE ANY(%s)", (like,)).fetchall()
            procs = con.execute("SELECT * FROM processing_log WHERE email_id LIKE ANY(%s) ORDER BY id", (like,)).fetchall()
            mid2case = {c["message_id"].strip("<>"): c["case_id"] for c in cases if c.get("message_id")}
            for row in emails + procs:
                row["test_case_id"] = next((cid for m, cid in mid2case.items() if m in (row["email_id"] or "")), None)

    skipped = {e["test_case_id"] for e in emails}
    procs_by_case = defaultdict(list)
    for p in procs:
        procs_by_case[p["test_case_id"]].append(p)

    lines = [f"# Évaluation — {args.run_id}", ""]
    # ---- Step 2 / 3 : filtre + split
    filt_ok = split_ok = 0
    lines += ["## Emails (filtre + éclatement des pièces jointes)", "",
              "| cas | attendu | obtenu | PJ attendues | PJ traitées | OK |", "|---|---|---|---|---|---|"]
    for c in cases:
        got = "skip" if c["case_id"] in skipped else ("process" if procs_by_case.get(c["case_id"]) else "absent")
        n_exp = len(c["attachments"]) if c["expected_filter"] == "process" else 0
        n_got = len(procs_by_case.get(c["case_id"], []))
        f_ok, s_ok = got == c["expected_filter"], n_got == n_exp
        filt_ok += f_ok
        split_ok += s_ok
        lines.append(f"| {c['case_id']} | {c['expected_filter']} | {got} | {n_exp} | {n_got} | {'✅' if f_ok and s_ok else '❌'} |")
    lines += ["", f"**Filtre : {filt_ok}/{len(cases)}** — **Éclatement : {split_ok}/{len(cases)}**", ""]

    # ---- Step 5 / 6 / 7-9 : par pièce jointe
    confusion = Counter()
    field_scores = defaultdict(list)
    status = Counter()
    rows = []
    expected_att = 0
    for c in cases:
        if c["expected_filter"] != "process":
            continue
        expected_att += len(c["attachments"])
        for p in procs_by_case.get(c["case_id"], []):
            d = by_file.get(p["file_name"])
            if not d:
                continue
            true_t, pred_t = d["true_type"], p["detected_type"]
            confusion[(true_t, pred_t)] += 1
            status[p["final_status"]] += 1
            gt = dict(d["ground_truth"], source=d["source"])
            cmp = compare(true_t, gt, p["extracted"]) if pred_t == true_t else {}
            for k, v in cmp.items():
                if v is not None:
                    field_scores[(true_t, k)].append(float(v))
            exp_val = d.get("expected_validation")
            rows.append(f"| {c['case_id']} | {p['file_name']} | {d['source']} | {true_t} | {pred_t} "
                        f"| {p['text_source']} | {p['confidence']} | {p['final_status']}"
                        f"{' (attendu : revue)' if exp_val == 'fail' else ''} "
                        f"| {sum(1 for v in cmp.values() if v)}/{len(cmp) if cmp else '-'} |")
    n_att = sum(confusion.values())
    correct = sum(v for (t, p), v in confusion.items() if t == p)
    lines += ["## Pièces jointes", "",
              "| cas | fichier | source | vrai type | prédit | texte | confiance | statut | champs OK |",
              "|---|---|---|---|---|---|---|---|---|", *rows, "",
              f"**Couverture : {n_att}/{expected_att} pièces jointes journalisées** — "
              f"**Classification : {correct}/{n_att} ({(correct / n_att * 100 if n_att else 0):.1f} %)**", ""]

    labels = TYPES + sorted({p for (_, p) in confusion} - set(TYPES))
    lines += ["### Matrice de confusion (lignes = vrai, colonnes = prédit)", "",
              "| | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for t in TYPES:
        lines.append(f"| **{t}** | " + " | ".join(str(confusion[(t, p)]) for p in labels) + " |")

    lines += ["", "### Exactitude des champs extraits (documents bien classés)", "", "| type | champ | score | n |", "|---|---|---|---|"]
    for (t, k), v in sorted(field_scores.items()):
        lines.append(f"| {t} | {k} | {sum(v) / len(v) * 100:.0f} % | {len(v)} |")
    lines += ["", "### Décisions (Steps 8-9)", ""] + [f"- {k} : {v}" for k, v in status.most_common()]

    report = "\n".join(lines)
    print(report)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"{args.run_id}.md").write_text(report, encoding="utf-8")
    print(f"\n-> reports/{args.run_id}.md")


if __name__ == "__main__":
    main()
