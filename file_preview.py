"""Предпросмотр файлов перед конвертацией (PNG)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import fitz

from cad_converter import CAD_EXTENSIONS
from job_control import _terminate_process_tree
from converter import (
    SUPPORTED_ALL,
    SUPPORTED_OFFICE,
    _convert_with_libreoffice,
    _is_smb_path,
    _smb_local_file,
    _smb_mounted,
    server_path_exists,
    validate_file,
)

PREVIEW_MAX_PAGES = max(1, int(os.getenv("CONVERT_PREVIEW_MAX_PAGES", "50")))
PREVIEW_PDF_SCALE = max(0.5, min(3.0, float(os.getenv("CONVERT_PREVIEW_PDF_SCALE", "1.5"))))
PREVIEW_CAD_TIMEOUT_SEC = int(os.getenv("CONVERT_PREVIEW_CAD_TIMEOUT_SEC", "600"))
PREVIEW_OFFICE_TIMEOUT_SEC = int(os.getenv("CONVERT_PREVIEW_OFFICE_TIMEOUT_SEC", "90"))
PREVIEW_SOURCE_USE_SIBLING_PDF = os.getenv("CONVERT_PREVIEW_SOURCE_USE_SIBLING_PDF", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
_preview_lock = threading.Lock()
_CAD_PREVIEW_WORKER = Path(__file__).with_name("cad_preview_worker.py")


def _render_cad_preview_subprocess(local_path: Path, page: int) -> tuple[bytes, dict[str, Any]]:
    """CAD-предпросмотр в отдельном процессе — при таймауте процесс убивается."""
    with tempfile.TemporaryDirectory(prefix="preview_cad_") as tmp_dir:
        tmp = Path(tmp_dir)
        out_png = tmp / "preview.png"
        out_meta = tmp / "preview.json"
        cmd = [
            sys.executable,
            str(_CAD_PREVIEW_WORKER),
            str(local_path),
            str(max(1, page)),
            str(out_png),
            str(out_meta),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=PREVIEW_CAD_TIMEOUT_SEC)
        except subprocess.TimeoutExpired as e:
            _terminate_process_tree(proc)
            raise TimeoutError(
                "Превышено время ожидания рендера чертежа. "
                "Попробуйте «PDF рядом» или выполните конвертацию."
            ) from e

        if proc.returncode != 0:
            detail = ""
            if out_meta.exists():
                try:
                    detail = json.loads(out_meta.read_text(encoding="utf-8")).get("error", "")
                except Exception:
                    detail = ""
            if not detail:
                detail = (stderr or stdout or "").strip()
            raise RuntimeError(detail or f"CAD preview failed (code {proc.returncode})")

        if not out_png.exists() or not out_meta.exists():
            raise RuntimeError("CAD preview did not produce output")

        meta = json.loads(out_meta.read_text(encoding="utf-8"))
        return out_png.read_bytes(), meta


def _pdf_sibling(source: Path) -> Path | None:
    if source.suffix.lower() == ".pdf":
        return None
    sibling = source.with_suffix(".pdf")
    if sibling == source:
        return None
    return sibling if server_path_exists(sibling) else None


@contextmanager
def _with_local_path(virtual_path: Path) -> Iterator[Path]:
    if _is_smb_path(virtual_path) and _smb_mounted():
        with _smb_local_file(virtual_path) as local:
            yield local
    else:
        yield virtual_path


def _count_pdf_pages(local_path: Path) -> int:
    doc = fitz.open(str(local_path))
    try:
        return min(len(doc), PREVIEW_MAX_PAGES)
    finally:
        doc.close()


def _render_pdf_page(local_path: Path, page: int) -> tuple[bytes, int]:
    doc = fitz.open(str(local_path))
    try:
        total = min(len(doc), PREVIEW_MAX_PAGES)
        if total == 0:
            raise ValueError("PDF пуст")
        page_idx = max(1, min(page, total)) - 1
        pg = doc[page_idx]
        mat = fitz.Matrix(PREVIEW_PDF_SCALE, PREVIEW_PDF_SCALE)
        pix = pg.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png"), total
    finally:
        doc.close()


def _render_office_preview(local_path: Path, page: int) -> tuple[bytes, int]:
    tmp = Path(tempfile.mkdtemp(prefix="preview_office_"))
    try:
        pdf = _convert_with_libreoffice(local_path, tmp)
        return _render_pdf_page(pdf, page)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def preview_info(path: str) -> dict[str, Any]:
    """Метаданные предпросмотра для UI."""
    file_path = validate_file(path)
    suffix = file_path.suffix.lower()
    sibling = _pdf_sibling(file_path)

    info: dict[str, Any] = {
        "path": str(file_path),
        "name": file_path.name,
        "format": suffix.lstrip("."),
        "previewable": suffix in SUPPORTED_ALL,
        "pages": 1,
        "variants": [],
    }

    if suffix == ".pdf":
        info["variants"].append({"id": "source", "label": "PDF", "path": str(file_path)})
    elif suffix in SUPPORTED_ALL:
        if sibling and suffix in CAD_EXTENSIONS:
            info["variants"].append({"id": "pdf", "label": "PDF", "path": str(sibling)})
        else:
            info["variants"].append({"id": "source", "label": "Исходник", "path": str(file_path)})
            if sibling:
                info["variants"].append({"id": "pdf", "label": "PDF рядом", "path": str(sibling)})

    if suffix == ".pdf" or sibling:
        info["viewer_mode"] = "pdf"
        info.setdefault("default_variant", "pdf" if sibling and suffix != ".pdf" else "source")
    elif suffix in CAD_EXTENSIONS:
        info["viewer_mode"] = "image"
    elif suffix in SUPPORTED_OFFICE:
        info["viewer_mode"] = "pdf"
    else:
        info["viewer_mode"] = "pdf"

    if not info["previewable"]:
        info["message"] = "Формат не поддерживается для предпросмотра"
        return info

    try:
        if suffix == ".pdf":
            with _with_local_path(file_path) as local:
                info["pages"] = _count_pdf_pages(local)
        elif suffix in CAD_EXTENSIONS:
            sibling_pdf = _pdf_sibling(file_path)
            if sibling_pdf:
                with _with_local_path(sibling_pdf) as local:
                    info["pages"] = _count_pdf_pages(local)
                info["default_variant"] = "pdf"
                info["viewer_mode"] = "pdf"
                info["render_timeout_sec"] = 45
            else:
                info["viewer_mode"] = "image"
                info["pages_unknown"] = True
                info["render_timeout_sec"] = PREVIEW_CAD_TIMEOUT_SEC
                info["message"] = f"Рендер чертежа может занять до {PREVIEW_CAD_TIMEOUT_SEC // 60} минут"
        elif suffix in SUPPORTED_OFFICE:
            info["pages_unknown"] = True
            info["message"] = "Для Office-файлов страницы определятся при загрузке"
        elif sibling:
            with _with_local_path(sibling) as local:
                info["pages"] = _count_pdf_pages(local)
    except Exception as e:
        info["previewable"] = False
        info["message"] = str(e)

    return info


def render_preview_png(
    path: str,
    *,
    page: int = 1,
    variant: str = "source",
) -> tuple[bytes, dict[str, Any]]:
    """Сгенерировать PNG предпросмотра."""
    file_path = validate_file(path)
    suffix = file_path.suffix.lower()

    if variant == "pdf":
        sibling = _pdf_sibling(file_path)
        target_path = sibling if sibling else file_path
        if target_path.suffix.lower() != ".pdf":
            raise ValueError("PDF рядом не найден")
    elif (
        variant == "source"
        and suffix in CAD_EXTENSIONS
        and PREVIEW_SOURCE_USE_SIBLING_PDF
        and (sibling := _pdf_sibling(file_path))
    ):
        target_path = sibling
    else:
        target_path = file_path

    target_suffix = target_path.suffix.lower()
    meta: dict[str, Any] = {
        "path": str(file_path),
        "variant": variant,
        "page": page,
    }

    with _with_local_path(target_path) as local:
        if target_suffix == ".pdf":
            png, pages = _render_pdf_page(local, page)
            meta["pages"] = pages
            meta["caption"] = f"Страница {max(1, min(page, pages))} из {pages}"
            if variant == "source" and suffix in CAD_EXTENSIONS and target_path != file_path:
                meta["via_sibling_pdf"] = True
        elif target_suffix in CAD_EXTENSIONS:
            png, cad_meta = _render_cad_preview_subprocess(local, page)
            meta.update(cad_meta)
            meta["pages"] = cad_meta.get("pages", 1)
            meta["caption"] = cad_meta.get("caption") or f"Страница {page} из {meta['pages']}"
        elif target_suffix in SUPPORTED_OFFICE:
            with _preview_lock:
                png, pages = _render_office_preview(local, page)
            meta["pages"] = pages
            meta["caption"] = f"Страница {max(1, min(page, pages))} из {pages}"
        else:
            raise ValueError(f"Предпросмотр для {target_suffix} недоступен")

    meta["page"] = max(1, min(page, meta.get("pages", 1)))
    return png, meta


def _view_target_path(file_path: Path, variant: str) -> Path:
    suffix = file_path.suffix.lower()
    if variant == "pdf":
        sibling = _pdf_sibling(file_path)
        target = sibling if sibling else file_path
        if target.suffix.lower() != ".pdf":
            raise ValueError("PDF рядом не найден — сначала выполните конвертацию")
        return target
    return file_path


def preview_timeout_sec(path: str, variant: str = "source") -> float:
    file_path = validate_file(path)
    suffix = file_path.suffix.lower()
    if suffix in CAD_EXTENSIONS:
        if variant == "pdf" or (variant == "source" and PREVIEW_SOURCE_USE_SIBLING_PDF and _pdf_sibling(file_path)):
            return 45.0
        if variant == "source":
            return float(PREVIEW_CAD_TIMEOUT_SEC)
    if suffix in SUPPORTED_OFFICE or (variant == "pdf" and suffix not in {".pdf"}):
        return float(PREVIEW_OFFICE_TIMEOUT_SEC)
    return 45.0


def view_document_timeout_sec(path: str, variant: str = "source") -> float:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in SUPPORTED_OFFICE and variant == "source":
        return float(PREVIEW_OFFICE_TIMEOUT_SEC)
    return 120.0


def resolve_view_document(path: str, variant: str = "source") -> tuple[Path, Path | None]:
    """PDF для просмотра в браузере. Возвращает (файл, temp_dir для очистки)."""
    file_path = validate_file(path)
    target = _view_target_path(file_path, variant)
    suffix = target.suffix.lower()

    if suffix == ".pdf":
        if _is_smb_path(target) and _smb_mounted():
            tmp = Path(tempfile.mkdtemp(prefix="view_pdf_"))
            with _smb_local_file(target) as local:
                dest = tmp / local.name
                shutil.copy2(local, dest)
            return dest, tmp
        return target, None

    if suffix in SUPPORTED_OFFICE:
        tmp = Path(tempfile.mkdtemp(prefix="view_office_"))
        with _with_local_path(target) as local:
            pdf = _convert_with_libreoffice(local, tmp)
            return pdf, tmp

    raise ValueError(
        "Просмотр исходника недоступен — откройте «PDF рядом» или выполните конвертацию"
    )


def viewer_mode_for(path: str, variant: str = "source") -> str:
    """pdf — встроенный PDF-viewer; image — PNG-рендер (CAD без PDF)."""
    file_path = validate_file(path)
    target = _view_target_path(file_path, variant) if variant == "pdf" else file_path
    suffix = target.suffix.lower()
    if suffix == ".pdf" or suffix in SUPPORTED_OFFICE:
        return "pdf"
    if suffix in CAD_EXTENSIONS:
        return "image"
    return "pdf"

