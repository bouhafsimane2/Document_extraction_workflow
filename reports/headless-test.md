# Évaluation — headless-test

## Emails (filtre + éclatement des pièces jointes)

| cas | attendu | obtenu | PJ attendues | PJ traitées | OK |
|---|---|---|---|---|---|
| MIX-01 | skip | skip | 0 | 0 | ✅ |
| MIX-02 | skip | skip | 0 | 0 | ✅ |
| MIX-03 | process | process | 1 | 1 | ✅ |
| MIX-04 | process | process | 2 | 2 | ✅ |
| MIX-05 | process | process | 3 | 3 | ✅ |
| MIX-06 | process | process | 2 | 2 | ✅ |
| MIX-07 | skip | skip | 0 | 0 | ✅ |
| MIX-08 | process | process | 1 | 1 | ✅ |
| MIX-09 | process | process | 3 | 3 | ✅ |
| MIX-10 | process | process | 4 | 4 | ✅ |

**Filtre : 10/10** — **Éclatement : 10/10**

## Pièces jointes

| cas | fichier | source | vrai type | prédit | texte | confiance | statut | champs OK |
|---|---|---|---|---|---|---|---|---|
| MIX-03 | inv_01.pdf | synthetic | invoice | invoice | pdf_text_layer | 1.000 | auto_approved | 8/8 |
| MIX-04 | inv_02.pdf | synthetic | invoice | invoice | pdf_text_layer | 1.000 | auto_approved | 8/8 |
| MIX-04 | irr_01.pdf | synthetic | irrelevant | unknown | pdf_text_layer | 0.000 | human_approved | 0/- |
| MIX-05 | inv_04.pdf | synthetic | invoice | invoice | pdf_text_layer | 1.000 | auto_approved | 8/8 |
| MIX-05 | sroie_000.jpg | sroie | invoice | invoice | ocr:docling | 1.000 | auto_approved | 3/3 |
| MIX-05 | cord_000.jpg | cord | invoice | invoice | ocr:docling | 0.429 | human_approved | 2/4 |
| MIX-06 | sup_01.pdf | synthetic | supplier_sheet | supplier_sheet | pdf_text_layer | 1.000 | auto_approved | 5/5 |
| MIX-06 | man_01.pdf | synthetic | manual | manual | pdf_text_layer | 1.000 | auto_approved | 3/3 |
| MIX-08 | inv_03_scanned.pdf | synthetic | invoice | invoice | ocr:docling | 1.000 | auto_approved | 8/8 |
| MIX-09 | irr_03.pdf | synthetic | irrelevant | irrelevant | pdf_text_layer | None | discarded_irrelevant | 0/- |
| MIX-09 | cord_001.jpg | cord | invoice | invoice | ocr:docling | 0.167 | human_approved | 0/2 |
| MIX-09 | sroie_001.jpg | sroie | invoice | invoice | ocr:docling | 0.857 | human_approved | 3/3 |
| MIX-10 | inv_05.pdf | synthetic | invoice | invoice | pdf_text_layer | 0.857 | human_approved (attendu : revue) | 8/8 |
| MIX-10 | sup_03.pdf | synthetic | supplier_sheet | supplier_sheet | pdf_text_layer | 1.000 | auto_approved | 5/5 |
| MIX-10 | man_05.pdf | synthetic | manual | manual | pdf_text_layer | 1.000 | auto_approved | 3/3 |
| MIX-10 | irr_04.pdf | synthetic | irrelevant | irrelevant | pdf_text_layer | None | discarded_irrelevant | 0/- |

**Couverture : 16/16 pièces jointes journalisées** — **Classification : 15/16 (93.8 %)**

### Matrice de confusion (lignes = vrai, colonnes = prédit)

| | invoice | supplier_sheet | manual | irrelevant | unknown |
|---|---|---|---|---|---|
| **invoice** | 9 | 0 | 0 | 0 | 0 |
| **supplier_sheet** | 0 | 2 | 0 | 0 | 0 |
| **manual** | 0 | 0 | 2 | 0 | 0 |
| **irrelevant** | 0 | 0 | 0 | 2 | 1 |

### Exactitude des champs extraits (documents bien classés)

| type | champ | score | n |
|---|---|---|---|
| invoice | currency | 100 % | 5 |
| invoice | date | 100 % | 7 |
| invoice | invoice_number | 100 % | 5 |
| invoice | line_items_count | 71 % | 7 |
| invoice | subtotal | 100 % | 6 |
| invoice | tax | 83 % | 6 |
| invoice | total | 89 % | 9 |
| invoice | vendor | 100 % | 7 |
| manual | equipment_reference | 100 % | 2 |
| manual | page_count | 100 % | 2 |
| manual | title | 100 % | 2 |
| supplier_sheet | address | 100 % | 2 |
| supplier_sheet | company_name | 100 % | 2 |
| supplier_sheet | contact_email | 100 % | 2 |
| supplier_sheet | contact_name | 100 % | 2 |
| supplier_sheet | products_offered_f1 | 100 % | 2 |

### Décisions (Steps 8-9)

- auto_approved : 9
- human_approved : 5
- discarded_irrelevant : 2