"""
Génère la partie synthétique du jeu de test + les emails mixtes + manifest.json.

    python scripts/download_public_datasets.py   # (optionnel) SROIE + CORD
    python scripts/generate_dataset.py

Sortie : data/synthetic/{invoices,supplier_sheets,manuals,irrelevant}/, data/mixed_emails/*.eml,
data/manifest.json. Les reçus SROIE/CORD présents dans data/sroie et data/cord sont intégrés au manifest.
"""
import json
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import pymupdf
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SYN = DATA / "synthetic"
STYLES = getSampleStyleSheet()
H1, H2, P = STYLES["Title"], STYLES["Heading2"], STYLES["BodyText"]


def money(v, cur):
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} {cur}"


def build_pdf(path, story):
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm).build(story)
    return pymupdf.open(str(path)).page_count


def table(rows, widths=None, header=True):
    t = Table(rows, colWidths=widths)
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde4ee")),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def rasterize(pdf_path, dpi=110):
    """Transforme un PDF texte en PDF 'scanné' (image seule, pas de couche texte)."""
    src = pymupdf.open(str(pdf_path))
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        p = out.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, stream=pix.tobytes("png"))
    src.close()
    out.save(str(pdf_path))
    out.close()


# ---------------------------------------------------------------- factures
INVOICES = [
    dict(id="inv_01", vendor="Métallerie Ferroviaire du Centre SARL", number="FAC-2026-0412",
         date="2026-03-14", currency="DZD", tax_rate=0.19,
         address="Zone industrielle Oued Smar, lot 27, 16270 Alger",
         items=[("Plaquettes de frein bogie type BF-220", 24, 8450.00),
                ("Boulons HR M20 classe 10.9 (lot de 50)", 6, 3120.00),
                ("Main d'oeuvre usinage support", 12, 2500.00)]),
    dict(id="inv_02", vendor="Électro-Traction Services SAS", number="ETS-INV-88213",
         date="2026-04-02", currency="EUR", tax_rate=0.20,
         address="14 rue des Ateliers, 69007 Lyon, France",
         items=[("Contacteur de traction CT-400", 2, 1240.00),
                ("Carte électronique convertisseur auxiliaire", 1, 3890.50),
                ("Forfait diagnostic sur site", 1, 650.00)]),
    dict(id="inv_03", vendor="Atlas Hydraulique", number="AH/26/00157",
         date="2026-02-21", currency="DZD", tax_rate=0.19, scanned=True,
         address="Route de Sidi Bel Abbès, 31000 Oran",
         items=[("Vérin hydraulique porte passagers", 4, 18700.00),
                ("Kit joints d'étanchéité VH-12", 10, 950.00)]),
    dict(id="inv_04", vendor="NordSignal GmbH", number="NS-2026-3301",
         date="2026-05-11", currency="EUR", tax_rate=0.0,
         address="Industriestraße 8, 30159 Hannover, Deutschland",
         note="Autoliquidation — TVA due par le preneur (art. 196 directive 2006/112/CE)",
         items=[("Balise de signalisation SB-9", 3, 2150.00),
                ("Câble blindé 4x1,5 mm² (100 m)", 2, 480.00)]),
    # Facture volontairement incohérente : total ≠ somme des lignes -> doit partir en revue humaine
    dict(id="inv_05", vendor="Soudure & Rail Maghreb", number="SRM-0098",
         date="2026-06-30", currency="DZD", tax_rate=0.19, wrong_total_delta=15000.00,
         address="12 boulevard de l'ALN, 25000 Constantine",
         items=[("Soudure aluminothermique rail 55E1", 8, 12500.00),
                ("Meulage et contrôle ultrason", 8, 3200.00)]),
]


def make_invoice(inv):
    cur = inv["currency"]
    lines = [dict(description=d, quantity=q, unit_price=u, amount=round(q * u, 2)) for d, q, u in inv["items"]]
    subtotal = round(sum(li["amount"] for li in lines), 2)
    tax = round(subtotal * inv["tax_rate"], 2)
    total = round(subtotal + tax, 2)
    printed_total = round(total + inv.get("wrong_total_delta", 0.0), 2)
    d = inv["date"].split("-")
    story = [
        Paragraph(inv["vendor"], H1),
        Paragraph(inv["address"], P),
        Spacer(1, 6 * mm),
        Paragraph(f"<b>FACTURE N° {inv['number']}</b>", H2),
        Paragraph(f"Date de facturation : {d[2]}/{d[1]}/{d[0]}", P),
        Paragraph("Client : SETRAM — Direction Maintenance, Dépôt de Kouba, Alger", P),
        Paragraph("Conditions de paiement : 30 jours fin de mois", P),
        Spacer(1, 5 * mm),
        table([["Désignation", "Qté", "Prix unitaire", "Montant HT"]] +
              [[li["description"], str(li["quantity"]), money(li["unit_price"], cur), money(li["amount"], cur)]
               for li in lines], widths=[88 * mm, 15 * mm, 35 * mm, 35 * mm]),
        Spacer(1, 4 * mm),
        table([["Total HT", money(subtotal, cur)],
               [f"TVA {int(inv['tax_rate'] * 100)} %", money(tax, cur)],
               ["TOTAL TTC", money(printed_total, cur)]], widths=[40 * mm, 40 * mm], header=False),
    ]
    if inv.get("note"):
        story += [Spacer(1, 4 * mm), Paragraph(inv["note"], P)]
    story += [Spacer(1, 8 * mm), Paragraph("RIB : BNA 00100 0123 4567890123 45 — Merci de rappeler le numéro de facture.", P)]
    path = SYN / "invoices" / f"{inv['id']}{'_scanned' if inv.get('scanned') else ''}.pdf"
    build_pdf(path, story)
    if inv.get("scanned"):
        rasterize(path)
    gt = dict(vendor=inv["vendor"], invoice_number=inv["number"], date=inv["date"], line_items=lines,
              subtotal=subtotal, tax=tax, total=printed_total, currency=cur)
    return path, gt, dict(scanned=bool(inv.get("scanned")),
                          expected_validation="fail" if "wrong_total_delta" in inv else "pass")


# ---------------------------------------------------------------- fiches fournisseurs
SUPPLIERS = [
    dict(id="sup_01", company_name="Tramelec Composants SPA",
         contact=dict(name="Samira Benali", role="Responsable commerciale", email="s.benali@tramelec-dz.test",
                      phone="+213 21 55 42 10"),
         address="Cité des 200 logements, bât. C, 16000 Alger",
         products=["Pantographes et bandes de frottement", "Disjoncteurs haute tension",
                   "Faisceaux de câblage sur mesure"]),
    dict(id="sup_02", company_name="Roulements & Transmission Europe",
         contact=dict(name="Marc Delaunay", role="Key account manager", email="m.delaunay@rte-bearings.test",
                      phone="+33 4 72 10 88 90"),
         address="5 allée de l'Industrie, 38070 Saint-Quentin-Fallavier, France",
         products=["Roulements d'essieu", "Réducteurs de bogie", "Accouplements élastiques"]),
    dict(id="sup_03", company_name="Clim'Rail Solutions",
         contact=dict(name="Yacine Hamidi", role="Directeur technique", email="y.hamidi@climrail.test",
                      phone="+213 41 33 20 07"),
         address="Parc d'activités Es-Senia, 31100 Oran",
         products=["Unités de climatisation embarquées", "Filtres à air cabine", "Contrats de maintenance CVC"]),
    dict(id="sup_04", company_name="VitroSafe Industrie",
         contact=dict(name="Claire Roussel", role="Chargée d'affaires", email="c.roussel@vitrosafe.test",
                      phone="+33 3 88 41 27 63"),
         address="22 rue du Verre, 67000 Strasbourg, France",
         products=["Pare-brise feuilletés tramway", "Vitrages latéraux trempés", "Films anti-graffiti"]),
    dict(id="sup_05", company_name="SignalTech Maghreb SARL",
         contact=dict(name="Karim Ould-Ali", role="Gérant", email="contact@signaltech-mgh.test",
                      phone="+213 31 92 15 48"),
         address="Zone d'activité Palma, 25000 Constantine",
         products=["Systèmes d'aide à l'exploitation (SAE)", "Afficheurs voyageurs", "Radio sol-train TETRA"]),
]


def make_supplier(s):
    c = s["contact"]
    story = [
        Paragraph("FICHE DE RÉFÉRENCEMENT FOURNISSEUR", H2),
        Paragraph(s["company_name"], H1),
        Spacer(1, 4 * mm),
        table([["Raison sociale", s["company_name"]],
               ["Adresse du siège", s["address"]],
               ["Interlocuteur", f"{c['name']} — {c['role']}"],
               ["Email", c["email"]],
               ["Téléphone", c["phone"]],
               ["NIF / Registre", "000123456789012 / 16B0987654"]], widths=[45 * mm, 125 * mm], header=False),
        Spacer(1, 5 * mm),
        Paragraph("<b>Produits et services proposés</b>", H2),
    ] + [Paragraph(f"• {p}", P) for p in s["products"]] + [
        Spacer(1, 5 * mm),
        Paragraph("Certifications : ISO 9001:2015, IRIS / ISO/TS 22163. Délai moyen de livraison : 4 à 6 semaines.", P),
    ]
    path = SYN / "supplier_sheets" / f"{s['id']}.pdf"
    build_pdf(path, story)
    gt = dict(company_name=s["company_name"], contact=c, address=s["address"], products_offered=s["products"])
    return path, gt, {}


# ---------------------------------------------------------------- manuels techniques
MANUALS = [
    dict(id="man_01", title="Manuel de maintenance — Pantographe PX-300", ref="PX-300", pages=2,
         sections=["Consignation électrique", "Contrôle des bandes de frottement", "Réglage de l'effort statique"]),
    dict(id="man_02", title="Notice technique — Système de freinage électro-hydraulique EHB-12", ref="EHB-12", pages=1,
         sections=["Principe de fonctionnement", "Purge du circuit", "Codes défaut"]),
    dict(id="man_03", title="Procédure d'entretien — Porte louvoyante-coulissante PLC-4", ref="PLC-4", pages=2,
         sections=["Graissage des guides", "Réglage des butées", "Test de l'anti-pincement"]),
    dict(id="man_04", title="Guide de dépannage — Convertisseur auxiliaire CVS-75", ref="CVS-75", pages=1,
         sections=["Voyants et diagnostics", "Remplacement des ventilateurs", "Mesures d'isolement"]),
    dict(id="man_05", title="Manuel utilisateur — Climatiseur de cabine HVAC-TR20", ref="HVAC-TR20", pages=2,
         sections=["Mise en service", "Nettoyage des filtres", "Recharge en fluide R134a"]),
]

LOREM = ("Avant toute intervention, vérifier que la rame est consignée et que les équipements sont hors tension. "
         "Utiliser les équipements de protection individuelle adaptés. Les couples de serrage sont donnés dans "
         "le tableau ci-dessous et doivent être respectés à ±5 %. Toute anomalie doit être tracée dans la GMAO.")


def make_manual(m):
    story = [Paragraph(m["title"], H1),
             Paragraph(f"Référence équipement : <b>{m['ref']}</b> — Révision C — Diffusion interne", P),
             Spacer(1, 5 * mm)]
    per_page = max(1, len(m["sections"]) // m["pages"])
    for i, sec in enumerate(m["sections"], 1):
        story += [Paragraph(f"{i}. {sec}", H2), Paragraph(LOREM, P), Spacer(1, 3 * mm),
                  table([["Repère", "Élément", "Couple (N·m)"],
                         [f"{m['ref']}-{i}01", "Vis de fixation", str(20 + 5 * i)],
                         [f"{m['ref']}-{i}02", "Écrou de blocage", str(35 + 5 * i)]],
                        widths=[35 * mm, 80 * mm, 35 * mm])]
        if m["pages"] > 1 and i == per_page:
            story.append(PageBreak())
    story += [Spacer(1, 4 * mm), Paragraph(f"Document {m['ref']} — {m['pages']} page(s).", P)]
    path = SYN / "manuals" / f"{m['id']}.pdf"
    real_pages = build_pdf(path, story)
    gt = dict(title=m["title"], equipment_reference=m["ref"], page_count=real_pages)
    return path, gt, {}


# ---------------------------------------------------------------- documents non pertinents
IRRELEVANT = [
    dict(id="irr_01", kind="flyer_marketing", title="MEGA SOLDES D'ÉTÉ — jusqu'à -50 %",
         body=["Profitez de nos offres exceptionnelles sur toute la gamme de smartphones et tablettes !",
               "Livraison gratuite dès 5 000 DA d'achat. Offre valable du 1er au 31 juillet.",
               "Scannez le QR code en magasin pour gagner un bon d'achat."]),
    dict(id="irr_02", kind="newsletter", title="La Lettre Mobilité Urbaine — n° 42",
         body=["Au sommaire : les villes qui misent sur le vélo, interview d'une urbaniste, agenda des salons.",
               "Le saviez-vous ? Le premier tramway électrique a circulé en 1881 près de Berlin.",
               "Pour vous désabonner, cliquez sur le lien en bas de ce message."]),
    dict(id="irr_03", kind="message_personnel", title="Coucou !",
         body=["Salut Nadia, voici les photos du week-end à Tipaza, c'était vraiment génial.",
               "On remet ça en septembre ? Bisous à toute la famille.",
               "PS : n'oublie pas l'anniversaire de maman samedi."]),
    dict(id="irr_04", kind="invitation", title="Invitation — Soirée de fin d'année du club de foot",
         body=["Le comité du club a le plaisir de vous convier à la soirée annuelle le 20 décembre à 19h.",
               "Au programme : remise des trophées, dîner et animation musicale.",
               "Merci de confirmer votre présence avant le 10 décembre."]),
    dict(id="irr_05", kind="article_presse", title="Tribune : le télétravail, et après ?",
         body=["Trois ans après la généralisation du travail hybride, les entreprises s'interrogent.",
               "Entre productivité, bien-être et coûts immobiliers, le débat reste ouvert.",
               "Retrouvez l'intégralité de notre dossier dans le magazine du mois."]),
]


def make_irrelevant(d):
    story = [Paragraph(d["title"], H1), Spacer(1, 4 * mm)] + [Paragraph(t, P) for t in d["body"]]
    path = SYN / "irrelevant" / f"{d['id']}.pdf"
    build_pdf(path, story)
    return path, dict(kind=d["kind"]), {}


# ---------------------------------------------------------------- manifest
def rel(p):
    return Path(p).relative_to(DATA).as_posix()


def collect_public(folder, source):
    docs = []
    for img in sorted((DATA / folder).glob(f"{folder}_*.jpg")):
        gt_file = img.with_suffix(".json")
        if not gt_file.exists():
            continue
        gt = json.loads(gt_file.read_text(encoding="utf-8"))
        gt.pop("raw_gt_parse", None)
        docs.append(dict(doc_id=img.stem, path=rel(img), true_type="invoice", source=source,
                         ground_truth=gt, scanned=True))
    return docs


def main():
    docs = []
    for maker, items, typ in [(make_invoice, INVOICES, "invoice"), (make_supplier, SUPPLIERS, "supplier_sheet"),
                              (make_manual, MANUALS, "manual"), (make_irrelevant, IRRELEVANT, "irrelevant")]:
        for it in items:
            path, gt, extra = maker(it)
            docs.append(dict(doc_id=it["id"], path=rel(path), true_type=typ, source="synthetic",
                             ground_truth=gt, **extra))
            (path.parent / f"{it['id']}.ground_truth.json").write_text(
                json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    sroie, cord = collect_public("sroie", "sroie"), collect_public("cord", "cord")
    docs += sroie + cord
    if not sroie or not cord:
        print("⚠ SROIE/CORD absents : lancez scripts/download_public_datasets.py (emails concernés réduits).")
    by_id = {d["doc_id"]: d for d in docs}

    def pick(*ids):
        return [i for i in ids if i in by_id]

    SUP = "Fournisseurs <achats-fournisseurs@test.local>"
    emails = [
        dict(id="MIX-01", sender="Nadia B. <nadia.perso@test.local>", subject="Déjeuner vendredi ?",
             body="Salut, on se retrouve vendredi midi à la cantine ? Dis-moi.", attachments=[],
             expected_filter="skip", scenario="sans pièce jointe, personnel"),
        dict(id="MIX-02", sender="Compta Métallerie <compta@metallerie-centre.test>",
             subject="Relance : facture FAC-2026-0412 impayée",
             body="Bonjour, sauf erreur de notre part la facture FAC-2026-0412 reste impayée. Cordialement.",
             attachments=[], expected_filter="skip", scenario="objet métier mais aucune pièce jointe"),
        dict(id="MIX-03", sender="Métallerie Ferroviaire <facturation@metallerie-centre.test>",
             subject="Facture FAC-2026-0412 — commande 4500123",
             body="Bonjour, veuillez trouver ci-joint notre facture. Bonne réception.",
             attachments=pick("inv_01"), expected_filter="process", scenario="1 facture"),
        dict(id="MIX-04", sender="Électro-Traction Services <billing@ets-traction.test>",
             subject="Votre facture ETS-INV-88213 + nos promotions",
             body="Bonjour, ci-joint la facture de l'intervention ainsi que notre flyer de l'été.",
             attachments=pick("inv_02", "irr_01"), expected_filter="process",
             scenario="1 facture pertinente + 1 flyer non pertinent"),
        dict(id="MIX-05", sender="Service Achats <achats@setram.test>",
             subject="Lot de factures fournisseurs à saisir (3)",
             body="Merci de saisir les trois factures jointes avant la clôture mensuelle.",
             attachments=pick("inv_04", "sroie_000", "cord_000"), expected_filter="process",
             scenario="plusieurs factures valides (PDF + 2 photos de reçus)"),
        dict(id="MIX-06", sender=SUP, subject="Documentation fournisseur Tramelec + manuel pantographe",
             body="Bonjour, voici notre fiche de référencement et le manuel de maintenance du PX-300.",
             attachments=pick("sup_01", "man_01"), expected_filter="process",
             scenario="fiche fournisseur + manuel"),
        dict(id="MIX-07", sender="La Lettre Mobilité <newsletter@mobilite-news.test>",
             subject="📰 Votre newsletter mensuelle — n° 42",
             body="Découvrez notre sélection d'articles du mois. Se désabonner : lien en bas de page.",
             attachments=pick("irr_02"), expected_filter="skip", scenario="newsletter avec PDF joint"),
        dict(id="MIX-08", sender="Atlas Hydraulique <contact@atlas-hydraulique.test>",
             subject="Facture AH/26/00157 (scan)",
             body="Veuillez trouver ci-joint le scan de notre facture.",
             attachments=pick("inv_03"), expected_filter="process", scenario="facture scannée → OCR"),
        dict(id="MIX-09", sender="Caisse dépôt <caisse@setram.test>",
             subject="Tickets de caisse — notes de frais équipe maintenance",
             body="Photos des tickets pour remboursement.",
             attachments=pick("cord_001", "sroie_001", "irr_03"), expected_filter="process",
             scenario="2 photos de reçus + 1 message personnel égaré"),
        dict(id="MIX-10", sender=SUP, subject="Mise à jour catalogue Clim'Rail + notice HVAC + facture SRM",
             body="Bonjour, en pièces jointes : fiche fournisseur, manuel HVAC-TR20, invitation et facture.",
             attachments=pick("sup_03", "man_05", "irr_04", "inv_05"), expected_filter="process",
             scenario="4 pièces jointes mélangées dont 1 facture incohérente (→ revue humaine)"),
    ]
    for e in emails:
        e["expected_attachments"] = [dict(doc_id=a, file_name=Path(by_id[a]["path"]).name,
                                          true_type=by_id[a]["true_type"]) for a in e["attachments"]]

    # .eml pour inspection / import manuel dans un client mail
    out = DATA / "mixed_emails"
    out.mkdir(parents=True, exist_ok=True)
    for e in emails:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = e["sender"], "inbox@test.local", e["subject"]
        msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(idstring=e["id"], domain="n8n-test.local")
        msg["X-Test-Case-Id"] = e["id"]
        msg.set_content(e["body"])
        for a in e["attachments"]:
            p = DATA / by_id[a]["path"]
            maintype, subtype = ("application", "pdf") if p.suffix == ".pdf" else ("image", "jpeg")
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        (out / f"{e['id']}.eml").write_bytes(bytes(msg))

    manifest = dict(version=1, documents=docs, mixed_emails=emails)
    (DATA / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {}
    for d in docs:
        counts[(d["source"], d["true_type"])] = counts.get((d["source"], d["true_type"]), 0) + 1
    print(f"{len(docs)} documents, {len(emails)} emails mixtes -> data/manifest.json")
    for k, v in sorted(counts.items()):
        print(f"  {k[0]:9} {k[1]:15} {v}")


if __name__ == "__main__":
    main()
