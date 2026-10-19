# Service OCR (Docling + RapidOCR) appelé par n8n : POST /ocr, GET /health
FROM python:3.12-slim

# Bibliothèques système requises par OpenCV (Docling / RapidOCR)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch CPU d'abord : sinon pip télécharge la version CUDA (plusieurs Go) avec Docling
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Modèles Docling / RapidOCR téléchargés au 1er appel puis conservés dans ce dossier (volume)
ENV HF_HOME=/models/huggingface \
    OCR_ENGINE=docling
VOLUME ["/models"]

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD curl -fs http://127.0.0.1:8001/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
