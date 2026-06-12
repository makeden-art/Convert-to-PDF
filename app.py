"""Convert-to-PDF — конвертация редактируемых форматов в PDF."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

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
    convert_folder,
    convert_uploads_to_merged_pdf,
    _convert_with_libreoffice,
)

MAX_MERGE_FILES = int(os.getenv("CONVERT_MAX_MERGE_FILES", "50"))
SUPPORTED_FORMATS_LABEL = "DOC, DOCX, XLS, XLSX, ODT, ODS, RTF, DWG, DXF, PDF"

app = FastAPI(title="Перевод в PDF", version="0.4.1")


def _version() -> str:
    p = Path(__file__).parent / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


class FolderRequest(BaseModel):
    path: str
    recursive: bool = True
    merge: bool = False
    output_name: str = "сборка.pdf"


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
    input[type=text], input[type=password] {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(148,163,184,.4); background: #0f172a; color: #e5e7eb; margin: 8px 0 12px; }}
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
      <p><b>Поддерживаются:</b> {SUPPORTED_FORMATS_LABEL}.</p>
      <p>По отдельности — PDF <b>рядом</b> с оригиналом. До <b>{MAX_MERGE_FILES}</b> файлов — <b>сборка в один PDF</b>.</p>
      <p>Локальная папка: <code>/data/documents</code>. Сетевая (SMB): <code>/data/smb/default</code> после подключения ниже.</p>
      <p class="ver">Каталоги на сервере: <code>{roots}</code></p>
    </div>

    <div class="box">
      <h2>🌐 Сетевая папка (SMB)</h2>
      <p>Подключение с сервера портала. Логин/пароль или анонимный гостевой доступ.</p>
      <p id="smb-status" class="ver">Статус: проверка…</p>
      <label for="smb-server">Сервер</label>
      <input type="text" id="smb-server" placeholder="192.168.88.50" />
      <label for="smb-share">Имя шары</label>
      <input type="text" id="smb-share" placeholder="Projects" />
      <label for="smb-user">Логин (если не аноним)</label>
      <input type="text" id="smb-user" placeholder="user" />
      <label for="smb-pass">Пароль</label>
      <input type="password" id="smb-pass" placeholder="••••••" />
      <div class="chk">
        <input type="checkbox" id="smb-anon" />
        <label for="smb-anon" style="margin:0">Анонимный доступ (guest)</label>
      </div>
      <button id="btn-smb-mount" type="button">Подключить SMB</button>
      <button id="btn-smb-unmount" type="button" style="margin-left:8px;background:#64748b">Отключить</button>
      <pre id="log-smb" style="display:none"></pre>
    </div>

    <div class="box">
      <h2>📁 Папка целиком</h2>
      <label for="folder">Путь к папке на сервере</label>
      <input type="text" id="folder" placeholder="/data/documents/комплект_1" />
      <div class="chk">
        <input type="checkbox" id="recursive" checked />
        <label for="recursive" style="margin:0">Включая вложенные подпапки</label>
      </div>
      <div class="chk">
        <input type="checkbox" id="merge-folder" />
        <label for="merge-folder" style="margin:0">Собрать всё в один PDF</label>
      </div>
      <label for="output-name">Имя итогового файла (при сборке)</label>
      <input type="text" id="output-name" value="сборка.pdf" />
      <button id="btn-folder">Конвертировать папку</button>
      <pre id="log-folder" style="display:none"></pre>
    </div>

    <div class="box">
      <h2>📚 Несколько файлов → один PDF</h2>
      <div class="drop" id="drop-multi">Выберите файлы (до {MAX_MERGE_FILES}) или перетащите</div>
      <input type="file" id="files-multi" style="display:none" multiple accept=".pdf,.doc,.docx,.xls,.xlsx,.odt,.ods,.rtf,.dwg,.dxf" />
      <button id="btn-multi" disabled>Скачать сборку PDF</button>
      <pre id="log-multi" style="display:none"></pre>
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
    const logSmb = document.getElementById('log-smb');
    const smbStatus = document.getElementById('smb-status');
    async function refreshSmb() {{
      try {{
        const r = await fetch('/api/platform/smb/status');
        const j = await r.json();
        if (j.mounted && j.convert_path) {{
          smbStatus.textContent = 'SMB: подключено → ' + j.convert_path + ' (' + (j.mount?.unc || '') + ')';
          document.getElementById('folder').placeholder = j.convert_path + '/подпапка';
        }} else if (j.configured) {{
          smbStatus.textContent = 'SMB: настроено, но не смонтировано';
        }} else {{
          smbStatus.textContent = 'SMB: не подключено';
        }}
      }} catch (e) {{
        smbStatus.textContent = 'SMB: статус недоступен';
      }}
    }}
    refreshSmb();
    document.getElementById('btn-smb-mount').onclick = async () => {{
      const server = document.getElementById('smb-server').value.trim();
      const share = document.getElementById('smb-share').value.trim();
      if (!server || !share) {{ alert('Укажите сервер и имя шары'); return; }}
      logSmb.style.display = 'block';
      logSmb.textContent = 'Подключение SMB...';
      const r = await fetch('/api/platform/smb/mount', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          server, share,
          username: document.getElementById('smb-user').value,
          password: document.getElementById('smb-pass').value,
          anonymous: document.getElementById('smb-anon').checked,
          mount_id: 'default'
        }})
      }});
      const j = await r.json();
      if (!r.ok) {{
        logSmb.textContent = j.detail || JSON.stringify(j);
        return;
      }}
      logSmb.textContent = 'Подключено: ' + j.mount.convert_path;
      refreshSmb();
    }};
    document.getElementById('btn-smb-unmount').onclick = async () => {{
      logSmb.style.display = 'block';
      logSmb.textContent = 'Отключение...';
      const r = await fetch('/api/platform/smb/unmount', {{ method: 'POST' }});
      const j = await r.json();
      logSmb.textContent = r.ok ? 'Отключено' : (j.detail || JSON.stringify(j));
      refreshSmb();
    }};

    const btnFolder = document.getElementById('btn-folder');
    const logFolder = document.getElementById('log-folder');
    btnFolder.onclick = async () => {{
      const path = document.getElementById('folder').value.trim();
      if (!path) {{ alert('Укажите путь к папке'); return; }}
      btnFolder.disabled = true;
      logFolder.style.display = 'block';
      logFolder.textContent = 'Перевод в PDF...';
      const merge = document.getElementById('merge-folder').checked;
      const output_name = document.getElementById('output-name').value.trim() || 'сборка.pdf';
      const r = await fetch('/api/convert-folder', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ path, recursive: document.getElementById('recursive').checked, merge, output_name }})
      }});
      const j = await r.json();
      if (!r.ok) {{
        logFolder.textContent = j.detail || JSON.stringify(j);
        btnFolder.disabled = false;
        return;
      }}
      if (j.merge) {{
        logFolder.textContent = 'Сборка: ' + j.merged_pdf + '\\nФайлов в PDF: ' + j.pages_from +
          ', ошибок: ' + j.stats.error + '\\n\\n' +
          j.files.map(f => f.status + ' | ' + f.source).join('\\n');
      }} else {{
        logFolder.textContent = 'Готово: ' + j.stats.ok + ' файлов, пропущено ' + j.stats.skipped + ', ошибок ' + j.stats.error + '\\n\\n' +
          j.files.map(f => f.status + ' | ' + f.source + ' → ' + (f.pdf || '-')).join('\\n');
      }}
      btnFolder.disabled = false;
    }};

    const dropMulti = document.getElementById('drop-multi');
    const inputMulti = document.getElementById('files-multi');
    const btnMulti = document.getElementById('btn-multi');
    const logMulti = document.getElementById('log-multi');
    let filesMulti = [];
    const maxMerge = {MAX_MERGE_FILES};
    const setMultiLabel = () => {{
      dropMulti.textContent = filesMulti.length
        ? ('Выбрано файлов: ' + filesMulti.length)
        : ('Выберите файлы (до ' + maxMerge + ') или перетащите');
      btnMulti.disabled = filesMulti.length === 0;
    }};
    dropMulti.onclick = () => inputMulti.click();
    inputMulti.onchange = () => {{ filesMulti = Array.from(inputMulti.files || []); setMultiLabel(); }};
    dropMulti.ondragover = e => e.preventDefault();
    dropMulti.ondrop = e => {{
      e.preventDefault();
      filesMulti = Array.from(e.dataTransfer.files || []);
      setMultiLabel();
    }};
    btnMulti.onclick = async () => {{
      if (!filesMulti.length) return;
      if (filesMulti.length > maxMerge) {{ alert('Максимум ' + maxMerge + ' файлов'); return; }}
      btnMulti.disabled = true;
      logMulti.style.display = 'block';
      logMulti.textContent = 'Сборка PDF...';
      const fd = new FormData();
      filesMulti.forEach(f => fd.append('files', f));
      const r = await fetch('/api/convert-merge', {{ method: 'POST', body: fd }});
      if (!r.ok) {{
        logMulti.textContent = await r.text();
        btnMulti.disabled = false;
        return;
      }}
      const blob = await r.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'сборка.pdf';
      a.click();
      logMulti.textContent = 'Готово, файл скачан';
      btnMulti.disabled = false;
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
        return JSONResponse(
            convert_folder(
                body.path,
                body.recursive,
                merge=body.merge,
                output_name=body.output_name,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-folder-form")
async def api_convert_folder_form(
    path: str = Form(...),
    recursive: bool = Form(True),
    merge: bool = Form(False),
    output_name: str = Form("сборка.pdf"),
):
    """Для вызова из curl / скриптов без JSON."""
    try:
        return JSONResponse(convert_folder(path, recursive, merge=merge, output_name=output_name))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/convert-merge")
async def api_convert_merge(
    files: Annotated[list[UploadFile], File(...)],
):
    if not files:
        raise HTTPException(status_code=400, detail="Передайте хотя бы один файл")
    if len(files) > MAX_MERGE_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Слишком много файлов (макс. {MAX_MERGE_FILES})",
        )

    tmp = Path(tempfile.mkdtemp(prefix="convert_merge_"))
    try:
        sources: list[Path] = []
        for uf in files:
            suffix = Path(uf.filename or "upload").suffix.lower()
            if suffix not in SUPPORTED_ALL:
                raise HTTPException(
                    status_code=400,
                    detail=f"Формат {suffix} не поддерживается ({uf.filename})",
                )
            dest = tmp / f"{len(sources):04d}_{Path(uf.filename or 'upload').name}"
            dest.write_bytes(await uf.read())
            sources.append(dest)

        out = tmp / "сборка.pdf"
        convert_uploads_to_merged_pdf(sources, out)
        return FileResponse(
            path=str(out),
            media_type="application/pdf",
            filename="сборка.pdf",
            background=BackgroundTask(lambda: shutil.rmtree(tmp, ignore_errors=True)),
        )
    except HTTPException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


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
