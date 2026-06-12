"""Логика конвертации: один файл или вся папка, PDF рядом с оригиналом."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from cad_converter import CAD_EXTENSIONS, convert_cad_to_pdf, oda_available

SUPPORTED_OFFICE = {".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".rtf"}
SUPPORTED_CAD = CAD_EXTENSIONS
SUPPORTED_ALL = SUPPORTED_OFFICE | SUPPORTED_CAD | {".pdf"}


def allowed_roots() -> list[Path]:
    raw = os.getenv(
        "CONVERT_ALLOWED_ROOTS",
        "/data,/workspace,/opt/road-pdf-platform",
    )
    return [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]


def validate_folder(path: str) -> Path:
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise ValueError(f"Папка не найдена: {folder}")
    if not folder.is_dir():
        raise ValueError(f"Это не папка: {folder}")
    roots = allowed_roots()
    allowed = False
    for root in roots:
        if folder == root:
            allowed = True
            break
        try:
            folder.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError(
            f"Путь вне разрешённых каталогов. Разрешено: {', '.join(str(r) for r in roots)}"
        )
    return folder


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
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "LibreOffice error").strip())
    pdf = out_dir / f"{src.stem}.pdf"
    if not pdf.exists():
        raise RuntimeError("PDF не создан после конвертации")
    return pdf


def merge_pdfs(sources: list[Path], dest: Path) -> Path:
    """Склеить PDF-файлы в один (порядок — как в списке sources)."""
    import fitz

    if not sources:
        raise ValueError("Нет PDF для сборки")

    dest.parent.mkdir(parents=True, exist_ok=True)
    out = fitz.open()
    try:
        for pdf in sources:
            doc = fitz.open(pdf)
            out.insert_pdf(doc)
            doc.close()
        out.save(dest)
    finally:
        out.close()
    return dest


def convert_file_to_pdf(src: Path, dest: Path) -> None:
    """Конвертировать один файл в указанный PDF."""
    src = src.resolve()
    suffix = src.suffix.lower()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        shutil.copy2(src, dest)
        return

    if suffix not in SUPPORTED_OFFICE and suffix not in SUPPORTED_CAD:
        raise ValueError(f"Формат {suffix} не поддерживается")

    tmp = Path(tempfile.mkdtemp(prefix="cvt_one_"))
    try:
        if suffix in SUPPORTED_CAD:
            if not oda_available():
                raise RuntimeError("ODAFileConverter не установлен (DWG/DXF недоступны)")
            pdf_tmp = convert_cad_to_pdf(str(src))
            shutil.move(str(pdf_tmp), str(dest))
        else:
            pdf_tmp = _convert_with_libreoffice(src, tmp)
            shutil.move(str(pdf_tmp), str(dest))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_file_in_place(src: Path) -> dict:
    """Конвертирует файл и кладёт PDF в ту же папку: doc.docx → doc.pdf."""
    src = src.resolve()
    suffix = src.suffix.lower()
    dest = src.with_suffix(".pdf")

    if suffix == ".pdf":
        return {
            "source": str(src),
            "pdf": str(src),
            "status": "skipped",
            "message": "Уже PDF",
        }

    if suffix not in SUPPORTED_OFFICE and suffix not in SUPPORTED_CAD:
        return {
            "source": str(src),
            "pdf": None,
            "status": "skipped",
            "message": f"Формат {suffix} не поддерживается",
        }

    try:
        convert_file_to_pdf(src, dest)
        return {
            "source": str(src),
            "pdf": str(dest),
            "status": "ok",
            "message": "Сконвертировано",
        }
    except Exception as e:
        return {
            "source": str(src),
            "pdf": None,
            "status": "error",
            "message": str(e),
        }


def iter_files(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        files = [p for p in folder.rglob("*") if p.is_file()]
    else:
        files = [p for p in folder.iterdir() if p.is_file()]
    return sorted(files, key=lambda p: str(p).lower())


def _collect_inputs(folder: Path, recursive: bool) -> list[Path]:
    files: list[Path] = []
    for f in iter_files(folder, recursive):
        if f.name.startswith("."):
            continue
        if f.suffix.lower() not in SUPPORTED_ALL:
            continue
        files.append(f)
    return files


def convert_folder(
    folder_path: str,
    recursive: bool = True,
    *,
    merge: bool = False,
    output_name: str = "сборка.pdf",
) -> dict:
    folder = validate_folder(folder_path)
    inputs = _collect_inputs(folder, recursive)

    if merge:
        return _convert_folder_merged(folder, inputs, output_name, recursive)

    results = []
    stats = {"ok": 0, "skipped": 0, "error": 0}

    for f in inputs:
        item = convert_file_in_place(f)
        results.append(item)
        stats[item["status"]] = stats.get(item["status"], 0) + 1

    return {
        "folder": str(folder),
        "recursive": recursive,
        "merge": False,
        "total": len(results),
        "stats": stats,
        "files": results,
    }


def _convert_folder_merged(folder: Path, inputs: list[Path], output_name: str, recursive: bool) -> dict:
    if not inputs:
        raise ValueError("В папке нет поддерживаемых файлов для сборки")

    safe_name = Path(output_name).name or "сборка.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    merged_path = folder / safe_name
    tmp = Path(tempfile.mkdtemp(prefix="cvt_merge_"))
    results: list[dict] = []
    pdf_parts: list[Path] = []
    stats = {"ok": 0, "skipped": 0, "error": 0}

    try:
        for idx, src in enumerate(inputs):
            part = tmp / f"{idx:04d}.pdf"
            try:
                convert_file_to_pdf(src, part)
                pdf_parts.append(part)
                results.append(
                    {
                        "source": str(src),
                        "pdf": str(part),
                        "status": "ok",
                        "message": "Включён в сборку",
                    }
                )
                stats["ok"] += 1
            except Exception as e:
                results.append(
                    {
                        "source": str(src),
                        "pdf": None,
                        "status": "error",
                        "message": str(e),
                    }
                )
                stats["error"] += 1

        if not pdf_parts:
            raise ValueError("Не удалось сконвертировать ни одного файла для сборки")

        merge_pdfs(pdf_parts, merged_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "folder": str(folder),
        "recursive": recursive,
        "merge": True,
        "merged_pdf": str(merged_path),
        "pages_from": len(pdf_parts),
        "total": len(results),
        "stats": stats,
        "files": results,
    }


def convert_uploads_to_merged_pdf(sources: list[Path], dest: Path) -> dict:
    """Сконвертировать загруженные файлы и собрать в один PDF."""
    if not sources:
        raise ValueError("Не передано ни одного файла")

    tmp = Path(tempfile.mkdtemp(prefix="cvt_up_merge_"))
    results: list[dict] = []
    pdf_parts: list[Path] = []
    stats = {"ok": 0, "error": 0}

    try:
        for idx, src in enumerate(sources):
            part = tmp / f"{idx:04d}.pdf"
            try:
                convert_file_to_pdf(src, part)
                pdf_parts.append(part)
                results.append({"source": src.name, "status": "ok"})
                stats["ok"] += 1
            except Exception as e:
                results.append({"source": src.name, "status": "error", "message": str(e)})
                stats["error"] += 1

        if not pdf_parts:
            raise ValueError("Не удалось сконвертировать ни одного файла")

        merge_pdfs(pdf_parts, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"merged_pdf": str(dest), "pages_from": len(pdf_parts), "stats": stats, "files": results}
