"""Convert-to-PDF — конвертация редактируемых форматов в PDF."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from cad_converter import CAD_EXTENSIONS, convert_cad_to_pdf, inspect_cad_frames, oda_available
from convert_jobs import cancel_job, create_job, get_job, list_jobs, queue_status
from file_preview import (
    preview_info,
    preview_timeout_sec,
    render_preview_png,
    resolve_view_document,
    view_document_timeout_sec,
)
from converter import (
    SUPPORTED_OFFICE,
    SUPPORTED_CAD,
    SUPPORTED_ALL,
    allowed_roots,
    browse_directory,
    check_output_files,
    convert_folder,
    convert_paths,
    convert_paths_merged_download,
    convert_uploads_to_merged_pdf,
    number_pdf_file,
    resolve_ordered_inputs,
    resolve_ordered_inputs_with_format,
    validate_file,
    _validate_file_format_or_raise,
    _convert_with_libreoffice,
)

MAX_MERGE_FILES = int(os.getenv("CONVERT_MAX_MERGE_FILES", "50"))

app = FastAPI(title="Перевод в PDF", version="0.5.0")


def _version() -> str:
    p = Path(__file__).parent / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0.0.0"


class FolderRequest(BaseModel):
    windows_cad_ip: str = ""

    path: str
    recursive: bool = True
    merge: bool = False
    output_name: str = "сборка.pdf"
    number_pages: bool = False
    numbering_from_page: int = 1
    numbering_start: int = 1
    color_mode: str = "color"


class PathsRequest(BaseModel):
    windows_cad_ip: str = ""

    paths: list[str]
    merge: bool = False
    output_name: str = "сборка.pdf"
    recursive: bool = True
    number_pages: bool = False
    numbering_from_page: int = 1
    numbering_start: int = 1
    color_mode: str = "color"


class ResolveRequest(BaseModel):
    paths: list[str]
    recursive: bool = True


class CheckOutputRequest(BaseModel):
    paths: list[str] | None = None
    folder_path: str | None = None
    merge: bool = False
    output_name: str = "сборка.pdf"
    recursive: bool = True


class NumberPdfRequest(BaseModel):
    path: str
    numbering_from_page: int = 1
    numbering_start: int = 1


def _paths_job_label(body: PathsRequest) -> str:
    n = len(body.paths)
    if body.merge:
        return f"Сборка «{body.output_name}» ({n} выб.)"
    return f"Конвертация ({n} " + ("путь" if n == 1 else "путей") + ")"


def _folder_job_label(body: FolderRequest) -> str:
    name = Path(body.path).name or body.path
    if body.merge:
        return f"Сборка «{body.output_name}» — {name}"
    return f"Папка: {name}"


def _preview_job_files(paths: list[str], *, recursive: bool = True, limit: int = 500) -> dict[str, Any]:
    """Список файлов задания для UI (до старта конвертации)."""
    files = resolve_ordered_inputs_with_format(paths, recursive=recursive)
    truncated = len(files) > limit
    items = []
    for row in files[:limit]:
        items.append(
            {
                "path": row["path"],
                "name": row["name"],
                "format": row.get("format_label") or row.get("format") or "",
            }
        )
    return {
        "files": items,
        "files_count": len(files),
        "truncated": truncated,
        "selection": list(paths),
    }


def _paths_job_meta(body: PathsRequest) -> dict[str, Any]:
    preview = _preview_job_files(body.paths, recursive=body.recursive)
    return {
        "merge": body.merge,
        "paths_count": len(body.paths),
        "output_name": body.output_name,
        "recursive": body.recursive,
        **preview,
    }


def _folder_job_meta(body: FolderRequest) -> dict[str, Any]:
    preview = _preview_job_files([body.path], recursive=body.recursive)
    return {
        "path": body.path,
        "merge": body.merge,
        "output_name": body.output_name,
        "recursive": body.recursive,
        **preview,
    }


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


@app.get("/version")
async def version():
    return {"version": _version(), "service": "convert-to-pdf"}


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


@app.get("/convert/view", response_class=HTMLResponse)
async def viewer_page() -> str:
    template_path = Path(__file__).parent / "viewer_page.html"
    return template_path.read_text(encoding="utf-8")


@app.get("/api/browse")
async def api_browse(path: str = ""):
    try:
        return JSONResponse(browse_directory(path))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/check-output")
async def api_check_output(body: CheckOutputRequest):
    try:
        return JSONResponse(
            check_output_files(
                paths=body.paths,
                folder_path=body.folder_path,
                merge=body.merge,
                output_name=body.output_name,
                recursive=body.recursive,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/resolve-paths")
async def api_resolve_paths(body: ResolveRequest):
    try:
        files = await asyncio.to_thread(
            resolve_ordered_inputs_with_format, body.paths, recursive=body.recursive
        )
        return JSONResponse({"files": files})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/detect-frames")
async def api_detect_frames(path: str):
    try:
        file_path = validate_file(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    suffix = file_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Только DWG/DXF")
    if suffix == ".dwg":
        raise HTTPException(
            status_code=400,
            detail="Поиск рамок для DWG отключен во избежание фоновой нагрузки на процессор."
        )
    try:
        from converter import _is_smb_path, _smb_local_file, _smb_mounted

        if _is_smb_path(file_path) and _smb_mounted():
            def work():
                with _smb_local_file(file_path) as local:
                    return inspect_cad_frames(str(local))

            result = await asyncio.to_thread(work)
            result["path"] = str(file_path)
        else:
            result = await asyncio.to_thread(inspect_cad_frames, str(file_path))
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/preview-info")
async def api_preview_info(path: str):
    try:
        result = await asyncio.to_thread(preview_info, path)
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/preview")
async def api_preview(path: str, page: int = 1, variant: str = "source"):
    try:
        timeout = preview_timeout_sec(path, variant)
        png, meta = await asyncio.wait_for(
            asyncio.to_thread(
                render_preview_png, path, page=max(1, page), variant=variant
            ),
            timeout=timeout,
        )
        return Response(
            content=png,
            media_type="image/png",
            headers={"X-Preview-Pages": str(meta.get("pages", 1))},
        )
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise HTTPException(
            status_code=504,
            detail="Превышено время ожидания предпросмотра. Попробуйте PDF рядом или выполните конвертацию.",
        ) from e
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/view-document")
async def api_view_document(path: str, variant: str = "source"):
    tmp_parent: Path | None = None
    try:
        timeout = view_document_timeout_sec(path, variant)
        pdf_path, tmp_parent = await asyncio.wait_for(
            asyncio.to_thread(resolve_view_document, path, variant),
            timeout=timeout,
        )
        filename = pdf_path.name or "document.pdf"
        cleanup_dir = tmp_parent
        safe_ascii = "document.pdf"
        disposition = f"inline; filename=\"{safe_ascii}\"; filename*=UTF-8''{quote(filename)}"

        def _cleanup() -> None:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            headers={"Content-Disposition": disposition},
            background=BackgroundTask(_cleanup),
        )
    except asyncio.TimeoutError as e:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        raise HTTPException(
            status_code=504,
            detail="Превышено время ожидания подготовки документа для просмотра.",
        ) from e
    except ValueError as e:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/convert-jobs")
async def api_convert_jobs_list(limit: int = 50):
    return JSONResponse({"jobs": list_jobs(limit=min(limit, 100)), "queue": queue_status()})


@app.get("/api/convert-jobs/queue")
async def api_convert_queue():
    return JSONResponse(queue_status())


@app.post("/api/convert-jobs/{job_id}/cancel")
async def api_convert_job_cancel(job_id: str):
    result = await asyncio.to_thread(cancel_job, job_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не удалось отменить"))
    return JSONResponse(result)


@app.get("/api/convert-jobs/{job_id}")
async def api_convert_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return JSONResponse(job)


@app.post("/api/number-pdf")
async def api_number_pdf(body: NumberPdfRequest):
    try:
        job_id = create_job(
            lambda: number_pdf_file(
                body.path,
                numbering_from_page=body.numbering_from_page,
                numbering_start=body.numbering_start,
            ),
            label=f"Нумерация: {Path(body.path).name}",
            kind="number_pdf",
            meta={
                "path": body.path,
                "numbering_from_page": body.numbering_from_page,
                "numbering_start": body.numbering_start,
                "files": [{"path": body.path, "name": Path(body.path).name, "format": "PDF"}]
            },
        )
        job = get_job(job_id) or {}
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "queue_position": job.get("queue_position", 1),
            },
            status_code=202,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-paths")
async def api_convert_paths(body: PathsRequest):
    try:
        job_id = create_job(
            lambda: convert_paths(
                body.paths,
                merge=body.merge,
                output_name=body.output_name,
                recursive=body.recursive,
                number_pages=body.number_pages,
                numbering_from_page=body.numbering_from_page,
                numbering_start=body.numbering_start,
                windows_cad_ip=body.windows_cad_ip,
            ),
            label=_paths_job_label(body),
            kind="convert_paths",
            meta=_paths_job_meta(body),
        )
        job = get_job(job_id) or {}
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "queue_position": job.get("queue_position", 1),
            },
            status_code=202,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-merge-download")
async def api_convert_merge_download(body: PathsRequest):
    """Сборка PDF с сервера (SMB) и отдача файла для скачивания."""
    if not body.paths:
        raise HTTPException(status_code=400, detail="Укажите файлы для сборки")
    tmp_parent: Path | None = None
    try:
        from_page = body.numbering_from_page if body.number_pages else None
        start_num = body.numbering_start if body.number_pages else 1
        dest, tmp_parent, _ = await asyncio.to_thread(
            convert_paths_merged_download,
            body.paths,
            body.output_name,
            recursive=body.recursive,
            numbering_from_page=from_page,
            numbering_start=start_num,
            windows_cad_ip=body.windows_cad_ip,
        )
        filename = Path(body.output_name).name or "сборка.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        return FileResponse(
            path=str(dest),
            media_type="application/pdf",
            filename=filename,
            background=BackgroundTask(lambda: shutil.rmtree(tmp_parent, ignore_errors=True)),
        )
    except ValueError as e:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/convert-folder")
async def api_convert_folder(body: FolderRequest):
    try:
        job_id = create_job(
            lambda: convert_folder(
                body.path,
                body.recursive,
                merge=body.merge,
                output_name=body.output_name,
                number_pages=body.number_pages,
                numbering_from_page=body.numbering_from_page,
                numbering_start=body.numbering_start,
                windows_cad_ip=body.windows_cad_ip,
            ),
            label=_folder_job_label(body),
            kind="convert_folder",
            meta=_folder_job_meta(body),
        )
        job = get_job(job_id) or {}
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "queue_position": job.get("queue_position", 1),
            },
            status_code=202,
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
    color_mode: str = Form("color"),
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
            _validate_file_format_or_raise(dest)
            sources.append(dest)

        out = tmp / "сборка.pdf"
        from_page = numbering_from_page if number_pages else None
        start_num = numbering_start if number_pages else 1
        await asyncio.to_thread(
            convert_uploads_to_merged_pdf,
            sources,
            out,
            numbering_from_page=from_page,
            numbering_start=start_num,
            color_mode=color_mode,
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
async def api_convert(
    file: UploadFile = File(...),
    color_mode: str = Form("color"),
):
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
        _validate_file_format_or_raise(src)

        if suffix == ".pdf":
            out = tmp / "result.pdf"
            shutil.copy(src, out)
        elif suffix in CAD_EXTENSIONS:
            if not oda_available():
                raise HTTPException(status_code=503, detail="ODAFileConverter не установлен")
            out = tmp / "result.pdf"
            pdf_tmp, _cad_meta = convert_cad_to_pdf(str(src), meta={"color_mode": color_mode})
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


@app.get("/api/cad-server-ping")
async def ping_cad_server(ip: str):
    import httpx
    if not ip:
        raise HTTPException(status_code=400, detail="No IP")
    if not ip.startswith("http"):
        ip = "http://" + ip
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(ip)
            return {"status": "ok", "code": resp.status_code}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/cad-server-script")
async def download_cad_server():
    return FileResponse("windows_cad_server.py", media_type="text/x-python", filename="windows_cad_server.py")
