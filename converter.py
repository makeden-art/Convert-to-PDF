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

    tmp = Path(tempfile.mkdtemp(prefix="cvt_"))
    try:
        if suffix in SUPPORTED_CAD:
            if not oda_available():
                return {
                    "source": str(src),
                    "pdf": None,
                    "status": "error",
                    "message": "ODAFileConverter не установлен (DWG/DXF недоступны)",
                }
            pdf_tmp = convert_cad_to_pdf(str(src))
            shutil.move(str(pdf_tmp), str(dest))
        else:
            pdf_tmp = _convert_with_libreoffice(src, tmp)
            shutil.move(str(pdf_tmp), str(dest))
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def iter_files(folder: Path, recursive: bool) -> list[Path]:
    if recursive:
        files = [p for p in folder.rglob("*") if p.is_file()]
    else:
        files = [p for p in folder.iterdir() if p.is_file()]
    return sorted(files, key=lambda p: str(p).lower())


def convert_folder(folder_path: str, recursive: bool = True) -> dict:
    folder = validate_folder(folder_path)
    results = []
    stats = {"ok": 0, "skipped": 0, "error": 0}

    for f in iter_files(folder, recursive):
        if f.name.startswith("."):
            continue
        if f.suffix.lower() not in SUPPORTED_ALL:
            continue
        item = convert_file_in_place(f)
        results.append(item)
        stats[item["status"]] = stats.get(item["status"], 0) + 1

    return {
        "folder": str(folder),
        "recursive": recursive,
        "total": len(results),
        "stats": stats,
        "files": results,
    }
