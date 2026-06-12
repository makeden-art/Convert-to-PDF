"""Convert-to-PDF — конвертация редактируемых форматов в PDF."""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from cad_converter import CAD_EXTENSIONS, convert_cad_to_pdf, oda_available
from converter import (
    SUPPORTED_OFFICE,
    SUPPORTED_CAD,
    SUPPORTED_ALL,
    allowed_roots,
    convert_file_in_place,
    convert_folder,
    validate_folder,
    _convert_with_libreoffice,
)

app = FastAPI(title="Перевод в PDF", version="0.3.1")


def _version() -> str:
    p = Path(__file__).parent / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


class FolderRequest(BaseModel):
    path: str
    recursive: bool = True


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": _version(),
        "service": "convert-to-pdf",
        "allowed_roots": [str(r) for r in allowed_roots()],
        "cad_support": oda_available(),
        "formats": sorted(SUPPORTED_ALL),
    }


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
    roots = ", ".join(str(r) for r in allowed_roots())
    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Перевод в PDF</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e5e7eb; margin: 0; min-height: 100vh; padding: 24px; }}
    .wrap {{ max-width: 640px; margin: 0 auto; }}
    .box {{ padding: 24px; background: #111827; border: 1px solid rgba(148,163,184,.3); border-radius: 16px; margin-bottom: 20px; }}
    h1 {{ margin: 0 0 8px; color: #38bdf8; font-size: 22px; }}
    h2 {{ margin: 0 0 12px; color: #7dd3fc; font-size: 16px; }}
    p, label {{ color: #9ca3af; font-size: 14px; line-height: 1.5; }}
    input[type=text] {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(148,163,184,.4); background: #0f172a; color: #e5e7eb; margin: 8px 0 12px; }}
    .drop {{ border: 2px dashed rgba(148,163,184,.4); border-radius: 12px; padding: 24px; text-align: center; margin: 12px 0; cursor: pointer; }}
    .drop:hover {{ border-color: #38bdf8; }}
    button {{ background: #38bdf8; color: #000; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; }}
    button:disabled {{ opacity: .5; }}
    .chk {{ display: flex; align-items: center; gap: 8px; margin: 8px 0 12px; }}
    pre {{ background: #0f172a; border-radius: 8px; padding: 12px; font-size: 12px; overflow: auto; max-height: 280px; white-space: pre-wrap; }}
    .ver {{ margin-top: 16px; font-size: 12px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="box">
      <h1>Перевод в PDF</h1>
      <p>Офисные и CAD-форматы → PDF. Результат кладётся <b>в ту же папку</b>, что и исходник (<code>отчёт.docx</code> → <code>отчёт.pdf</code>, <code>план.dwg</code> → <code>план.pdf</code>).</p>
      <p>Пример папки для загрузки файлов: <code>/data/documents</code></p>
      <p class="ver">Доступные каталоги на сервере: <code>{roots}</code></p>
    </div>

    <div class="box">
      <h2>📁 Папка целиком</h2>
      <label for="folder">Путь к папке на сервере</label>
      <input type="text" id="folder" placeholder="/data/documents/комплект_1" />
      <div class="chk">
        <input type="checkbox" id="recursive" checked />
        <label for="recursive" style="margin:0">Включая вложенные подпапки</label>
      </div>
      <button id="btn-folder">Конвертировать папку</button>
      <pre id="log-folder" style="display:none"></pre>
    </div>

    <div class="box">
      <h2>📄 Один файл (загрузка)</h2>
      <div class="drop" id="drop">Перетащите файл или нажмите</div>
      <input type="file" id="file" style="display:none" accept=".pdf,.doc,.docx,.xls,.xlsx,.odt,.ods,.rtf,.dwg,.dxf" />
      <button id="btn-file" disabled>Скачать PDF</button>
    </div>

    <div class="ver">v{_version()}</div>
  </div>
  <script>
    const btnFolder = document.getElementById('btn-folder');
    const logFolder = document.getElementById('log-folder');
    btnFolder.onclick = async () => {{
      const path = document.getElementById('folder').value.trim();
      if (!path) {{ alert('Укажите путь к папке'); return; }}
      btnFolder.disabled = true;
      logFolder.style.display = 'block';
      logFolder.textContent = 'Перевод в PDF...';
      const r = await fetch('/api/convert-folder', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ path, recursive: document.getElementById('recursive').checked }})
      }});
      const j = await r.json();
      if (!r.ok) {{
        logFolder.textContent = j.detail || JSON.stringify(j);
        btnFolder.disabled = false;
        return;
      }}
      logFolder.textContent = 'Готово: ' + j.stats.ok + ' файлов, пропущено ' + j.stats.skipped + ', ошибок ' + j.stats.error + '\\n\\n' +
        j.files.map(f => f.status + ' | ' + f.source + ' → ' + (f.pdf || '-')).join('\\n');
      btnFolder.disabled = false;
    }};

    const drop = document.getElementById('drop');
    const input = document.getElementById('file');
    const btnFile = document.getElementById('btn-file');
    let file = null;
    drop.onclick = () => input.click();
    input.onchange = () => {{ file = input.files[0]; btnFile.disabled = !file; drop.textContent = file ? file.name : 'Выберите файл'; }};
    drop.ondragover = e => e.preventDefault();
    drop.ondrop = e => {{ e.preventDefault(); file = e.dataTransfer.files[0]; btnFile.disabled = !file; drop.textContent = file ? file.name : 'Выберите файл'; }};
    btnFile.onclick = async () => {{
      if (!file) return;
      btnFile.disabled = true;
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/convert', {{ method: 'POST', body: fd }});
      if (!r.ok) {{ alert(await r.text()); btnFile.disabled = false; return; }}
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (file.name.replace(/\\.[^.]+$/, '') || 'document') + '.pdf';
      a.click();
      btnFile.disabled = false;
    }};
  </script>
</body>
</html>
"""


@app.post("/api/convert-folder")
async def api_convert_folder(body: FolderRequest):
    try:
        return JSONResponse(convert_folder(body.path, body.recursive))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-folder-form")
async def api_convert_folder_form(path: str = Form(...), recursive: bool = Form(True)):
    """Для вызова из curl / скриптов без JSON."""
    try:
        return JSONResponse(convert_folder(path, recursive))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/convert")
async def api_convert(file: UploadFile = File(...)):
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in SUPPORTED_ALL:
        raise HTTPException(
            status_code=400,
            detail=f"Формат {suffix} не поддерживается. Доступно: {', '.join(sorted(SUPPORTED_ALL))}",
        )

    tmp = Path(tempfile.mkdtemp(prefix="convert_pdf_"))
    try:
        src = tmp / f"{uuid.uuid4().hex}{suffix}"
        src.write_bytes(await file.read())

        if suffix == ".pdf":
            out = tmp / "result.pdf"
            shutil.copy(src, out)
        elif suffix in CAD_EXTENSIONS:
            if not oda_available():
                raise HTTPException(status_code=503, detail="ODAFileConverter не установлен")
            out = tmp / "result.pdf"
            pdf_tmp = convert_cad_to_pdf(str(src))
            shutil.move(str(pdf_tmp), str(out))
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
