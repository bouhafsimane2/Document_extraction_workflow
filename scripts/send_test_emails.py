"""
Envoie le jeu de test dans la boîte surveillée par le nœud Email Trigger (IMAP) de n8n.

Modes :
  mixed      -> les 10 emails mixtes (data/manifest.json > mixed_emails)          [Step 12]
  documents  -> 1 email par document (40) : mesure classification / extraction sur tout le jeu
  all        -> les deux

Exemples :
  # GreenMail local (docker compose up -d) : SMTP localhost:3025 sans auth
  python scripts/send_test_emails.py --mode mixed

  # Vraie boîte (ex. Gmail + mot de passe d'application)
  set SMTP_PASSWORD=xxxx
  python scripts/send_test_emails.py --mode mixed --host smtp.gmail.com --port 587 --starttls ^
         --user moncompte.test@gmail.com --to moncompte.test@gmail.com

Chaque email porte les en-têtes X-Test-Case-Id / X-Test-Run-Id (repris par n8n dans processing_log)
et le run est tracé dans data/sent_runs/<run_id>.json pour l'évaluation (scripts/evaluate.py).
"""
import argparse
import json
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

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


def build_cases(manifest, mode):
    docs = {d["doc_id"]: d for d in manifest["documents"]}
    cases = []
    if mode in ("mixed", "all"):
        for e in manifest["mixed_emails"]:
            cases.append(dict(case_id=e["id"], sender=e["sender"], subject=e["subject"], body=e["body"],
                              attachments=e["attachments"], expected_filter=e["expected_filter"]))
    if mode in ("documents", "all"):
        for d in manifest["documents"]:
            name = Path(d["path"]).name
            cases.append(dict(case_id=f"DOC-{d['doc_id']}", sender="Documents entrants <documents@fournisseur-test.local>",
                              subject=f"Document à traiter : {name}",
                              body="Bonjour,\nVeuillez trouver le document en pièce jointe.\nCordialement.",
                              attachments=[d["doc_id"]], expected_filter="process"))
    return cases, docs


def to_message(case, docs, to_addr, run_id):
    msg = EmailMessage()
    msg["From"] = case["sender"]
    msg["To"] = to_addr
    msg["Subject"] = case["subject"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(idstring=f"{case['case_id']}.{run_id}", domain="n8n-test.local")
    msg["X-Test-Case-Id"] = case["case_id"]
    msg["X-Test-Run-Id"] = run_id
    msg.set_content(case["body"])
    for doc_id in case["attachments"]:
        p = DATA / docs[doc_id]["path"]
        maintype, subtype = ("application", "pdf") if p.suffix.lower() == ".pdf" else ("image", "jpeg")
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["mixed", "documents", "all"], default="mixed")
    ap.add_argument("--host", default=os.getenv("SMTP_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.getenv("SMTP_PORT", "3025")))
    ap.add_argument("--user", default=os.getenv("SMTP_USER"))
    ap.add_argument("--password", default=os.getenv("SMTP_PASSWORD"))
    ap.add_argument("--starttls", action="store_true")
    ap.add_argument("--ssl", action="store_true", help="SMTP over SSL (port 465)")
    ap.add_argument("--to", default=os.getenv("TEST_INBOX", "inbox@test.local"))
    ap.add_argument("--delay", type=float, default=3.0, help="secondes entre deux emails (limites LLM)")
    ap.add_argument("--only", help="liste d'ids séparés par des virgules (ex. MIX-04,MIX-10)")
    ap.add_argument("--run-id", default=datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--dry-run", action="store_true", help="écrit les .eml dans data/outbox/ sans envoyer")
    args = ap.parse_args()

    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    cases, docs = build_cases(manifest, args.mode)
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["case_id"] in keep]

    smtp = None
    if not args.dry_run:
        smtp = (smtplib.SMTP_SSL if args.ssl else smtplib.SMTP)(args.host, args.port, timeout=60)
        if args.starttls:
            smtp.starttls()
        if args.user:
            smtp.login(args.user, args.password or "")
    outbox = DATA / "outbox" / args.run_id
    sent = []
    for i, case in enumerate(cases, 1):
        msg = to_message(case, docs, args.to, args.run_id)
        if args.dry_run:
            outbox.mkdir(parents=True, exist_ok=True)
            (outbox / f"{case['case_id']}.eml").write_bytes(bytes(msg))
        else:
            smtp.send_message(msg)
        sent.append(dict(case_id=case["case_id"], message_id=msg["Message-ID"], subject=case["subject"],
                         attachments=case["attachments"], expected_filter=case["expected_filter"]))
        print(f"[{i}/{len(cases)}] {case['case_id']:18} {len(case['attachments'])} PJ  {case['subject'][:60]}")
        if smtp and i < len(cases):
            time.sleep(args.delay)
    if smtp:
        smtp.quit()

    runs = DATA / "sent_runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{args.run_id}.json").write_text(json.dumps(dict(run_id=args.run_id, mode=args.mode, cases=sent),
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nrun_id = {args.run_id}  ->  python scripts/evaluate.py --run-id {args.run_id}")


if __name__ == "__main__":
    main()
