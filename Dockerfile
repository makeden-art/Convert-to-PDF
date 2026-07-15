FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    curl ca-certificates smbclient ghostscript \
    && rm -rf /var/lib/apt/lists/*

COPY app.py converter.py cad_converter.py frame_detect.py format_detect.py file_preview.py cad_preview_worker.py convert_page.html viewer_page.html convert_worker.py convert_jobs.py job_control.py VERSION windows_cad_server.py setup_cad_server.ps1 uninstall_cad_server.ps1 ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port 8000 --limit-concurrency ${CONVERT_UVICORN_CONCURRENCY:-16}"]
