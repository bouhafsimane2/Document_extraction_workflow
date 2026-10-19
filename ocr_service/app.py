"""
Micro-service OCR appelé par n8n (Step 4) pour les images et les PDF scannés.

    pip install -r ocr_service/requirements.txt
    python -m uvicorn app:app --app-dir ocr_service --host 127.0.0.1 --port 8001

POST /ocr  (multipart, champ "file")  -> {"text": "...", "pages": N, "engine": "docling"|"rapidocr"}
GET  /health

Moteurs (variable OCR_ENGINE) :
  docling  (défaut) : Docling + OCR RapidOCR. Analyse la mise en page, reconstruit les tableaux,
                      renvoie du Markdown. Idéal pour factures / manuels.
  rapidocr          : RapidOCR seul (ONNX, léger et rapide). Texte brut regroupé en lignes.
OCR_DPI=200 (rendu des pages PDF pour rapidocr)
"""
import io
import os

import pymupdf
from fastapi import FastAPI, File, HTTPException, UploadFile

ENGINE = os.getenv("OCR_ENGINE", "docling").lower()
DPI = int(os.getenv("OCR_DPI", "200"))

app = FastAPI(title="OCR service")
_converter = None
_rapid = None


# ------------------------------------------------------------------ Docling
def converter():
    """Construit une seule fois (le chargement des modèles prend plusieurs secondes)."""
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = True
        opts.do_table_structure = True
        # force_full_page_ocr : n8n n'envoie ici que des scans / images, on OCRise toute la page
        opts.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
        _converter = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        })
    return _converter


def docling_text(data: bytes, filename: str) -> tuple[str, int]:
    from docling.datamodel.base_models import DocumentStream

    if not os.path.splitext(filename)[1]:
        filename += ".pdf" if data[:5] == b"%PDF-" else ".png"
    doc = converter().convert(DocumentStream(name=filename, stream=io.BytesIO(data))).document
    return doc.export_to_markdown(), max(1, len(doc.pages))


# ------------------------------------------------------------------ RapidOCR seul
def rapid():
    global _rapid
    if _rapid is None:
        from rapidocr import RapidOCR
        _rapid = RapidOCR()
    return _rapid


def rapidocr_lines(image: bytes) -> str:
    """Regroupe les mots détectés en lignes (tri par y puis x) : indispensable pour les reçus."""
    res = rapid()(image)
    if res.boxes is None or res.txts is None:
        return ""
    words = []
    for box, text in zip(res.boxes, res.txts):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        words.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), text))
    words.sort()
    lines, current, current_y, current_h = [], [], None, 0
    for y, x, h, text in words:
        if current and abs(y - current_y) > max(current_h, h) * 0.6:
            lines.append(" ".join(t for _, t in sorted(current)))
            current = []
        if not current:
            current_y, current_h = y, h
        current.append((x, text))
    if current:
        lines.append(" ".join(t for _, t in sorted(current)))
    return "\n".join(lines)


def pdf_pages_as_png(data: bytes):
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [page.get_pixmap(dpi=DPI).tobytes("png") for page in doc]


# ------------------------------------------------------------------ API
@app.get("/health")
def health():
    return {"status": "ok", "engine": ENGINE}


@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "fichier vide")
    name = file.filename or ""
    try:
        if ENGINE == "rapidocr":
            images = pdf_pages_as_png(data) if data[:5] == b"%PDF-" else [data]
            text = "\n\n".join(rapidocr_lines(img) for img in images)
            return {"text": text, "pages": len(images), "engine": "rapidocr", "file_name": name}
        text, pages = docling_text(data, name)
        return {"text": text, "pages": pages, "engine": "docling", "file_name": name}
    except Exception as e:  # remonté à n8n -> Error Trigger
        raise HTTPException(500, f"OCR échoué : {e}")
