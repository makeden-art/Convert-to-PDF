"""Логика конвертации: один файл или вся папка, PDF рядом с оригиналом."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
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
    _ensure_under_allowed_roots(folder)
    return folder


def _ensure_under_allowed_roots(path: Path) -> None:
    roots = allowed_roots()
    allowed = False
    for root in roots:
        if path == root:
            allowed = True
            break
        try:
            path.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ValueError(
            f"Путь вне разрешённых каталогов. Разрешено: {', '.join(str(r) for r in roots)}"
        )


def validate_file(path: str) -> Path:
    file_path = Path(path).expanduser().resolve()
    _ensure_under_allowed_roots(file_path.parent)
    if _is_smb_path(file_path) and _smb_mounted():
        remote_dir, remote_name = _virtual_smb_remote(file_path)
        if not remote_name:
            raise ValueError(f"Это не файл: {file_path}")
        names = {e["name"] for e in _parse_smbclient_ls(_run_smbclient(remote_dir, "ls"))}
        if remote_name not in names:
            raise ValueError(f"Файл не найден на SMB: {file_path}")
        return file_path
    if not file_path.exists():
        raise ValueError(f"Файл не найден: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Это не файл: {file_path}")
    return file_path


BROWSE_TIMEOUT_SEC = float(os.getenv("CONVERT_BROWSE_TIMEOUT_SEC", "5"))
PLATFORM_STATE = Path("/opt/road-pdf-platform/platform.state.json")
SMB_ROOT = Path("/data/smb")


def _smb_mounted() -> bool:
    if not PLATFORM_STATE.exists():
        return False
    try:
        state = json.loads(PLATFORM_STATE.read_text(encoding="utf-8"))
        return bool(state.get("smb_mounted"))
    except Exception:
        return False


def _smb_config() -> dict:
    if not PLATFORM_STATE.exists():
        return {}
    try:
        state = json.loads(PLATFORM_STATE.read_text(encoding="utf-8"))
        return dict(state.get("smb_mount") or {})
    except Exception:
        return {}


def _smb_creds_path(mount_id: str = "default") -> Path:
    return Path(f"/opt/road-pdf-platform/secrets/smb/{mount_id}.creds")


def _smb_mount_base() -> Path:
    mount_id = _smb_config().get("mount_id", "default")
    return (SMB_ROOT / mount_id).resolve()


def _virtual_smb_remote(path: Path) -> tuple[str, str | None]:
    """Путь в UI → (каталог на шаре, имя файла или None для каталога)."""
    base = _smb_mount_base()
    rel = path.resolve().relative_to(base)
    if not rel.parts:
        return ".", None
    if path.suffix:
        remote_dir = "/".join(rel.parent.parts) if rel.parent.parts else "."
        return remote_dir, rel.name
    return "/".join(rel.parts), None


def _run_smbclient(remote_dir: str, command: str, *, timeout: float = 30) -> str:
    info = _smb_config()
    unc = info.get("unc")
    mount_id = info.get("mount_id", "default")
    creds = _smb_creds_path(mount_id)
    if not unc or not creds.exists():
        raise ValueError("SMB не настроен — подключите сетевую папку выше")
    cmd = [
        "smbclient",
        unc,
        "-A",
        str(creds),
        "-D",
        remote_dir.replace("/", "\\") if remote_dir not in (".", "") else ".",
        "-c",
        command,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "smbclient error").strip()
        raise ValueError(err)
    return proc.stdout or ""


_SMB_LS_LINE = re.compile(r"^\s+(.+)\s+([AD])\s+(\d+)\s+\S")


def _parse_smbclient_ls(output: str) -> list[dict]:
    entries: list[dict] = []
    for line in output.splitlines():
        m = _SMB_LS_LINE.match(line)
        if not m:
            continue
        name, kind, size_s = m.group(1).strip(), m.group(2), m.group(3)
        if name in (".", ".."):
            continue
        entries.append(
            {
                "name": name,
                "type": "dir" if kind == "D" else "file",
                "size": int(size_s),
            }
        )
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return entries


def _browse_smb_directory(folder: Path) -> dict:
    remote_dir, _ = _virtual_smb_remote(folder)
    output = _run_smbclient(remote_dir, "ls")
    base = _smb_mount_base()
    entries: list[dict] = []
    for item in _parse_smbclient_ls(output):
        virtual = base if remote_dir in (".", "") else base / remote_dir.replace("\\", "/")
        item_path = virtual / item["name"]
        entry = {"name": item["name"], "path": str(item_path), "type": item["type"]}
        if item["type"] == "file":
            suffix = Path(item["name"]).suffix.lower()
            entry["convertible"] = suffix in SUPPORTED_ALL
            entry["size"] = item["size"]
        entries.append(entry)

    parent: str | None = None
    if folder.resolve() != base:
        parent = str(folder.parent.resolve())
    elif str(folder.resolve()) == str(SMB_ROOT.resolve()):
        parent = ""
    return {"path": str(folder), "parent": parent, "entries": entries}


@contextmanager
def _smb_local_file(virtual_path: Path):
    """Скачать файл с SMB во временный каталог для конвертации."""
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        raise ValueError(f"Это не файл: {virtual_path}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="smb_get_"))
    local = tmp_dir / remote_name
    quoted = remote_name.replace('"', '\\"')
    _run_smbclient(remote_dir, f'get "{quoted}" "{local}"', timeout=120)
    if not local.exists():
        raise ValueError(f"Не удалось скачать с SMB: {remote_name}")
    try:
        yield local
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _smb_put_file(local_path: Path, virtual_path: Path) -> None:
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        remote_name = local_path.name
        remote_dir, _ = _virtual_smb_remote(virtual_path.parent)
    quoted_local = str(local_path).replace('"', '\\"')
    quoted_remote = remote_name.replace('"', '\\"')
    _run_smbclient(remote_dir, f'put "{quoted_local}" "{quoted_remote}"', timeout=120)


def _is_smb_path(folder: Path) -> bool:
    try:
        folder.resolve().relative_to(SMB_ROOT.resolve())
        return True
    except ValueError:
        return False


def _call_with_timeout(func, timeout: float = BROWSE_TIMEOUT_SEC):
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(func)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeoutError as e:
            raise ValueError(
                "Каталог недоступен (таймаут). Возможно SMB отключён — "
                "включите Windows-ПК и переподключите шару."
            ) from e
        except OSError as e:
            raise ValueError(f"Каталог недоступен: {e}") from e


def _dir_accessible(folder: Path) -> bool:
    def _check() -> bool:
        return folder.is_dir() and os.access(folder, os.R_OK)

    return bool(_call_with_timeout(_check))


def browse_directory(path: str = "") -> dict:
    """Содержимое каталога для дерева выбора (один уровень)."""
    if not path.strip():
        entries = []
        for root in allowed_roots():
            try:
                if _dir_accessible(root):
                    entries.append(
                        {
                            "name": root.name or str(root),
                            "path": str(root),
                            "type": "dir",
                        }
                    )
            except ValueError:
                continue
        entries.sort(key=lambda e: e["name"].lower())
        return {"path": "", "parent": None, "entries": entries}

    folder = Path(path).expanduser().resolve()
    _ensure_under_allowed_roots(folder)

    if _is_smb_path(folder) and not _smb_mounted():
        raise ValueError(
            "SMB не подключён. Подключите сетевую папку в блоке «Сетевая папка (SMB)» выше."
        )

    if _is_smb_path(folder) and _smb_mounted():
        base = _smb_mount_base()
        if folder.resolve() == SMB_ROOT.resolve():
            mount_id = _smb_config().get("mount_id", "default")
            return {
                "path": str(folder),
                "parent": "/data" if "/data" in [str(r) for r in allowed_roots()] else "",
                "entries": [
                    {
                        "name": mount_id,
                        "path": str(SMB_ROOT / mount_id),
                        "type": "dir",
                    }
                ],
            }
        if folder.resolve() == base or folder.resolve().is_relative_to(base):
            return _browse_smb_directory(folder)

    if not _dir_accessible(folder):
        raise ValueError(
            "Папка недоступна. Если это SMB — включите Windows-ПК и переподключите шару."
        )

    def _list_items() -> list[Path]:
        return sorted(folder.iterdir(), key=lambda p: p.name.lower())

    try:
        items = _call_with_timeout(_list_items)
    except ValueError:
        raise
    except PermissionError as e:
        raise ValueError("Нет доступа к каталогу") from e

    entries: list[dict] = []
    for item in items:
        if item.name.startswith("."):
            continue
        if (
            not _smb_mounted()
            and item.is_dir()
            and str(item.resolve()) == str(SMB_ROOT.resolve())
        ):
            continue
        try:
            if item.is_dir():
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.resolve()),
                        "type": "dir",
                    }
                )
            elif item.is_file():
                suffix = item.suffix.lower()
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item.resolve()),
                        "type": "file",
                        "convertible": suffix in SUPPORTED_ALL,
                        "size": item.stat().st_size,
                    }
                )
        except OSError:
            continue

    parent: str | None = None
    for root in allowed_roots():
        if folder == root:
            parent = ""
            break
        try:
            folder.relative_to(root)
            parent = "" if folder.parent == folder else str(folder.parent.resolve())
            break
        except ValueError:
            continue

    result = {"path": str(folder), "parent": parent, "entries": entries}
    if str(folder.resolve()) == "/data" and not _smb_mounted():
        result["entries"] = [e for e in entries if e.get("name") != "smb"]
    if str(folder) == str(SMB_ROOT.resolve()) and not _smb_mounted():
        result["smb_mounted"] = False
        result["message"] = "SMB не подключён"
    return result


def convert_paths(
    paths: list[str],
    *,
    merge: bool = False,
    output_name: str = "сборка.pdf",
) -> dict:
    """Конвертировать выбранные файлы на сервере."""
    if not paths:
        raise ValueError("Не выбрано ни одного файла")

    inputs: list[Path] = []
    for raw in paths:
        fp = validate_file(raw)
        if fp.suffix.lower() not in SUPPORTED_ALL:
            raise ValueError(f"Формат не поддерживается: {fp.name}")
        inputs.append(fp)

    inputs = sorted(set(inputs), key=lambda p: str(p).lower())

    if merge:
        folder = inputs[0].parent
        return _convert_folder_merged(folder, inputs, output_name, recursive=False)

    results = []
    stats = {"ok": 0, "skipped": 0, "error": 0}
    for src in inputs:
        item = convert_file_in_place(src)
        results.append(item)
        stats[item["status"]] = stats.get(item["status"], 0) + 1

    return {
        "folder": str(inputs[0].parent),
        "merge": False,
        "total": len(results),
        "stats": stats,
        "files": results,
    }


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
        if _is_smb_path(src) and _smb_mounted():
            with _smb_local_file(src) as local_src:
                tmp_pdf = local_src.with_suffix(".pdf")
                convert_file_to_pdf(local_src, tmp_pdf)
                _smb_put_file(tmp_pdf, dest)
        else:
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
