# Workflow n8n : extraction de documents depuis une boîte mail (plusieurs types, boîte mixte)

Ce dossier contient :

| Chemin | Rôle |
|---|---|
| `workflows/document_extraction_workflow.json` | **Workflow principal (Steps 1 à 10)** à coller dans n8n |
| `workflows/error_alert_workflow.json` | **Workflow d'alerte (Step 11)** : Error Trigger, puis log et email |
| `sql/schema.sql` | À exécuter dans Supabase (SQL Editor). Tables `email_log`, `invoices`, `suppliers`, `documents`, `processing_log`, `error_log` |
| `ocr_service/app.py` | Service OCR FastAPI appelé par n8n : **Docling + RapidOCR** (défaut) ou RapidOCR seul |
| `docker-compose.yml` | **Tout le projet en conteneurs** : n8n + service OCR Docling ; GreenMail + Mailpit en option (profil `test-mail`). La base est sur **Supabase** |
| `.env.example` | Modèle du fichier `.env` (clé de chiffrement n8n, connexion Supabase pour les scripts) |
| `scripts/download_public_datasets.py` | Télécharge 10 reçus SROIE et 10 reçus CORD avec leur ground truth |
| `scripts/generate_dataset.py` | Génère 20 documents synthétiques, 10 emails mixtes (`.eml`) et `data/manifest.json` |
| `scripts/send_test_emails.py` | **Envoie le jeu de test par email** dans la boîte surveillée par n8n |
| `scripts/evaluate.py` | Compare les tables Supabase au manifest et produit un rapport de précision/couverture |
| `scripts/build_workflow.py` | Régénère les JSON des workflows (le code JS des nœuds y est lisible) |

---

## 0. Démarrage rapide avec Docker

Prérequis : Docker Desktop démarré, un projet Supabase, une clé Groq.

```bash
cp .env.example .env
```

Dans `.env`, mettez une longue chaîne aléatoire dans `N8N_ENCRYPTION_KEY`, puis :

```bash
docker compose up -d --build
```

- **n8n** s'ouvre sur http://localhost:5678 (ou sur le port `N8N_HOST_PORT` du `.env`). Au premier démarrage, les **deux workflows sont importés automatiquement**, avec le workflow d'erreur déjà relié et `ocr_url` déjà réglé sur le conteneur OCR (`http://ocr:8001/ocr`).
- **Le service OCR Docling** tourne dans le conteneur `ocr` (santé : http://127.0.0.1:8001/health). Le premier document est lent, le temps de télécharger les modèles ; ils sont ensuite conservés dans un volume.

Il reste à faire dans n8n : créer le compte propriétaire, puis les credentials (section 3), puis activer le workflow principal. La base Supabase se prépare comme en section 2.1.

Pour tester sans Gmail, ajoutez la boîte mail de test :

```bash
docker compose --profile test-mail up -d
```

Depuis le conteneur n8n, les credentials utilisent alors les **noms des services** : IMAP host `greenmail`, port `3143` ; SMTP host `mailpit`, port `1025`. Depuis le PC, le script de test envoie toujours sur `localhost:3025`, et Mailpit s'ouvre sur http://localhost:8025.

| Commande | Effet |
|---|---|
| `docker compose logs -f n8n ocr` | Voir les journaux |
| `docker compose down` | Arrêter (les données sont conservées) |
| `docker compose down -v` | Tout supprimer, y compris les workflows et credentials n8n |

Si un n8n tourne déjà sur votre PC sur le port 5678, mettez `N8N_HOST_PORT=5690` et `N8N_PUBLIC_URL=http://localhost:5690/` dans `.env`.

> Sans Docker, suivez les sections 2 et 3 ci-dessous. Dans ce cas, n8n et le service OCR tournent directement sur le PC.

---

## 1. Correspondance entre les étapes et les nœuds

| Step | Nœud(s) n8n |
|---|---|
| 1 Trigger | `Email Trigger (IMAP)` (pièces jointes téléchargées, emails non lus) |
| 2 Filtre | `Config and Normalize` → **`Relevance Filter`** (Text Classifier : relevant / not_relevant) → `IF Has Attachments` |
| 2a Skipped | `Mark Skipped` → **`Supabase: email_log`** |
| 3 Split | `Split Attachments` (Code) : un item par pièce jointe* |
| 4 OCR | `Switch File Kind`, puis `Extract PDF Text`, `Check Text Layer` et `IF Needs OCR` ; `OCR Service` pour les images et les PDF scannés |
| 5 Classification | `Document Ready` → **`Classify Document`** (Text Classifier) : **une sortie par type** (invoice, supplier_sheet, manual, irrelevant, et *Other* en cas de doute, qui part en revue humaine) |
| 6 Extraction | **`Extract Invoice` / `Extract Supplier Sheet` / `Extract Manual`** (Information Extractor, chacun avec son schéma JSON), puis `Parse …` ; `Mark Discarded` pour les documents non pertinents |
| 7 Validation | `Validate Extraction` (Code JS : champs requis, date réelle, total = somme des lignes (+ taxe), etc.) |
| 8 Branche | `IF Auto-Approve` (aucun contrôle bloquant en échec **et** confiance ≥ 0,8) |
| 9a Auto | `Mark Auto-Approved` → `Prepare Records` → `Route by Table` → **`Supabase: invoices` / `suppliers` / `documents`** |
| 9b Humain | `Loop Review Items` (un document à la fois), puis `Prepare Review Message`, `Send Review Email` (document original + JSON + contrôles en échec), `Wait for Human Review` (formulaire) et `Apply Review Decision` |
| 10 Log | **toutes les branches convergent vers `Prepare Records`**, puis `Build Log Row` (reprend l'`id` de la ligne métier créée dans `record_id`), puis **`Supabase: processing_log`** |
| 11 Erreurs | Workflow séparé `Document Extraction - Error Alert` |

\* Le nœud *Split Out* ne sait pas éclater des binaires (`attachment_0`, `attachment_1`…). Un nœud Code fait donc ce travail et garde le fichier dans le champ binaire `data`.

Un PDF est considéré comme « scanné » quand sa couche texte fait moins de 40 caractères (`min_text_chars`). Un email **sans pièce jointe** est toujours marqué *skipped*, quelle que soit la réponse du LLM.

---

## 2. Installation (une seule fois)

```bash
cd n8n_extraction
pip install reportlab pymupdf "psycopg[binary]" requests
pip install -r ocr_service/requirements.txt
```

### 2.1 Base de données Supabase

1. Créez un projet sur https://supabase.com et notez le **mot de passe de la base** choisi à la création.
2. Ouvrez **SQL Editor → New query**, collez tout le contenu de `sql/schema.sql`, puis cliquez sur **Run**. Les 6 tables apparaissent dans *Table Editor*. La RLS y est activée : elles ne sont pas lisibles avec la clé publique `anon`, mais n8n y accède normalement.
3. *(Seulement pour `scripts/evaluate.py`, qui lit la base directement ; n8n passe par l'API.)* Cliquez sur **Connect** (en haut du tableau de bord), puis choisissez **Session pooler**. Notez :
   - Host : `aws-0-<region>.pooler.supabase.com`
   - Port : `5432`
   - Database : `postgres`
   - User : `postgres.<référence-du-projet>`

   N'utilisez pas la *Direct connection* (`db.<ref>.supabase.co`) : elle n'est accessible qu'en IPv6, ce qui échoue sur la plupart des connexions Windows et des box.

Pour `evaluate.py`, définissez une fois la chaîne de connexion (PowerShell) :

```powershell
setx EXTRACTION_DSN "postgresql://postgres.<ref>:<mot_de_passe>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
```

Rouvrez ensuite le terminal pour que la variable soit prise en compte.

### 2.2 Serveurs mail de test

```bash
docker compose up -d
```

Cette commande démarre les services suivants :

- **GreenMail** : boîte `inbox@test.local` (mot de passe `inbox`). SMTP sur le port **3025** (c'est là qu'on envoie les tests) et IMAP sur le port **3143** (c'est là que n8n lit).
- **Mailpit** : SMTP sur le port **1025** et interface web sur http://localhost:8025. Les emails de revue humaine et les alertes y arrivent.

### 2.3 Service OCR (Docling)

```bash
python -m uvicorn app:app --app-dir ocr_service --host 127.0.0.1 --port 8001
```

Vérification : http://127.0.0.1:8001/health. Deux moteurs sont disponibles, choisis avec la variable `OCR_ENGINE` :

| Moteur | Avantages | Temps (CPU) |
|---|---|---|
| `docling` (défaut) | Analyse la mise en page, **reconstruit les tableaux** en Markdown, accents corrects ; OCR fait par RapidOCR | ~90 s au 1er appel (chargement des modèles), puis ~10-40 s par page |
| `rapidocr` | Très léger et rapide, texte brut regroupé en lignes | ~5 s par page |

Pour utiliser RapidOCR seul (PowerShell) :

```powershell
$env:OCR_ENGINE="rapidocr"; python -m uvicorn app:app --app-dir ocr_service --host 127.0.0.1 --port 8001
```

Docling ne sert qu'aux images et aux PDF **scannés** : les PDF avec une couche texte sont lus directement par n8n (nœud *Extract PDF Text*).

### 2.4 Jeu de données

```bash
python scripts/download_public_datasets.py
```

```bash
python scripts/generate_dataset.py
```

La première commande récupère 10 reçus SROIE et 10 reçus CORD. En cas d'échec réseau, elle affiche les liens pour un téléchargement manuel. La seconde génère 5 factures (dont une scannée et une incohérente), 5 fiches fournisseur, 5 manuels, 5 documents non pertinents, 10 emails mixtes et `data/manifest.json`.

---

## 3. Import dans n8n

1. Ouvrez `workflows/document_extraction_workflow.json`, copiez **tout** son contenu, puis collez-le (Ctrl+V) dans un workflow vide de n8n. Vous pouvez aussi passer par *⋯ → Import from File*.
2. Faites de même avec `workflows/error_alert_workflow.json` dans un **second** workflow, puis enregistrez-le.
3. Créez les credentials et affectez-les aux nœuds (ils apparaissent en rouge) :

| Credential | Nœuds | Valeurs pour le test local |
|---|---|---|
| **IMAP** | Email Trigger (IMAP) | host `127.0.0.1`, port `3143`, SSL **off**, user `inbox@test.local`, password `inbox` |
| **Groq account** | les 2 nœuds `Groq Chat Model` (reliés au filtre, à la classification et aux 3 extracteurs) | API Key : ta clé Groq seule, **sans** `Bearer` (https://console.groq.com/keys) |
| **Supabase API** | les 5 nœuds `Supabase: …` du workflow principal et `Supabase: error_log` | Host `https://<ref>.supabase.co`, Service Role Secret = clé **`service_role`** (Supabase → Project Settings → API Keys) |
| **SMTP** | Send Review Email, Send Alert Email | host `127.0.0.1`, port `1025`, SSL off, sans utilisateur (Mailpit) |

> Les nœuds **Supabase** (Row → Create, *Auto-Map Input Data to Columns*) passent par l'API REST de Supabase. L'item qui entre dans chaque nœud contient **uniquement les colonnes de la table** ; les champs techniques `_target` et `_log` sont exclus par l'option *Inputs to Ignore*.
>
> Écarts avec une requête SQL unique : la ligne métier et la ligne `processing_log` sont écrites en **deux appels**. Si le second échoue, le document reste enregistré sans sa ligne de suivi, et le workflow d'erreur envoie une alerte.
>
> La clé `service_role` donne tous les droits et passe outre la RLS : ne la mettez que dans n8n.

> ⚠ Utilisez `127.0.0.1` plutôt que `localhost` dans n8n : Node.js résout `localhost` en IPv6 (`::1`), et les services qui n'écoutent qu'en IPv4 renvoient alors *connection refused*.

4. Dans le nœud **Config and Normalize**, adaptez le bloc `CFG` : `ocr_url`, `review_email`, `from_email` et `auto_approve_threshold`. Le **modèle** se choisit dans les nœuds *Groq Chat Model* (par défaut `openai/gpt-oss-120b`). Les **catégories et prompts** se trouvent dans les nœuds *Relevance Filter* et *Classify Document*, et les **schémas d'extraction** dans les 3 nœuds *Extract …*.

> Pour passer à un autre fournisseur (OpenAI, Mistral…), remplace simplement les nœuds *Groq Chat Model* par le Chat Model correspondant ; le reste du workflow ne change pas.
5. Dans le workflow principal, ouvrez *Settings → Error workflow* et choisissez **Document Extraction - Error Alert**.
6. **Activez** le workflow principal (bouton *Active*). Le trigger IMAP ne tourne en continu que si le workflow est actif.

> Pour la revue humaine, le lien du formulaire utilise l'URL publique de n8n. En local, `http://localhost:5678/form-waiting/…` fonctionne. Si n8n tourne derrière un domaine, définissez `WEBHOOK_URL`.

---

## 4. Tester avec les emails

### 4.1 Les 10 scénarios mixtes (Step 12)

```bash
python scripts/send_test_emails.py --mode mixed
```

Le script dépose les 10 emails dans `inbox@test.local` via GreenMail (port 3025). Chaque email porte les en-têtes `X-Test-Case-Id` et `X-Test-Run-Id`, que n8n recopie dans `email_log` et `processing_log`. Le script affiche un `run_id`, par exemple `run-20260930-101500`.

| Cas | Contenu | Résultat attendu |
|---|---|---|
| MIX-01 | message personnel, sans pièce jointe | **skipped** (Step 2a) |
| MIX-02 | relance de facture **sans pièce jointe** | **skipped** (pas de pièce jointe) |
| MIX-03 | 1 facture PDF | invoice, auto-approuvée |
| MIX-04 | facture + flyer publicitaire | 2 items : invoice approuvée, flyer *discarded_irrelevant* |
| MIX-05 | 3 factures (PDF + photos SROIE et CORD) | 3 items invoice (les reçus passent par l'OCR) |
| MIX-06 | fiche fournisseur + manuel | supplier_sheet + manual |
| MIX-07 | newsletter avec PDF | **skipped** au filtre |
| MIX-08 | facture **scannée** (PDF image) | OCR, puis invoice |
| MIX-09 | 2 photos de reçus + message personnel | 2 invoice + 1 irrelevant |
| MIX-10 | fiche + manuel + invitation + **facture au total faux** | la facture part en **revue humaine** (`totals_consistent` en échec) |

Pendant l'exécution :

- **Revue humaine** : ouvrez http://localhost:8025 (Mailpit). Chaque email de revue contient le document original, le JSON extrait, les contrôles en échec et un lien **« Ouvrir le formulaire de validation »**. Trois choix sont possibles : `approve`, `approve_with_corrections` (collez le JSON corrigé, et changez éventuellement le type) ou `reject`. Les documents à revoir sont traités **un par un**. Sans réponse sous 24 h, le statut devient `review_timeout`.
- **Exécutions** : dans n8n, l'onglet *Executions* montre chaque email et le chemin suivi.

### 4.2 Le jeu complet, pour les chiffres de précision

```bash
python scripts/send_test_emails.py --mode documents --delay 5
```

Cette commande envoie un email par document (40 au total). Elle sert à mesurer la classification et l'extraction sur tout le jeu. `--mode all` envoie les deux jeux, et `--only MIX-04,MIX-10` rejoue des cas précis.

### 4.3 Évaluer

```bash
python scripts/evaluate.py --run-id run-20260930-101500
```

Le rapport (également écrit dans `reports/<run_id>.md`) contient :

- le filtrage et l'éclatement par email (attendu / obtenu) ;
- pour chaque pièce jointe : le type prédit, la source du texte (couche PDF ou OCR), la confiance et le statut final ;
- la **matrice de confusion** de classification ;
- l'**exactitude par champ** (vendor, date, total, invoice_number, company_name, products_offered F1, page_count…) ;
- la répartition auto-approuvé / revue humaine / rejeté.

Requêtes utiles, à lancer dans Supabase → SQL Editor :

```sql
SELECT test_case_id, file_name, detected_type, confidence, final_status, validation_errors
FROM processing_log WHERE test_run_id = 'run-…' ORDER BY id;
SELECT * FROM v_processing_summary;
SELECT * FROM email_log ORDER BY id DESC;
```

### 4.4 Tester la gestion d'erreur (Step 11)

Arrêtez le service OCR, puis :

```bash
python scripts/send_test_emails.py --only MIX-08
```

Le nœud `OCR Service` échoue après 2 tentatives. L'alerte arrive dans Mailpit et une ligne est ajoutée à `error_log`.

### 4.5 Avec une vraie boîte mail (Gmail, Outlook…)

Utilisez un compte dédié aux tests avec un **mot de passe d'application**. Dans n8n, le credential IMAP pointe alors sur `imap.gmail.com:993` avec SSL. Envoyez les tests ainsi :

```bash
python scripts/send_test_emails.py --mode mixed --host smtp.gmail.com --port 587 --starttls --user compte.test@gmail.com --to compte.test@gmail.com
```

Le mot de passe se passe par la variable d'environnement `SMTP_PASSWORD`. Si le serveur supprime les en-têtes `X-Test-*`, `evaluate.py` retrouve les emails par leur `Message-ID`.

Vous pouvez aussi glisser les fichiers `data/mixed_emails/*.eml` dans un client mail et les transférer à la boîte surveillée. Dans ce cas, l'évaluation automatique par `run_id` n'est pas disponible.

---

## 5. Remarques

- **Limites de débit du LLM** : le compte Groq gratuit est limité à **8 000 tokens par minute et par modèle**. Le filtre et la classification utilisent donc `openai/gpt-oss-20b`, et l'extraction `openai/gpt-oss-120b` : chacun a son propre quota. Les nœuds IA sont aussi cadencés avec leur option *Batch Processing* (filtre : lots de 2 toutes les 12 s ; classification : lots de 2 toutes les 30 s ; extraction : 1 document toutes les 30 s). Il faut donc compter **~10 minutes pour les 10 emails mixtes**. Avec un compte Groq payant, réduisez ces délais dans *Options → Batch Processing* de chaque nœud.
- **Confiance de classification** : le Text Classifier ne renvoie pas de score. La confiance d'un document repose donc sur les contrôles de validation (étape 7). Un document classé *Other* part toujours en revue humaine.
- **Reçus CORD** (Indonésie, IDR, remises et service) : le contrôle arithmétique échoue souvent. Ces reçus partent alors en revue humaine, ce qui est le comportement voulu.
- **Résultat de référence** (`reports/headless-test.md`, 10 emails mixtes, Text Classifier + Information Extractor sur Groq, OCR Docling) : filtre 10/10, éclatement 10/10, 16/16 pièces jointes journalisées, classification 15/16. Le flyer publicitaire est tombé dans *Other* et a été envoyé en revue humaine au lieu d'être écarté : c'est le comportement prudent prévu. Au total : 9 documents auto-approuvés, 5 en revue (dont la facture incohérente et les reçus CORD), 2 écartés.
- **Modifier le workflow** : éditez `scripts/build_workflow.py`, puis lancez `python scripts/build_workflow.py` pour régénérer les JSON. Le dossier `workflows/_code_check/` sert seulement à `node --check`.

---

## 6. Publier sur GitHub

Le dépôt ne contient **aucun secret** : le fichier `.env` est exclu par `.gitignore`. Les clés (Groq, Supabase, Gmail) sont saisies dans les credentials de n8n, pas dans le code.

Le dossier `data/` n'est pas publié non plus : les images SROIE et CORD appartiennent à leurs auteurs. N'importe qui peut le recréer avec :

```bash
python scripts/download_public_datasets.py
```
```bash
python scripts/generate_dataset.py
```

Pour créer le dépôt :

1. Sur https://github.com/new, créez un dépôt **vide** (sans README), par exemple `n8n-document-extraction`, en **Private** si le code ne doit pas être public.
2. Dans le dossier `n8n_extraction` :

```bash
git init -b main
```
```bash
git add .
```
```bash
git commit -m "Pipeline n8n d'extraction de documents"
```
```bash
git remote add origin https://github.com/<votre-compte>/n8n-document-extraction.git
```
```bash
git push -u origin main
```

Au premier `push`, Windows ouvre une fenêtre de connexion GitHub.
