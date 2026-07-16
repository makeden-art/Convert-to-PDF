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

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from cad_converter import CAD_EXTENSIONS, convert_cad_to_pdf
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
    _smb_put_file,
    _smb_mkdir,
    _smb_delete,
    _is_smb_path,
    _smb_mounted,
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
    windows_cad_ip: str = ""


class PathsRequest(BaseModel):
    windows_cad_ip: str = ""

    paths: list[str]
    merge: bool = False
    output_name: str = "сборка.pdf"
    recursive: bool = True
    number_pages: bool = False
    numbering_from_page: int = 1
    numbering_start: int = 1
    windows_cad_ip: str = ""


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
        "cad_support": True,
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
async def convert_page():
    template_path = Path(__file__).parent / "convert_page.html"
    html = template_path.read_text(encoding="utf-8")
    content = (
        html.replace("{{MAX_MERGE}}", str(MAX_MERGE_FILES))
        .replace("{{ROOTS}}", ", ".join(str(r) for r in allowed_roots()))
        .replace("{{VERSION}}", _version())
    )
    return HTMLResponse(content, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/convert/view", response_class=HTMLResponse)
async def viewer_page():
    template_path = Path(__file__).parent / "viewer_page.html"
    content = template_path.read_text(encoding="utf-8")
    return HTMLResponse(content, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


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


@app.post("/api/create-folder-smb")
async def api_create_folder_smb(target_dir: str = Form(...), folder_name: str = Form(...)):
    """Создает новую папку на SMB."""
    if not _is_smb_path(Path(target_dir)):
        raise HTTPException(status_code=400, detail="Только для SMB-шар")
    if not _smb_mounted():
        raise HTTPException(status_code=500, detail="SMB шара не примонтирована")
    
    target_path = Path(target_dir)
    new_folder = target_path / folder_name
    try:
        await asyncio.to_thread(_smb_mkdir, new_folder)
        return {"status": "ok", "path": str(new_folder)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from fastapi.responses import JSONResponse
import traceback

@app.post("/api/delete-smb")
async def api_delete_smb(request: Request):
    """Удаляет файлы или папки на SMB."""
    try:
        form = await request.form()
        paths = form.getlist('paths')
        is_dirs_str = form.getlist('is_dirs')
        is_dirs = [s.lower() == 'true' for s in is_dirs_str]
        
        if not _smb_mounted():
            return JSONResponse(status_code=500, content={"detail": "SMB шара не примонтирована"})
        
        deleted = []
        errors = []
        for path_str, is_dir in zip(paths, is_dirs):
            if not _is_smb_path(Path(path_str)):
                errors.append({"path": path_str, "error": "Не SMB путь"})
                continue
            try:
                await asyncio.to_thread(_smb_delete, Path(path_str), is_dir)
                deleted.append(path_str)
            except Exception as e:
                errors.append({"path": path_str, "error": str(e)})
                
        if errors and not deleted:
            return JSONResponse(status_code=500, content={"detail": f"Ошибки при удалении: {errors}"})
        return {"status": "ok", "deleted": deleted, "errors": errors}
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": traceback.format_exc()})

@app.post("/api/upload-to-smb")
async def api_upload_to_smb(
    target_dir: str = Form(...),
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...)
):
    if not files or not paths or len(files) != len(paths):
        raise HTTPException(status_code=400, detail="Invalid files or paths")
        
    target_path = Path(target_dir)
    is_smb = _is_smb_path(target_path) and _smb_mounted()
    
    tmp = Path(tempfile.mkdtemp(prefix="upload_smb_"))
    try:
        created_dirs = set()
        for i, uf in enumerate(files):
            rel_path = paths[i]
            # Защита от выхода за пределы папки
            rel_path = rel_path.lstrip("/\\")
            if ".." in rel_path:
                continue
                
            local_dest = tmp / rel_path
            local_dest.parent.mkdir(parents=True, exist_ok=True)
            local_dest.write_bytes(await uf.read())
            
            final_target = target_path / rel_path
            if is_smb:
                # Если папка новая, нужно создать ее на SMB
                parent_dir = final_target.parent
                if str(parent_dir) not in created_dirs and parent_dir != target_path:
                    # Создаем все родительские папки
                    parts = rel_path.split("/")[:-1]
                    cur = target_path
                    for p in parts:
                        cur = cur / p
                        if str(cur) not in created_dirs:
                            try:
                                await asyncio.to_thread(_smb_mkdir, cur)
                            except Exception:
                                pass
                            created_dirs.add(str(cur))
                
                await asyncio.to_thread(_smb_put_file, local_dest, final_target)
            else:
                final_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_dest, final_target)
                
        return {"status": "ok", "count": len(files)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
    windows_cad_ip: str = Form(""),
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
            windows_cad_ip=windows_cad_ip,
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
    windows_cad_ip: str = Form(""),
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
            out = tmp / "result.pdf"
            pdf_tmp, _cad_meta = convert_cad_to_pdf(str(src), meta={"windows_cad_ip": windows_cad_ip})
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
    import urllib.request
    if not ip:
        raise HTTPException(status_code=400, detail="No IP")
    if not ip.startswith("http"):
        ip = "http://" + ip
    try:
        req = urllib.request.Request(ip, method="GET")
        with urllib.request.urlopen(req, timeout=3) as response:
            return {"status": "ok", "code": response.status}
    except urllib.error.HTTPError as e:
        return {"status": "ok", "code": e.code}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/cad-server-script")
async def download_cad_server():
    return FileResponse("windows_cad_server.py", media_type="text/x-python", filename="windows_cad_server.py")

@app.get("/api/setup-cad-server")
async def download_setup_cad_server():
    return FileResponse("setup_cad_server.ps1", media_type="application/octet-stream", filename="setup_cad_server.ps1")

@app.get("/api/uninstall-cad-server")
async def download_uninstall_cad_server():
    return FileResponse("uninstall_cad_server.ps1", media_type="application/octet-stream", filename="uninstall_cad_server.ps1")

@app.get("/api/install-cad-service")
async def download_install_cad_service():
    return FileResponse("install_cad_service.ps1", media_type="application/octet-stream", filename="install_cad_service.ps1")

