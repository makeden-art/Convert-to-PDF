"""Проксирование /calc и /norm на отдельные контейнеры-модули."""
from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from portal.module_services import MODULE_SERVICES
from portal.modules import is_module_enabled
from portal.service_urls import module_base_urls

CALC_URL = os.getenv("CALC_SERVICE_URL", "http://lisp-calc:8000").rstrip("/")
NORM_URL = os.getenv("NORM_SERVICE_URL", "http://norm-control:8000").rstrip("/")
CONVERT_URL = os.getenv("CONVERT_SERVICE_URL", "http://convert-to-pdf:8000").rstrip("/")

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def setup_service_proxies(app: FastAPI) -> None:
    @app.api_route("/calc", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/calc/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    async def proxy_calc(request: Request, path: str = "") -> Response:
        return await _proxy(request, "calc", CALC_URL, path)

    @app.api_route("/process", methods=["POST"], include_in_schema=False)
    async def proxy_calc_process(request: Request) -> Response:
        return await _proxy(request, "calc", CALC_URL, "process")

    @app.api_route("/download/{token}", methods=["GET"], include_in_schema=False)
    async def proxy_calc_download(request: Request, token: str) -> Response:
        return await _proxy(request, "calc", CALC_URL, f"download/{token}")

    @app.api_route("/norm", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/norm/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    async def proxy_norm(request: Request, path: str = "") -> Response:
        return await _proxy(request, "norm", NORM_URL, path)

    @app.api_route("/process_norm", methods=["POST"], include_in_schema=False)
    async def proxy_norm_api(request: Request) -> Response:
        return await _proxy(request, "norm", NORM_URL, "process_norm")

    @app.api_route("/convert", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    @app.api_route("/convert/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"], include_in_schema=False)
    async def proxy_convert(request: Request, path: str = "") -> Response:
        sub = f"convert/{path}" if path else "convert"
        return await _proxy(request, "convert", CONVERT_URL, sub)

    @app.api_route("/api/convert", methods=["POST"], include_in_schema=False)
    async def proxy_convert_api(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert")

    @app.api_route("/api/cad-server-script", methods=["GET"], include_in_schema=False)
    async def proxy_cad_server_script(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/cad-server-script")

    @app.api_route("/api/setup-cad-server", methods=["GET"], include_in_schema=False)
    async def proxy_setup_cad_server(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/setup-cad-server")

    @app.api_route("/api/uninstall-cad-server", methods=["GET"], include_in_schema=False)
    async def proxy_uninstall_cad_server(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/uninstall-cad-server")

    @app.api_route("/api/cad-server-ping", methods=["GET"], include_in_schema=False)
    async def proxy_cad_server_ping(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/cad-server-ping")

    @app.api_route("/api/convert-folder", methods=["POST"], include_in_schema=False)
    async def proxy_convert_folder_api(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-folder")

    @app.api_route("/api/convert-folder-form", methods=["POST"], include_in_schema=False)
    async def proxy_convert_folder_form(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-folder-form")

    @app.api_route("/api/convert-merge", methods=["POST"], include_in_schema=False)
    async def proxy_convert_merge(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-merge")

    @app.api_route("/api/browse", methods=["GET"], include_in_schema=False)
    async def proxy_convert_browse(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/browse")

    @app.api_route("/api/convert-paths", methods=["POST"], include_in_schema=False)
    async def proxy_convert_paths(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-paths")

    @app.api_route("/api/resolve-paths", methods=["POST"], include_in_schema=False)
    async def proxy_resolve_paths(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/resolve-paths")

    @app.api_route("/api/upload-to-smb", methods=["POST"], include_in_schema=False)
    async def proxy_upload_to_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/upload-to-smb")

    @app.api_route("/api/create-folder-smb", methods=["POST"], include_in_schema=False)
    async def proxy_create_folder_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/create-folder-smb")

    @app.api_route("/api/delete-smb", methods=["POST"], include_in_schema=False)
    async def proxy_delete_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/delete-smb")

    @app.api_route("/api/convert-merge-download", methods=["POST"], include_in_schema=False)
    async def proxy_convert_merge_download(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-merge-download")

    @app.api_route("/api/convert-jobs", methods=["GET"], include_in_schema=False)
    async def proxy_convert_jobs_list(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-jobs")

    @app.api_route("/api/convert-jobs/queue", methods=["GET"], include_in_schema=False)
    async def proxy_convert_jobs_queue(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/convert-jobs/queue")

    @app.api_route("/api/convert-jobs/{job_id}/cancel", methods=["POST"], include_in_schema=False)
    async def proxy_convert_jobs_cancel(request: Request, job_id: str) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, f"api/convert-jobs/{job_id}/cancel")

    @app.api_route("/api/convert-jobs/{job_id}", methods=["GET"], include_in_schema=False)
    async def proxy_convert_jobs(request: Request, job_id: str) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, f"api/convert-jobs/{job_id}")

    @app.api_route("/api/check-output", methods=["POST"], include_in_schema=False)
    async def proxy_check_output(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/check-output")

    @app.api_route("/api/detect-frames", methods=["GET"], include_in_schema=False)
    async def proxy_detect_frames(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/detect-frames")

    @app.api_route("/api/preview-info", methods=["GET"], include_in_schema=False)
    async def proxy_preview_info(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/preview-info")

    @app.api_route("/api/preview", methods=["GET"], include_in_schema=False)
    async def proxy_preview(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/preview")

    @app.api_route("/api/view-document", methods=["GET"], include_in_schema=False)
    async def proxy_view_document(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/view-document")

    @app.api_route("/api/create-folder-smb", methods=["POST"], include_in_schema=False)
    async def proxy_create_folder_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/create-folder-smb")

    @app.api_route("/api/upload-to-smb", methods=["POST"], include_in_schema=False)
    async def proxy_upload_to_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/upload-to-smb")

    @app.api_route("/api/delete-smb", methods=["POST"], include_in_schema=False)
    async def proxy_delete_smb(request: Request) -> Response:
        return await _proxy(request, "convert", CONVERT_URL, "api/delete-smb")


async def _proxy(request: Request, module: str, base: str, path: str) -> Response:
    if not is_module_enabled(module):
        raise HTTPException(status_code=404, detail=f"Модуль «{module}» отключён")

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_HEADERS
    }
    timeout = 1800.0 if module == "convert" else 300.0
    bases = module_base_urls(module) or [base.rstrip("/")]
    last_error: Exception | None = None

    for idx, service_base in enumerate(bases):
        target = f"{service_base}/{path}" if path else service_base + request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
            
        client = httpx.AsyncClient(timeout=timeout)
        req = client.build_request(
            request.method,
            target,
            headers=headers,
            content=request.stream(),
        )
        try:
            r = await client.send(req, stream=True)
            
            resp_headers = {
                k: v
                for k, v in r.headers.items()
                if k.lower() not in _HOP_HEADERS
            }
            
            async def cleanup(resp=r, cli=client):
                await resp.aclose()
                await cli.aclose()
                
            return StreamingResponse(
                r.aiter_raw(),
                status_code=r.status_code,
                headers=resp_headers,
                background=BackgroundTask(cleanup)
            )
        except httpx.ConnectError as e:
            await client.aclose()
            last_error = e
            if idx + 1 < len(bases):
                continue
        except httpx.HTTPError as e:
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"Ошибка сервиса {module}: {e}") from e

    service = MODULE_SERVICES.get(module, module)
    raise HTTPException(
        status_code=503,
        detail=f"Сервис «{module}» не запущен. Включите модуль на /services (контейнер {service}).",
    ) from last_error
