"""
Construit les deux workflows n8n importables :
  workflows/document_extraction_workflow.json   (Steps 1 → 10)
  workflows/error_alert_workflow.json           (Step 11)

Le code JS des nœuds Code est écrit ici en clair ; relancer ce script après modification :
    python scripts/build_workflow.py
"""
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows"

# ------------------------------------------------------------------ configuration (modifiable ensuite dans n8n)
CONFIG_JS = r"""
// ====== CONFIGURATION — à adapter ======
const CFG = {
  // Modèle LLM : nœuds « Groq Chat Model ». Prompts : nœuds Text Classifier / Information Extractor.
  ocr_url: 'http://127.0.0.1:8001/ocr',  // 127.0.0.1 et non localhost (Node résout localhost en ::1)
  review_email: 'review@test.local',
  from_email: 'n8n-extraction@test.local',
  min_text_chars: 40,          // en dessous : PDF considéré comme scanné -> OCR
  auto_approve_threshold: 0.8, // confiance minimale pour l'auto-approbation
};
// =======================================

const h = (v) => (Array.isArray(v) ? v.join(',') : (v ?? '')).toString().trim();
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  const meta = j.metadata || {};
  const binary = item.binary || {};
  const attKeys = Object.keys(binary).filter((k) => k.startsWith('attachment_'));
  const messageId = h(meta['message-id'] || j.messageId || j['message-id']);
  out.push({
    json: {
      email_id: messageId || `${h(j.from)}|${h(j.subject)}|${h(j.date)}`,
      sender: h(j.from),
      subject: h(j.subject),
      email_date: h(j.date) || null,
      body_snippet: h(j.textPlain || j.text).slice(0, 500),
      attachment_count: attKeys.length,
      attachment_names: attKeys.map((k) => binary[k].fileName || k),
      test_case_id: h(meta['x-test-case-id']) || null,
      test_run_id: h(meta['x-test-run-id']) || null,
      cfg: CFG,
    },
    binary,
  });
}
return out;
"""

MARK_SKIPPED_JS = r"""
// Step 2a — une ligne email_log (uniquement les colonnes de la table : auto-map Supabase)
const reason = $json.attachment_count === 0 ? 'aucune pièce jointe' : 'classé non pertinent par le LLM';
return { json: {
  email_id: $json.email_id,
  sender: $json.sender,
  subject: $json.subject,
  attachment_count: $json.attachment_count,
  status: 'skipped',
  reason: 'filtered as irrelevant: ' + reason,
  test_case_id: $json.test_case_id ?? null,
  test_run_id: $json.test_run_id ?? null,
} };
"""

SPLIT_JS = r"""
// Step 3 — une sortie par pièce jointe (Split Out ne sait pas éclater les binaires, d'où ce Code node)
const out = [];
$input.all().forEach((item, i) => {
  const { attachment_names, ...email } = item.json;
  const bin = item.binary || {};
  Object.keys(bin).filter((k) => k.startsWith('attachment_')).forEach((k, idx) => {
    const b = bin[k];
    const file_name = b.fileName || k;
    const ext = (b.fileExtension || file_name.split('.').pop() || '').toLowerCase();
    const mime = (b.mimeType || '').toLowerCase();
    let file_kind = 'other';
    if (mime === 'application/pdf' || ext === 'pdf') file_kind = 'pdf';
    else if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp'].includes(ext)) file_kind = 'image';
    else if (mime.startsWith('text/') || ['txt', 'csv', 'md'].includes(ext)) file_kind = 'text';
    out.push({
      json: { ...email, attachment_id: `${email.email_id}#${idx}`, attachment_index: idx,
              file_name, mime_type: mime, file_kind, file_size: b.fileSize || null },
      binary: { data: b },
      pairedItem: { item: i },
    });
  });
});
return out;
"""

CHECK_TEXT_LAYER_JS = r"""
const src = $('Split Attachments').item;
const raw = $json.text || '';
const needs_ocr = raw.replace(/\s+/g, '').length < src.json.cfg.min_text_chars;
return {
  json: { ...src.json, text: needs_ocr ? '' : raw, page_count_detected: $json.numpages ?? null,
          text_source: needs_ocr ? 'pending_ocr' : 'pdf_text_layer', needs_ocr },
  binary: src.binary,
};
"""

ATTACH_OCR_JS = r"""
const src = $('Split Attachments').item;
return {
  json: { ...src.json, text: $json.text || '', text_source: 'ocr:' + ($json.engine || 'unknown'),
          page_count_detected: $json.pages ?? null },
  binary: src.binary,
};
"""

READ_TEXT_JS = r"""
const out = [];
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  const buf = await this.helpers.getBinaryDataBuffer(i, 'data');
  out.push({ json: { ...items[i].json, text: buf.toString('utf8'), text_source: 'plain_text', page_count_detected: null },
             binary: items[i].binary, pairedItem: { item: i } });
}
return out;
"""

DOCUMENT_READY_JS = r"""
// Point de convergence des 3 chemins (PDF texte / OCR / fichier texte)
const text = ($json.text || '').trim();
return { json: { ...$json, text, text_chars: text.length }, binary: $input.item.binary };
"""

PARSE_EXTRACTION_JS = r"""
// Sortie de l'Information Extractor ({ output: {...} }) + contexte de la pièce jointe
const src = $('Document Ready').item;
const out = $json.output;
const ok = out && typeof out === 'object';
return {
  json: { ...src.json, doc_type: '%s', class_confidence: null, extracted: ok ? out : {},
          extraction_parse_error: ok ? null : ('extraction impossible' + ($json.error ? ' : ' + $json.error : '')) },
  binary: src.binary,
};
"""

MARK_UNCLASSIFIED_JS = r"""
// Text Classifier -> sortie « Other » : aucune catégorie claire -> revue humaine
return {
  json: { ...$json, doc_type: 'unknown', class_confidence: null, extracted: {}, confidence: 0,
          validation: { passed: false, critical_failed: true, checks: [],
                        errors: ['aucune catégorie claire (Text Classifier : Other)'] } },
  binary: $input.item.binary,
};
"""

HELPERS_JS = r"""
function toNum(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  let s = String(v).replace(/[^\d.,\-]/g, '');
  if (!s) return null;
  const lc = s.lastIndexOf(','), ld = s.lastIndexOf('.');
  if (lc > -1 && ld > -1) s = lc > ld ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
  else if (lc > -1) s = (/,\d{1,2}$/.test(s) && (s.match(/,/g) || []).length === 1) ? s.replace(',', '.') : s.replace(/,/g, '');
  else if (ld > -1 && ((s.match(/\./g) || []).length > 1 || /\.\d{3}$/.test(s))) s = s.replace(/\./g, '');
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : null;
}
function toISODate(v) {
  if (!v) return null;
  const s = String(v).trim();
  let y, m, d, r;
  if ((r = s.match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})/))) [y, m, d] = [r[1], r[2], r[3]];
  else if ((r = s.match(/^(\d{1,2})[-\/.](\d{1,2})[-\/.](\d{2,4})/))) { d = r[1]; m = r[2]; y = r[3].length === 2 ? '20' + r[3] : r[3]; }
  else return null;
  y = +y; m = +m; d = +d;
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
  if (y < 1990 || y > 2100) return null;
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
}
const isFilled = (v) => v !== null && v !== undefined && String(v).trim() !== '' && String(v).toLowerCase() !== 'null';
"""

VALIDATE_JS = HELPERS_JS + r"""
// Step 7 — contrôles par type ; confiance = part de contrôles réussis x confiance de classification
const out = [];
const close = (a, b) => a !== null && b !== null && Math.abs(a - b) <= Math.max(0.02, Math.abs(b) * 0.01);
$input.all().forEach((item, i) => {
  const j = item.json;
  const x = j.extracted && typeof j.extracted === 'object' ? j.extracted : {};
  const checks = [];
  const add = (name, ok, critical, detail = '') => checks.push({ name, ok: !!ok, critical, detail });

  if (j.extraction_parse_error) add('extraction_json', false, true, j.extraction_parse_error);

  if (j.doc_type === 'invoice') {
    const total = toNum(x.total), sub = toNum(x.subtotal), tax = toNum(x.tax);
    const date = toISODate(x.date);
    add('vendor_present', isFilled(x.vendor), true);
    add('date_valid', !!date, true, `date=${JSON.stringify(x.date ?? null)}`);
    add('total_positive', total !== null && total > 0, true, `total=${JSON.stringify(x.total ?? null)}`);
    add('invoice_number_present', isFilled(x.invoice_number), false);
    add('currency_present', isFilled(x.currency), false);
    const lines = Array.isArray(x.line_items) ? x.line_items : [];
    add('line_items_present', lines.length > 0, false);
    const amounts = lines.map((li) => {
      const a = toNum(li && li.amount);
      if (a !== null) return a;
      const q = toNum(li && li.quantity), u = toNum(li && li.unit_price);
      return q !== null && u !== null ? q * u : null;
    });
    if (lines.length && amounts.every((a) => a !== null) && total !== null) {
      const sum = amounts.reduce((s, a) => s + a, 0);
      const ok = close(sum, total)
        || (sub !== null && close(sum, sub) && (tax === null ? close(sub, total) : close(sub + tax, total)))
        || (sub === null && tax !== null && close(sum + tax, total));
      add('totals_consistent', ok, true,
          `somme lignes=${sum.toFixed(2)} sous-total=${sub} taxe=${tax} total=${total}`);
    }
  } else if (j.doc_type === 'supplier_sheet') {
    const c = x.contact && typeof x.contact === 'object' ? x.contact : { name: x.contact };
    add('company_name_present', isFilled(x.company_name), true);
    add('contact_present', isFilled(c.name) || isFilled(c.email) || isFilled(c.phone), true);
    if (isFilled(c.email)) add('contact_email_format', /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(c.email).trim()), false, String(c.email));
    add('address_present', isFilled(x.address), false);
    add('products_present', Array.isArray(x.products_offered) && x.products_offered.length > 0, true);
  } else if (j.doc_type === 'manual') {
    const pc = toNum(x.page_count);
    add('title_present', isFilled(x.title), true);
    add('equipment_reference_present', isFilled(x.equipment_reference), true);
    add('page_count_valid', pc !== null && Number.isInteger(pc) && pc > 0, false, `page_count=${x.page_count}`);
    if (j.page_count_detected) add('page_count_matches_file', pc === j.page_count_detected, false,
                                  `extrait=${pc} fichier=${j.page_count_detected}`);
  }

  const passedCount = checks.filter((c) => c.ok).length;
  const ratio = checks.length ? passedCount / checks.length : 0;
  const cc = typeof j.class_confidence === 'number' ? j.class_confidence : 1;
  const confidence = Math.round(ratio * (0.5 + 0.5 * cc) * 1000) / 1000;
  const critical_failed = checks.some((c) => c.critical && !c.ok);
  const passed = !critical_failed && confidence >= j.cfg.auto_approve_threshold;
  const errors = checks.filter((c) => !c.ok).map((c) => `${c.name}${c.critical ? ' [bloquant]' : ''}${c.detail ? ' — ' + c.detail : ''}`);
  if (!critical_failed && !passed) errors.push(`confiance ${confidence} < seuil ${j.cfg.auto_approve_threshold}`);
  out.push({ json: { ...j, confidence, validation: { passed, critical_failed, checks, errors } },
             binary: item.binary, pairedItem: { item: i } });
});
return out;
"""

MARK_JS = r"""
const { text, ...rest } = $json;
return { json: { ...rest, final_status: '%s', review_mode: '%s'%s } };
"""

PREPARE_REVIEW_JS = r"""
const j = $json;
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const errors = (j.validation && j.validation.errors) || [];
const review_html = `
<h2>Revue requise : ${esc(j.file_name)}</h2>
<p><b>Email :</b> ${esc(j.subject)} — ${esc(j.sender)}<br>
<b>Type détecté :</b> ${esc(j.doc_type)} — <b>confiance :</b> ${esc(j.confidence)}<br>
<b>Source du texte :</b> ${esc(j.text_source)}</p>
<h3>Contrôles en échec</h3>
<ul>${errors.map((e) => `<li>${esc(e)}</li>`).join('') || '<li>—</li>'}</ul>
<h3>JSON extrait</h3>
<pre style="background:#f4f4f4;padding:8px">${esc(JSON.stringify(j.extracted, null, 2))}</pre>
<p><a href="${$execution.resumeFormUrl}" style="font-size:16px">&#10145; Ouvrir le formulaire de validation</a></p>
<p style="color:#888">Le document original est joint à cet email.</p>`;
return { json: { ...j, review_html }, binary: $input.item.binary };
"""

APPLY_REVIEW_JS = r"""
const ctx = $('Prepare Review Message').first().json;
const f = $json;
const decision = String(f.decision || '').trim();
let extracted = ctx.extracted;
let doc_type = ctx.doc_type;
let final_status;
let review_note = '';
if (!decision) final_status = 'review_timeout';
else if (decision === 'reject') final_status = 'human_rejected';
else {
  final_status = 'human_approved';
  if (decision === 'approve_with_corrections' && String(f.corrected_json || '').trim()) {
    try { extracted = JSON.parse(f.corrected_json); final_status = 'human_corrected'; }
    catch (e) { review_note = 'corrected_json invalide : extraction d\'origine conservée'; }
  }
  if (f.corrected_doc_type && f.corrected_doc_type !== 'keep' && f.corrected_doc_type !== doc_type) {
    doc_type = f.corrected_doc_type;
    final_status = 'human_corrected';
  }
}
const { review_html, text, ...rest } = ctx;
return { json: { ...rest, doc_type, extracted, final_status, review_mode: 'human',
                 review_decision: decision || 'timeout', review_note } };
"""

PREPARE_RECORDS_JS = HELPERS_JS + r"""
// Step 9 / 10 : pour chaque pièce jointe, la ligne métier (si approuvée) + sa ligne processing_log.
// Sortie « à plat » = colonnes de la table métier ; _target et _log sont ignorés par le nœud Supabase.
const APPROVED = ['auto_approved', 'human_approved', 'human_corrected'];
const TABLE = { invoice: 'invoices', supplier_sheet: 'suppliers', manual: 'documents' };
const str = (v) => (isFilled(v) ? (typeof v === 'object' ? JSON.stringify(v) : String(v)) : null);

function businessRecord(t, x, j) {
  const base = { attachment_id: j.attachment_id, email_id: j.email_id, source_file: j.file_name, raw: x };
  if (t === 'invoice') return { ...base,
    vendor: str(x.vendor), invoice_number: str(x.invoice_number), invoice_date: toISODate(x.date),
    subtotal: toNum(x.subtotal), tax: toNum(x.tax), total: toNum(x.total), currency: str(x.currency),
    line_items: Array.isArray(x.line_items) ? x.line_items : [] };
  if (t === 'supplier_sheet') return { ...base,
    company_name: str(x.company_name), contact: x.contact ?? null, address: str(x.address),
    products_offered: Array.isArray(x.products_offered) ? x.products_offered : [] };
  const pc = toNum(x.page_count);
  return { ...base, doc_kind: 'manual',
    title: str(x.title), equipment_reference: str(x.equipment_reference),
    page_count: pc !== null ? Math.round(pc) : null, summary: str(x.summary),
    content_text: (j.text || '').slice(0, 100000) || null };
}

return $input.all().map((item, i) => {
  const j = item.json;
  const x = j.extracted && typeof j.extracted === 'object' ? j.extracted : {};
  const target = APPROVED.includes(j.final_status) ? (TABLE[j.doc_type] || null) : null;
  const _log = {
    attachment_id: j.attachment_id, email_id: j.email_id,
    test_case_id: j.test_case_id ?? null, test_run_id: j.test_run_id ?? null,
    file_name: j.file_name, mime_type: j.mime_type, text_source: j.text_source || null,
    detected_type: j.doc_type || null, class_confidence: toNum(j.class_confidence), confidence: toNum(j.confidence),
    validation_passed: j.validation ? !!j.validation.passed : null,
    validation_errors: (j.validation && j.validation.errors) || [],
    final_status: j.final_status, review_mode: j.review_mode || 'none', extracted: x,
  };
  const record = target ? businessRecord(j.doc_type, x, j) : {};
  return { json: { ...record, _target: target || 'none', _log }, pairedItem: { item: i } };
});
"""

BUILD_LOG_ROW_JS = r"""
// Step 10 : ligne processing_log, reliée à la ligne métier créée (id renvoyé par Supabase)
const p = $('Prepare Records').item.json;
const saved = p._target !== 'none';
return { json: { ...p._log, target_table: saved ? p._target : null, record_id: saved ? $json.id : null } };
"""

ERROR_LOG_ROW_JS = r"""
return { json: {
  workflow_name: $json.workflow_name, execution_id: $json.execution_id,
  execution_url: $json.execution_url, last_node: $json.last_node, error_message: $json.error_message,
} };
"""

ERROR_FORMAT_JS = r"""
const e = $json.execution || {};
const w = $json.workflow || {};
const message = (e.error && (e.error.message || e.error.description)) || 'Erreur inconnue';
return { json: {
  workflow_name: w.name || null,
  execution_id: e.id ? String(e.id) : null,
  execution_url: e.url || null,
  last_node: e.lastNodeExecuted || null,
  error_message: message,
  html: `<h2>&#9888; Échec du workflow « ${w.name} »</h2><p><b>Nœud :</b> ${e.lastNodeExecuted}<br><b>Erreur :</b> ${message}</p>` +
        (e.url ? `<p><a href="${e.url}">Voir l'exécution</a></p>` : '') +
        '<p>Le document concerné n\'a PAS été enregistré : relancer l\'exécution après correction.</p>',
} };
"""


# ------------------------------------------------------------------ helpers de construction
def node(name, type_, version, pos, params, **extra):
    return {"parameters": params, "id": str(uuid.uuid4()), "name": name, "type": type_,
            "typeVersion": version, "position": list(pos), **extra}


def code(name, pos, js, per_item=False, **extra):
    p = {"jsCode": js.strip() + "\n"}
    if per_item:
        p["mode"] = "runOnceForEachItem"
    return node(name, "n8n-nodes-base.code", 2, pos, p, **extra)


def cond(left, op_type, operation, right=None):
    c = {"id": str(uuid.uuid4()), "leftValue": left, "operator": {"type": op_type, "operation": operation}}
    if right is None:
        c["rightValue"] = ""
        c["operator"]["singleValue"] = True
    else:
        c["rightValue"] = right
    return c


def conditions(*cs):
    return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
            "conditions": list(cs), "combinator": "and"}


def if_node(name, pos, c):
    return node(name, "n8n-nodes-base.if", 2, pos, {"conditions": conditions(c), "options": {}})


def switch_node(name, pos, field, values):
    rules = [{"conditions": conditions(cond(field, "string", "equals", v)), "renameOutput": True, "outputKey": v}
             for v in values]
    return node(name, "n8n-nodes-base.switch", 3, pos, {"rules": {"values": rules}, "options": {}})


RETRY = {"retryOnFail": True, "maxTries": 5, "waitBetweenTries": 5000}


LC = "@n8n/n8n-nodes-langchain."
DOC_TEXT = ("={{ 'Nom du fichier : ' + $json.file_name + '\\nNombre de pages détecté : ' + ($json.page_count_detected || 'inconnu') "
            "+ '\\n\\nTexte du document :\\n' + ($json.text || '(aucun texte extrait)').slice(0, 6000) }}")


def groq_model(name, pos, model, max_tokens):
    # Groq applique ses limites (8 000 tokens/min en gratuit) PAR MODÈLE : filtre + classification sur le 20b,
    # extraction sur le 120b, pour ne pas partager le même quota.
    return node(name, LC + "lmChatGroq", 1, pos, {
        "model": model,   # liste des modèles : menu déroulant du nœud (credential Groq requis)
        "options": {"temperature": 0, "maxTokensToSample": max_tokens}})


def classifier(name, pos, text, system_prompt, categories, batch_size, delay_ms):
    return node(name, LC + "textClassifier", 1.1, pos, {
        "inputText": text,
        "categories": {"categories": [{"category": c, "description": d} for c, d in categories]},
        "options": {"fallback": "other", "systemPromptTemplate": system_prompt, "enableAutoFixing": True,
                    # Groq gratuit = 8 000 tokens/min : petits lots espacés
                    "batching": {"batchSize": batch_size, "delayBetweenBatches": delay_ms}}}, **RETRY)


def extractor(name, pos, system_prompt, schema, delay_ms):
    return node(name, LC + "informationExtractor", 1.2, pos, {
        "text": DOC_TEXT,
        "schemaType": "manual",
        "inputSchema": json.dumps(schema, ensure_ascii=False, indent=2),
        "options": {"systemPromptTemplate": system_prompt,
                    "batching": {"batchSize": 1, "delayBetweenBatches": delay_ms}}},
        onError="continueRegularOutput", **RETRY)


def nullable(t, desc=None):
    d = {"type": [t, "null"]}
    if desc:
        d["description"] = desc
    return d


INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": nullable("string", "société émettrice de la facture / du reçu"),
        "invoice_number": nullable("string", "numéro de facture ou de reçu"),
        "date": nullable("string", "date du document au format YYYY-MM-DD"),
        "line_items": {"type": "array", "items": {"type": "object", "properties": {
            "description": nullable("string"), "quantity": nullable("number"),
            "unit_price": nullable("number"), "amount": nullable("number")}}},
        "subtotal": nullable("number", "total hors taxes"),
        "tax": nullable("number", "montant de taxe (TVA)"),
        "total": nullable("number", "montant final payé TTC"),
        "currency": nullable("string", "code ISO : EUR, DZD, MYR, IDR..."),
    },
    "required": ["vendor", "invoice_number", "date", "line_items", "subtotal", "tax", "total", "currency"],
}
SUPPLIER_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": nullable("string", "raison sociale du fournisseur"),
        "contact": {"type": "object", "properties": {
            "name": nullable("string"), "role": nullable("string"),
            "email": nullable("string"), "phone": nullable("string")}},
        "address": nullable("string", "adresse postale complète"),
        "products_offered": {"type": "array", "items": {"type": "string"},
                             "description": "produits et services proposés"},
    },
    "required": ["company_name", "contact", "address", "products_offered"],
}
MANUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": nullable("string", "titre du document"),
        "equipment_reference": nullable("string", "uniquement le code modèle / référence de l'équipement, ex. PX-300, sans révision ni autre texte"),
        "page_count": nullable("integer", "nombre de pages du document"),
        "summary": nullable("string", "une phrase de résumé, pour l'indexation"),
    },
    "required": ["title", "equipment_reference", "page_count", "summary"],
}
RULES = " Mets null pour toute information absente, n'invente rien. Le texte peut venir d'un OCR bruité."
PROMPT_INVOICE = ("Tu extrais les données d'une facture fournisseur ou d'un reçu / ticket de caisse. "
                  "Nombres : point décimal, sans séparateur de milliers (\"60.000\" IDR => 60000 ; \"2 150,00\" => 2150). "
                  "total = montant final payé TTC ; subtotal = montant hors taxes." + RULES)
PROMPT_SUPPLIER = "Tu extrais les données d'une fiche de présentation / référencement fournisseur." + RULES
PROMPT_MANUAL = "Tu extrais les métadonnées d'un manuel technique ou d'une notice d'équipement." + RULES


def supabase(name, pos, table, ignore="", **extra):
    # Resource Row / Create / auto-map : l'item entrant doit contenir uniquement des colonnes de la table
    # (les champs listés dans inputsToIgnore sont exclus).
    return node(name, "n8n-nodes-base.supabase", 1, pos, {
        "resource": "row", "operation": "create", "tableId": table,
        "dataToSend": "autoMapInputData", "inputsToIgnore": ignore}, **extra)


def sticky(text, pos, w, h, color=7):
    return node(f"Note {uuid.uuid4().hex[:6]}", "n8n-nodes-base.stickyNote", 1, pos,
                {"content": text, "width": w, "height": h, "color": color})


# ------------------------------------------------------------------ workflow principal
nodes = [
    sticky("## Extraction de documents (multi-types)\n"
           "Avant la 1re exécution :\n"
           "1. Credentials : **IMAP** (trigger), **Groq account** (3 nœuds *Groq Model*), "
           "**Supabase API** (6 nœuds *Supabase* : Host `https://<ref>.supabase.co` + clé `service_role`), **SMTP** (email de revue)\n"
           "2. Adapter le bloc CONFIG du nœud *Config and Normalize* (URL OCR, email de revue, seuil) ; modèle LLM dans *Groq Chat Model*\n"
           "3. Settings du workflow → Error workflow = *Document Extraction - Error Alert*\n"
           "4. Exécuter `sql/schema.sql` dans Supabase → SQL Editor",
           (-420, -340), 560, 260, 4),
    node("Email Trigger (IMAP)", "n8n-nodes-base.emailReadImap", 2, (0, 300), {
        "mailbox": "INBOX", "postProcessAction": "read", "downloadAttachments": True, "format": "simple",
        "options": {"customEmailConfig": "[\"UNSEEN\"]"}}),
    code("Config and Normalize", (220, 300), CONFIG_JS),
    classifier("Relevance Filter", (440, 300),
               "={{ 'Objet : ' + $json.subject + '\\nExpéditeur : ' + $json.sender + '\\nPièces jointes (' + $json.attachment_count "
               "+ ') : ' + ($json.attachment_names || []).join(', ') + '\\nExtrait du corps : ' + ($json.body_snippet || '(vide)').slice(0, 300) }}",
               "Tu tries la boîte mail du service maintenance / achats d'une entreprise de tramway. "
               "Classe l'email (objet, expéditeur, noms des pièces jointes, extrait du corps) dans une des catégories : {categories}. "
               "Ne donne aucune explication, réponds uniquement avec le JSON demandé.",
               [("relevant", "l'email contient probablement une facture, un reçu / ticket de caisse, une fiche fournisseur, "
                             "un manuel technique ou un autre document métier à traiter"),
                ("not_relevant", "newsletter, promotion, publicité, message personnel, invitation, notification sans document métier")],
               2, 12000),
    groq_model("Groq Model - Filter", (460, 540), "openai/gpt-oss-20b", 1024),
    if_node("IF Has Attachments", (880, 200), cond("={{ $json.attachment_count }}", "number", "gt", 0)),
    code("Mark Skipped", (880, 520), MARK_SKIPPED_JS, per_item=True),
    supabase("Supabase: email_log", (1100, 520), "email_log"),
    code("Split Attachments", (1100, 200), SPLIT_JS),
    switch_node("Switch File Kind", (1320, 200), "={{ $json.file_kind }}", ["pdf", "image", "text", "other"]),
    node("Extract PDF Text", "n8n-nodes-base.extractFromFile", 1, (1540, 0),
         {"operation": "pdf", "binaryPropertyName": "data", "options": {}}, onError="continueRegularOutput"),
    code("Check Text Layer", (1760, 0), CHECK_TEXT_LAYER_JS, per_item=True),
    if_node("IF Needs OCR", (1980, 0), cond("={{ $json.needs_ocr }}", "boolean", "true")),
    node("OCR Service", "n8n-nodes-base.httpRequest", 4.2, (2200, 220), {
        "method": "POST", "url": "={{ $json.cfg.ocr_url }}", "sendBody": True, "contentType": "multipart-form-data",
        "bodyParameters": {"parameters": [{"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"}]},
        "options": {"timeout": 300000}}, retryOnFail=True, maxTries=2, waitBetweenTries=5000),
    code("Attach OCR Text", (2420, 220), ATTACH_OCR_JS, per_item=True),
    code("Read Text File", (1540, 380), READ_TEXT_JS),
    code("Mark Unsupported", (1540, 600), MARK_JS % ("unsupported_format", "none",
         ", doc_type: 'unsupported', extracted: {}, validation: { passed: false, errors: ['format non supporté : ' + $json.mime_type] }"),
         per_item=True),
    code("Document Ready", (2640, 100), DOCUMENT_READY_JS, per_item=True),
    classifier("Classify Document", (2860, 100),
               "={{ 'Nom du fichier : ' + $json.file_name + '\\n\\nTexte du document :\\n' + ($json.text || '(aucun texte extrait)').slice(0, 3000) }}",
               "Classe le document (texte éventuellement issu d'un OCR bruité, dans n'importe quelle langue) "
               "dans UNE des catégories : {categories}. Ne donne aucune explication, réponds uniquement avec le JSON demandé.",
               [("invoice", "facture fournisseur, ticket de caisse, reçu : tout document qui réclame ou atteste un paiement"),
                ("supplier_sheet", "fiche de présentation / référencement d'un fournisseur : coordonnées, contact, produits proposés"),
                ("manual", "manuel technique, notice, procédure de maintenance d'un équipement"),
                ("irrelevant", "publicité, newsletter, message personnel, invitation, article, tout autre document")],
               2, 30000),
    groq_model("Groq Model - Classify", (2880, 640), "openai/gpt-oss-20b", 1024),
    groq_model("Groq Model - Extract", (3180, 760), "openai/gpt-oss-120b", 2048),
    extractor("Extract Invoice", (3300, -160), PROMPT_INVOICE, INVOICE_SCHEMA, 30000),
    extractor("Extract Supplier Sheet", (3300, 60), PROMPT_SUPPLIER, SUPPLIER_SCHEMA, 30000),
    extractor("Extract Manual", (3300, 280), PROMPT_MANUAL, MANUAL_SCHEMA, 30000),
    code("Mark Discarded", (3300, 460), MARK_JS % ("discarded_irrelevant", "none", ", doc_type: 'irrelevant', class_confidence: null, extracted: {}"), per_item=True),
    code("Mark Unclassified", (3300, 620), MARK_UNCLASSIFIED_JS, per_item=True),
    code("Parse Invoice", (3560, -160), PARSE_EXTRACTION_JS % "invoice", per_item=True),
    code("Parse Supplier Sheet", (3560, 60), PARSE_EXTRACTION_JS % "supplier_sheet", per_item=True),
    code("Parse Manual", (3560, 280), PARSE_EXTRACTION_JS % "manual", per_item=True),
    code("Validate Extraction", (3960, 80), VALIDATE_JS),
    if_node("IF Auto-Approve", (4180, 80), cond("={{ $json.validation.passed }}", "boolean", "true")),
    code("Mark Auto-Approved", (4400, -40), MARK_JS % ("auto_approved", "auto", ""), per_item=True),
    # reset quand l'entrée ne vient PAS du retour de boucle : le nœud est alimenté plusieurs fois par exécution
    # (une fois par branche d'extraction) ; sans reset, les nouveaux items partiraient dans "done" sans revue.
    node("Loop Review Items", "n8n-nodes-base.splitInBatches", 3, (4400, 300),
         {"batchSize": 1, "options": {"reset": "={{ $prevNode.name !== 'Apply Review Decision' }}"}}),
    code("Prepare Review Message", (4620, 440), PREPARE_REVIEW_JS, per_item=True),
    node("Send Review Email", "n8n-nodes-base.emailSend", 2.1, (4840, 440), {
        "fromEmail": "={{ $json.cfg.from_email }}",
        "toEmail": "={{ $json.cfg.review_email }}",
        "subject": "=[Revue extraction] {{ $json.doc_type }} — {{ $json.file_name }}",
        "emailFormat": "html",
        "html": "={{ $json.review_html }}",
        "options": {"attachments": "data", "appendAttribution": False}}),
    node("Wait for Human Review", "n8n-nodes-base.wait", 1.1, (5060, 440), {
        "resume": "form",
        "formTitle": "Validation d'une extraction",
        "formDescription": "Vérifiez le JSON reçu par email puis choisissez une décision. "
                           "Pour corriger, collez le JSON complet corrigé.",
        "formFields": {"values": [
            {"fieldLabel": "decision", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "approve"}, {"option": "approve_with_corrections"},
                                         {"option": "reject"}]}},
            {"fieldLabel": "corrected_json", "fieldType": "textarea",
             "placeholder": "{\"vendor\": \"...\", \"total\": 123.45, ...}"},
            {"fieldLabel": "corrected_doc_type", "fieldType": "dropdown",
             "fieldOptions": {"values": [{"option": "keep"}, {"option": "invoice"}, {"option": "supplier_sheet"},
                                         {"option": "manual"}, {"option": "irrelevant"}]}},
        ]},
        "limitWaitTime": True, "limitType": "afterTimeInterval", "resumeAmount": 24, "resumeUnit": "hours",
        "options": {}}),
    code("Apply Review Decision", (5280, 440), APPLY_REVIEW_JS, per_item=True),
    code("Prepare Records", (4840, 100), PREPARE_RECORDS_JS),
    switch_node("Route by Table", (5060, 100), "={{ $json._target }}", ["invoices", "suppliers", "documents", "none"]),
    supabase("Supabase: invoices", (5300, -120), "invoices", "_target,_log"),
    supabase("Supabase: suppliers", (5300, 60), "suppliers", "_target,_log"),
    supabase("Supabase: documents", (5300, 240), "documents", "_target,_log"),
    code("Build Log Row", (5540, 100), BUILD_LOG_ROW_JS, per_item=True),
    supabase("Supabase: processing_log", (5760, 100), "processing_log"),
]
for text, pos, w, h in [
    ("### Steps 1-2 : réception + filtre (Text Classifier)\nPas de pièce jointe ⇒ toujours *skipped*", (-40, 80), 1060, 640),
    ("### Steps 3-4 : 1 item par pièce jointe, texte PDF ou OCR", (1060, -120), 1540, 860),
    ("### Steps 5-6 : Text Classifier (1 sortie par type) puis Information Extractor (1 schéma par type)", (2600, -280), 1140, 1040),
    ("### Steps 7-9 : validation, auto-approbation ou revue humaine (1 par 1)", (3720, -240), 1760, 860),
    ("### Steps 9-10 : enregistrement Supabase\nLigne métier (invoices / suppliers / documents) puis processing_log avec record_id", (4800, -240), 1180, 700),
]:
    nodes.append(sticky(text, pos, w, h))


def edge(to, index=0):
    return {"node": to, "type": "main", "index": index}


connections = {}


def link(src, dst, out=0):
    outs = connections.setdefault(src, {"main": []})["main"]
    while len(outs) <= out:
        outs.append([])
    outs[out].append(edge(dst))


for a, b in [("Email Trigger (IMAP)", "Config and Normalize"), ("Config and Normalize", "Relevance Filter"),
             ("Mark Skipped", "Supabase: email_log"),
             ("Split Attachments", "Switch File Kind"), ("Extract PDF Text", "Check Text Layer"),
             ("Check Text Layer", "IF Needs OCR"), ("OCR Service", "Attach OCR Text"),
             ("Attach OCR Text", "Document Ready"), ("Read Text File", "Document Ready"),
             ("Mark Unsupported", "Prepare Records"), ("Document Ready", "Classify Document"),
             ("Extract Invoice", "Parse Invoice"), ("Extract Supplier Sheet", "Parse Supplier Sheet"),
             ("Extract Manual", "Parse Manual"), ("Parse Invoice", "Validate Extraction"),
             ("Parse Supplier Sheet", "Validate Extraction"), ("Parse Manual", "Validate Extraction"),
             ("Mark Discarded", "Prepare Records"), ("Mark Unclassified", "Loop Review Items"),
             ("Validate Extraction", "IF Auto-Approve"),
             ("Mark Auto-Approved", "Prepare Records"), ("Prepare Review Message", "Send Review Email"),
             ("Send Review Email", "Wait for Human Review"), ("Wait for Human Review", "Apply Review Decision"),
             ("Apply Review Decision", "Loop Review Items"), ("Prepare Records", "Route by Table"),
             ("Supabase: invoices", "Build Log Row"), ("Supabase: suppliers", "Build Log Row"),
             ("Supabase: documents", "Build Log Row"), ("Build Log Row", "Supabase: processing_log")]:
    link(a, b)
# Relevance Filter : 0 relevant, 1 not_relevant, 2 Other (doute -> on traite)
link("Relevance Filter", "IF Has Attachments", 0)
link("Relevance Filter", "Mark Skipped", 1)
link("Relevance Filter", "IF Has Attachments", 2)
link("IF Has Attachments", "Split Attachments", 0)
link("IF Has Attachments", "Mark Skipped", 1)
for i, dst in enumerate(["Extract PDF Text", "OCR Service", "Read Text File", "Mark Unsupported"]):
    link("Switch File Kind", dst, i)
link("IF Needs OCR", "OCR Service", 0)
link("IF Needs OCR", "Document Ready", 1)
# Classify Document : une sortie par catégorie + « Other »
for i, dst in enumerate(["Extract Invoice", "Extract Supplier Sheet", "Extract Manual", "Mark Discarded", "Mark Unclassified"]):
    link("Classify Document", dst, i)
link("IF Auto-Approve", "Mark Auto-Approved", 0)
link("IF Auto-Approve", "Loop Review Items", 1)
link("Loop Review Items", "Prepare Records", 0)    # sortie "done"
for i, dst in enumerate(["Supabase: invoices", "Supabase: suppliers", "Supabase: documents", "Build Log Row"]):
    link("Route by Table", dst, i)
link("Loop Review Items", "Prepare Review Message", 1)  # sortie "loop"
# sous-nœuds modèle -> nœuds IA
connections["Groq Model - Filter"] = {"ai_languageModel": [[
    {"node": "Relevance Filter", "type": "ai_languageModel", "index": 0}]]}
connections["Groq Model - Classify"] = {"ai_languageModel": [[
    {"node": "Classify Document", "type": "ai_languageModel", "index": 0}]]}
connections["Groq Model - Extract"] = {"ai_languageModel": [[
    {"node": n, "type": "ai_languageModel", "index": 0}
    for n in ["Extract Invoice", "Extract Supplier Sheet", "Extract Manual"]]]}

# Identifiants fixes : l'import automatique (Docker) relie le workflow d'erreur. Ignorés lors d'un copier-coller.
MAIN_ID, ERROR_ID = "docExtractMain01", "docExtractErr001"
main_wf = {"id": MAIN_ID, "name": "Document Extraction - Multi-type Inbox", "nodes": nodes, "connections": connections,
           "active": False, "settings": {"executionOrder": "v1", "saveManualExecutions": True, "errorWorkflow": ERROR_ID},
           "pinData": {}, "meta": {"templateCredsSetupCompleted": False}}

# ------------------------------------------------------------------ workflow d'erreur (Step 11)
err_nodes = [
    node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, (0, 200), {}),
    code("Format Alert", (220, 200), ERROR_FORMAT_JS, per_item=True),
    code("Error Log Row", (460, 80), ERROR_LOG_ROW_JS, per_item=True),
    supabase("Supabase: error_log", (680, 80), "error_log", onError="continueRegularOutput"),
    node("Send Alert Email", "n8n-nodes-base.emailSend", 2.1, (460, 320), {
        "fromEmail": "n8n-extraction@test.local", "toEmail": "review@test.local",
        "subject": "=[ALERTE n8n] {{ $json.workflow_name }} — échec sur {{ $json.last_node }}",
        "emailFormat": "html", "html": "={{ $json.html }}", "options": {"appendAttribution": False}}),
    sticky("### Step 11 — Alerte d'erreur\nSélectionner ce workflow dans *Settings → Error workflow* du workflow principal. "
           "Remplacer *Send Alert Email* par un nœud Slack si besoin.", (-40, -60), 520, 200),
]
err_conn = {"Error Trigger": {"main": [[edge("Format Alert")]]},
            "Format Alert": {"main": [[edge("Error Log Row"), edge("Send Alert Email")]]},
            "Error Log Row": {"main": [[edge("Supabase: error_log")]]}}
err_wf = {"id": ERROR_ID, "name": "Document Extraction - Error Alert", "nodes": err_nodes, "connections": err_conn,
          "active": False, "settings": {"executionOrder": "v1"}, "pinData": {}}

OUT.mkdir(exist_ok=True)
(OUT / "document_extraction_workflow.json").write_text(json.dumps(main_wf, ensure_ascii=False, indent=2), encoding="utf-8")
(OUT / "error_alert_workflow.json").write_text(json.dumps(err_wf, ensure_ascii=False, indent=2), encoding="utf-8")

# export des Code nodes pour vérification syntaxique (node --check)
chk = OUT / "_code_check"
chk.mkdir(exist_ok=True)
for wf in (main_wf, err_wf):
    for n in wf["nodes"]:
        if n["type"] == "n8n-nodes-base.code":
            fname = n["name"].replace(" ", "_") + ".js"
            (chk / fname).write_text("async function f() {\n" + n["parameters"]["jsCode"] + "\n}\n", encoding="utf-8")
print(f"OK : {len(nodes)} nœuds (principal), {len(err_nodes)} nœuds (erreur) -> {OUT}")
