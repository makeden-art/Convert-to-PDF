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
    browse_directory,
    convert_folder,
    convert_paths,
    convert_uploads_to_merged_pdf,
    resolve_ordered_inputs,
    _convert_with_libreoffice,
)

MAX_MERGE_FILES = int(os.getenv("CONVERT_MAX_MERGE_FILES", "50"))

app = FastAPI(title="Перевод в PDF", version="0.5.0")


def _version() -> str:
    p = Path(__file__).parent / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


class FolderRequest(BaseModel):
    path: str
    recursive: bool = True
    merge: bool = False
    output_name: str = "сборка.pdf"
    number_pages: bool = False
    numbering_from_page: int = 1
    numbering_start: int = 1


class PathsRequest(BaseModel):
    paths: list[str]
    merge: bool = False
    output_name: str = "сборка.pdf"
    recursive: bool = True
    number_pages: bool = False
    numbering_from_page: int = 1
    numbering_start: int = 1


class ResolveRequest(BaseModel):
    paths: list[str]
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
    template_path = Path(__file__).parent / "convert_page.html"
    html = template_path.read_text(encoding="utf-8")
    return (
        html.replace("{{MAX_MERGE}}", str(MAX_MERGE_FILES))
        .replace("{{ROOTS}}", ", ".join(str(r) for r in allowed_roots()))
        .replace("{{VERSION}}", _version())
    )


@app.get("/api/browse")
async def api_browse(path: str = ""):
    try:
        return JSONResponse(browse_directory(path))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/resolve-paths")
async def api_resolve_paths(body: ResolveRequest):
    try:
        files = resolve_ordered_inputs(body.paths, recursive=body.recursive)
        return JSONResponse(
            {
                "files": [
                    {"path": str(f), "name": f.name, "parent": f.parent.name}
                    for f in files
                ]
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/convert-paths")
async def api_convert_paths(body: PathsRequest):
    try:
        return JSONResponse(
            convert_paths(
                body.paths,
                merge=body.merge,
                output_name=body.output_name,
                recursive=body.recursive,
                number_pages=body.number_pages,
                numbering_from_page=body.numbering_from_page,
                numbering_start=body.numbering_start,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-folder")
async def api_convert_folder(body: FolderRequest):
    try:
        return JSONResponse(
            convert_folder(
                body.path,
                body.recursive,
                merge=body.merge,
                output_name=body.output_name,
                number_pages=body.number_pages,
                numbering_from_page=body.numbering_from_page,
                numbering_start=body.numbering_start,
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
    number_pages: bool = Form(False),
    numbering_from_page: int = Form(1),
    numbering_start: int = Form(1),
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
        from_page = numbering_from_page if number_pages else None
        start_num = numbering_start if number_pages else 1
        convert_uploads_to_merged_pdf(
            sources, out, numbering_from_page=from_page, numbering_start=start_num
        )
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
