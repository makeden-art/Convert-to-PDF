"""Convert-to-PDF — сервис конвертации документов в PDF."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

app = FastAPI(title="Convert to PDF", version="0.1.0")

SUPPORTED_OFFICE = {".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".rtf"}
SUPPORTED_PASSTHROUGH = {".pdf"}


def _version() -> str:
    p = Path(__file__).parent / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


def _convert_with_libreoffice(src: Path, out_dir: Path) -> Path:
    proc = subprocess.run(
        [
            "soffice",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "LibreOffice conversion failed")
    pdf = out_dir / f"{src.stem}.pdf"
    if not pdf.exists():
        raise RuntimeError("PDF не создан после конвертации")
    return pdf


@app.get("/health")
async def health():
    return {"status": "ok", "version": _version(), "service": "convert-to-pdf"}


@app.get("/api/check_update")
async def check_update():
  import os
  import urllib.request

  current = _version()
  url = os.getenv(
      "UPDATE_VERSION_URL",
      "https://raw.githubusercontent.com/makeden-art/Convert-to-PDF/main/VERSION",
  )
  try:
      with urllib.request.urlopen(url, timeout=5) as resp:
          remote = resp.read().decode("utf-8").strip()

      def parse(v: str) -> tuple:
          try:
              return tuple(map(int, v.split(".")))
          except Exception:
              return (0, 0, 0)

      has_update = bool(remote and parse(remote) > parse(current))
      return JSONResponse({"current": current, "remote": remote, "has_update": has_update})
  except Exception as e:
      return JSONResponse({"current": current, "remote": "unknown", "has_update": False, "error": str(e)})


@app.get("/", response_class=HTMLResponse)
@app.get("/convert", response_class=HTMLResponse)
async def convert_page() -> str:
    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Convert to PDF</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e5e7eb; margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
    .box {{ max-width: 520px; width: 100%; padding: 24px; background: #111827; border: 1px solid rgba(148,163,184,.3); border-radius: 16px; }}
    h1 {{ margin: 0 0 8px; color: #38bdf8; font-size: 22px; }}
    p {{ color: #9ca3af; font-size: 14px; line-height: 1.5; }}
    .drop {{ border: 2px dashed rgba(148,163,184,.4); border-radius: 12px; padding: 32px; text-align: center; margin: 20px 0; cursor: pointer; }}
    .drop:hover {{ border-color: #38bdf8; }}
    button {{ background: #38bdf8; color: #000; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; }}
    button:disabled {{ opacity: .5; }}
    .ver {{ margin-top: 16px; font-size: 12px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>Convert to PDF</h1>
    <p>Конвертация DOC/DOCX/XLS/XLSX/ODT/ODS/RTF и PDF. DWG/DXF — в следующих версиях.</p>
    <div class="drop" id="drop">Перетащите файл или нажмите для выбора</div>
    <input type="file" id="file" style="display:none" accept=".pdf,.doc,.docx,.xls,.xlsx,.odt,.ods,.rtf" />
    <button id="btn" disabled>Конвертировать</button>
    <div class="ver">v{_version()}</div>
  </div>
  <script>
    const drop = document.getElementById('drop');
    const input = document.getElementById('file');
    const btn = document.getElementById('btn');
    let file = null;
    drop.onclick = () => input.click();
    input.onchange = () => {{ file = input.files[0]; btn.disabled = !file; drop.textContent = file ? file.name : 'Выберите файл'; }};
    drop.ondragover = e => {{ e.preventDefault(); }};
    drop.ondrop = e => {{
      e.preventDefault();
      file = e.dataTransfer.files[0];
      btn.disabled = !file;
      drop.textContent = file ? file.name : 'Выберите файл';
    }};
    btn.onclick = async () => {{
      if (!file) return;
      btn.disabled = true;
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/convert', {{ method: 'POST', body: fd }});
      if (!r.ok) {{ alert(await r.text()); btn.disabled = false; return; }}
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (file.name.replace(/\\.[^.]+$/, '') || 'document') + '.pdf';
      a.click();
      btn.disabled = false;
    }};
  </script>
</body>
</html>
"""


@app.post("/api/convert")
async def api_convert(file: UploadFile = File(...)):
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in SUPPORTED_OFFICE | SUPPORTED_PASSTHROUGH:
        raise HTTPException(
            status_code=400,
            detail=f"Формат {suffix} пока не поддерживается. Доступно: {', '.join(sorted(SUPPORTED_OFFICE | SUPPORTED_PASSTHROUGH))}",
        )

    tmp = Path(tempfile.mkdtemp(prefix="convert_pdf_"))
    try:
        src = tmp / f"{uuid.uuid4().hex}{suffix}"
        src.write_bytes(await file.read())

        if suffix in SUPPORTED_PASSTHROUGH:
            out = tmp / "result.pdf"
            shutil.copy(src, out)
        else:
            out = _convert_with_libreoffice(src, tmp)

        return FileResponse(
            path=str(out),
            media_type="application/pdf",
            filename=f"{Path(file.filename).stem}.pdf",
            background=BackgroundTask(lambda: shutil.rmtree(tmp, ignore_errors=True)),
        )
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
