"""Логика конвертации: один файл или вся папка, PDF рядом с оригиналом."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import gc
import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from pathlib import Path

from cad_converter import CAD_EXTENSIONS, convert_cad_to_pdf
from format_detect import (
    FormatInfo,
    _extension_fallback,
    detect_format_from_bytes,
    format_from_extension,
    inspect_from_extension_only,
    validation_error_message,
)
from job_control import JobCancelledError, check_cancelled, run_monitored

SUPPORTED_OFFICE = {".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".rtf"}
MAGIC_INSPECT_MAX_BYTES = 8192
MAGIC_INSPECT_MAX_SIZE = int(os.getenv("CONVERT_MAGIC_MAX_SIZE", str(5 * 1024 * 1024)))
SUPPORTED_CAD = CAD_EXTENSIONS
SUPPORTED_ALL = SUPPORTED_OFFICE | SUPPORTED_CAD | {".pdf"}


def release_memory() -> None:
    """Вернуть неиспользуемую RAM процессу ОС (Python сам этого не делает)."""
    gc.collect()
    if os.name != "posix":
        return
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def allowed_roots() -> list[Path]:
    raw = os.getenv(
        "CONVERT_ALLOWED_ROOTS",
        "/data,/workspace,/opt/road-pdf-platform",
    )
    return [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]


def get_file_content_hash(path: Path) -> str:
    """Быстрый хэш файла на основе размера, mtime и структуры байт."""
    try:
        stat = path.stat()
        h = hashlib.md5()
        # Добавляем имя, размер и время изменения
        h.update(f"{path.name}_{stat.st_size}_{stat.st_mtime}".encode())
        # Если файл не пустой, берем крайние байты для страховки от коллизий при подмене
        if stat.st_size > 0:
            with open(path, "rb") as f:
                h.update(f.read(8192))
                if stat.st_size > 8192:
                    f.seek(-8192, 2)
                    h.update(f.read(8192))
        return h.hexdigest()
    except Exception:
        return hashlib.md5(f"{path}_{time.time()}".encode()).hexdigest()


def get_global_cache_dir() -> Path:
    """Глобальная папка кэша (резервная)."""
    d = Path(os.getenv("CONVERT_CACHE_DIR", "/data/cache/convert"))
    try:
        d.mkdir(parents=True, exist_ok=True)
        test_file = d / f".test_write_{uuid.uuid4().hex}"
        test_file.touch()
        test_file.unlink()
        return d
    except Exception:
        fallback = Path(tempfile.gettempdir()) / "convert_cache"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def get_local_cache_dir(src_file: Path) -> Path:
    """Локальная папка проекта `.convert_cache`. Если папка read-only, отдает глобальную."""
    parent = src_file.parent
    cache_dir = parent / ".convert_cache"
    try:
        cache_dir.mkdir(exist_ok=True)
        test_file = cache_dir / f".test_write_{uuid.uuid4().hex}"
        test_file.touch()
        test_file.unlink()
        return cache_dir
    except Exception:
        return get_global_cache_dir()


def server_path_exists(path: Path) -> bool:
    """Проверить, существует ли файл на сервере (локально или SMB)."""
    p = path.expanduser().resolve()
    try:
        _ensure_under_allowed_roots(p.parent)
    except ValueError:
        return False

    if _is_smb_path(p) and _smb_mounted():
        remote_dir, remote_name = _virtual_smb_remote(p)
        if not remote_name:
            return False
        try:
            names = {
                e["name"]
                for e in _parse_smbclient_ls(_run_smbclient(remote_dir, "ls", timeout=10))
            }
            return remote_name in names
        except ValueError:
            return False
    return p.is_file()


def check_output_files(
    *,
    paths: list[str] | None = None,
    folder_path: str | None = None,
    merge: bool = False,
    output_name: str = "сборка.pdf",
    recursive: bool = True,
) -> dict:
    """Проверить итоговые PDF: существуют ли и можно ли записать до конвертации."""
    targets_paths: list[Path] = []

    if merge:
        if paths:
            inputs = resolve_ordered_inputs(paths, recursive=recursive)
            folder = inputs[0].parent
        elif folder_path:
            folder = validate_folder(folder_path)
        else:
            return {"targets": [], "existing": [], "blocked": [], "count": 0, "can_proceed": True}
        safe_name = Path(output_name).name or "сборка.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        targets_paths.append(folder / safe_name)
    else:
        if paths:
            for raw in resolve_ordered_inputs(paths, recursive=recursive):
                if raw.suffix.lower() != ".pdf":
                    targets_paths.append(raw.with_suffix(".pdf"))
        elif folder_path:
            folder = validate_folder(folder_path)
            for f in _collect_inputs(folder, recursive):
                if f.suffix.lower() != ".pdf":
                    targets_paths.append(f.with_suffix(".pdf"))

    seen: set[str] = set()
    targets: list[dict] = []
    existing: list[dict] = []
    blocked: list[dict] = []

    for t in targets_paths:
        key = str(t)
        if key in seen:
            continue
        seen.add(key)
        info = _check_output_writable(t)
        targets.append(info)
        if info["exists"]:
            existing.append({"path": info["path"], "name": info["name"]})
        if not info["writable"]:
            blocked.append(info)

    return {
        "targets": targets,
        "existing": existing,
        "blocked": blocked,
        "count": len(existing),
        "can_proceed": len(blocked) == 0,
    }


def _check_output_writable(path: Path) -> dict:
    """Проверить, можно ли записать итоговый PDF по указанному пути."""
    p = path.expanduser().resolve()
    exists = server_path_exists(p)
    writable, message = _can_write_output(p)
    return {
        "path": str(p),
        "name": p.name,
        "exists": exists,
        "writable": writable,
        "locked": exists and not writable,
        "message": message,
    }


def _can_write_output(path: Path) -> tuple[bool, str]:
    if _is_smb_path(path) and _smb_mounted():
        if server_path_exists(path):
            return _smb_can_overwrite(path)
        return _smb_can_create_in_folder(path)
    parent = path.parent
    if not parent.exists():
        return False, "Папка назначения не найдена"
    if not os.access(parent, os.W_OK):
        return False, "Нет прав на запись в папку"
    if path.exists() and not os.access(path, os.W_OK):
        return False, (
            "Файл не перезаписан — используется в другой программе. "
            "Закройте его и повторите."
        )
    return True, ""


def _smb_can_create_in_folder(virtual_path: Path) -> tuple[bool, str]:
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if remote_name:
        remote_dir, _ = _virtual_smb_remote(virtual_path.parent)
    probe = f".__wprobe_{uuid.uuid4().hex[:8]}"
    fd, tmp_name = tempfile.mkstemp(prefix="smb_probe_")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(b"ok")
        quoted_local = str(tmp).replace('"', '\\"')
        quoted_probe = probe.replace('"', '\\"')
        _run_smbclient(remote_dir, f'put "{quoted_local}" "{quoted_probe}"', timeout=15)
        _run_smbclient(remote_dir, f'del "{quoted_probe}"', timeout=15)
        return True, ""
    except ValueError as e:
        return False, _friendly_smb_error(str(e))
    finally:
        tmp.unlink(missing_ok=True)


def _smb_can_overwrite(virtual_path: Path) -> tuple[bool, str]:
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        return False, "Некорректный путь к файлу"
    temp_name = f".__lck_{uuid.uuid4().hex[:8]}_{remote_name}"
    q_old = remote_name.replace('"', '\\"')
    q_new = temp_name.replace('"', '\\"')
    try:
        _run_smbclient(remote_dir, f'rename "{q_old}" "{q_new}"', timeout=15)
        _run_smbclient(remote_dir, f'rename "{q_new}" "{q_old}"', timeout=15)
        return True, ""
    except ValueError as e:
        return False, _friendly_smb_error(str(e))


def validate_folder(path: str) -> Path:
    folder = Path(path).expanduser().resolve()
    _ensure_under_allowed_roots(folder)
    if _is_smb_path(folder) and _smb_mounted():
        remote_dir, remote_name = _virtual_smb_remote(folder)
        if remote_name:
            raise ValueError(f"Это не папка: {folder}")
        try:
            _run_smbclient(remote_dir, "ls")
        except ValueError as e:
            raise ValueError(f"Папка не найдена на SMB: {folder}") from e
        return folder
    if not folder.exists():
        raise ValueError(f"Папка не найдена: {folder}")
    if not folder.is_dir():
        raise ValueError(f"Это не папка: {folder}")
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
MERGE_WORKERS = max(1, int(os.getenv("CONVERT_MERGE_WORKERS", "1")))
OFFICE_WORKERS = max(1, int(os.getenv("CONVERT_OFFICE_WORKERS", "1")))
CAD_WORKERS = max(1, int(os.getenv("CONVERT_CAD_WORKERS", "1")))
CONVERT_ISOLATE = os.getenv("CONVERT_ISOLATE", "1").strip().lower() in ("1", "true", "yes")
FILE_CONVERT_TIMEOUT_SEC = int(os.getenv("CONVERT_FILE_TIMEOUT_SEC", "300"))
CAD_CONVERT_TIMEOUT_SEC = int(os.getenv("CONVERT_CAD_TIMEOUT_SEC", "1800"))
CONVERT_CHILD_MEM_MB = int(os.getenv("CONVERT_CHILD_MEM_MB", "4096"))
_WORKER_SCRIPT = Path(__file__).with_name("convert_worker.py")
_office_sem = threading.Semaphore(OFFICE_WORKERS)
_cad_sem = threading.Semaphore(CAD_WORKERS)
PLATFORM_STATE = Path("/opt/road-pdf-platform/platform.state.json")
SMB_ROOT = Path("/data/smb")


def _smb_mounted() -> bool:
    if _smb_configured():
        return True
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


def _smb_configured() -> bool:
    cfg = _smb_config()
    mount_id = cfg.get("mount_id", "default")
    return bool(cfg.get("unc") and _smb_creds_path(mount_id).exists())


def _smb_creds_path(mount_id: str = "default") -> Path:
    tmp = Path(f"/tmp/smb-{mount_id}.creds")
    if tmp.exists():
        return tmp
    return Path(f"/opt/road-pdf-platform/secrets/smb/{mount_id}.creds")


def _smb_mount_base() -> Path:
    mount_id = _smb_config().get("mount_id", "default")
    return (SMB_ROOT / mount_id).resolve()


def _smb_share_path_prefix() -> str:
    return (_smb_config().get("share_path") or "").strip().strip("/")


def _join_remote_dir(*parts: str) -> str:
    clean = [p.strip("/\\").replace("\\", "/") for p in parts if p and p not in (".", "")]
    if not clean:
        return "."
    return "/".join(clean).replace("/", "\\")


def _virtual_smb_remote(path: Path) -> tuple[str, str | None]:
    """Путь в UI → (каталог на шаре, имя файла или None для каталога)."""
    base = _smb_mount_base()
    share_prefix = _smb_share_path_prefix()
    rel = path.resolve().relative_to(base)
    rel_str = "/".join(rel.parts) if rel.parts else ""
    if path.suffix and rel.parts:
        parent_rel = "/".join(rel.parent.parts) if rel.parent.parts else ""
        return _join_remote_dir(share_prefix, parent_rel), rel.name
    if path.suffix:
        return _join_remote_dir(share_prefix), rel.name
    return _join_remote_dir(share_prefix, rel_str), None


def _run_smbclient(remote_dir: str, command: str, *, timeout: float = 30) -> str:
    info = _smb_config()
    unc = info.get("unc")
    mount_id = info.get("mount_id", "default")
    creds = _smb_creds_path(mount_id)
    if not unc or not creds.exists():
        raise ValueError("SMB не настроен — подключите сетевую папку выше")
    remote = remote_dir.replace("/", "\\").strip("\\") if remote_dir not in (".", "") else ""
    full_command = f'cd "{remote}"; {command}' if remote else command
    cmd = [
        "smbclient",
        unc,
        "-A",
        str(creds),
        "-c",
        full_command,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "smbclient error").strip()
        raise ValueError(_friendly_smb_error(err))
    return proc.stdout or ""


def _friendly_smb_error(err: str) -> str:
    low = err.lower()
    if "authentication file" in low or "credentials file" in low:
        return (
            "SMB: не найден файл учётных данных. "
            "Переподключите сетевую папку в блоке «Сетевая папка (SMB)»."
        )
    if "NT_STATUS_SHARING_VIOLATION" in err or "SHARING_VIOLATION" in err:
        return (
            "Файл не перезаписан — используется в другой программе на Windows. "
            "Закройте его (PDF, Word, проводник) и повторите."
        )
    if "BAD_NETWORK_NAME" in err:
        return (
            "Шара не найдена. Укажите имя шары и подпапку через слэш: scan/pdf."
        )
    return err


_SMB_LS_LINE = re.compile(r"^\s+(.+)\s+([AD])\s+(\d+)\s+\S")


def _smb_file_size(virtual_path: Path) -> int | None:
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        return None
    try:
        output = _run_smbclient(remote_dir, "ls", timeout=10)
        for item in _parse_smbclient_ls(output):
            if item["name"] == remote_name:
                return int(item["size"])
    except ValueError:
        return None
    return None


def _read_file_header(path: Path, limit: int = MAGIC_INSPECT_MAX_BYTES) -> bytes:
    if _is_smb_path(path) and _smb_mounted():
        with _smb_local_file(path) as local:
            with local.open("rb") as fh:
                return fh.read(limit)
    with path.open("rb") as fh:
        return fh.read(limit)


def inspect_file_format(
    path: Path,
    *,
    file_size: int | None = None,
    light: bool = False,
) -> FormatInfo:
    """Определить реальный формат файла (magic bytes или по расширению для крупных SMB)."""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_ALL:
        return inspect_from_extension_only(ext)

    if light:
        return inspect_from_extension_only(ext)

    if _is_smb_path(path) and _smb_mounted():
        size = file_size if file_size is not None else _smb_file_size(path)
        if size == 0:
            info = inspect_from_extension_only(ext)
            info.valid = False
            info.error = "Файл пустой"
            info.extension_ok = False
            return info
        if size is not None and size > MAGIC_INSPECT_MAX_SIZE:
            return inspect_from_extension_only(ext)
    elif path.is_file():
        try:
            if path.stat().st_size == 0:
                info = inspect_from_extension_only(ext)
                info.valid = False
                info.error = "Файл пустой"
                info.extension_ok = False
                return info
        except OSError:
            info = inspect_from_extension_only(ext)
            info.valid = False
            info.error = "Не удалось прочитать файл"
            return info
    else:
        info = inspect_from_extension_only(ext)
        info.valid = False
        info.error = "Файл не найден"
        return info

    try:
        data = _read_file_header(path)
    except Exception as e:
        if ext in SUPPORTED_ALL:
            return _extension_fallback(
                ext,
                reason=f"Не удалось прочитать заголовок ({e}), конвертация по расширению",
            )
        info = inspect_from_extension_only(ext)
        info.valid = False
        info.error = f"Не удалось прочитать файл: {e}"
        return info

    info = detect_format_from_bytes(data, ext)
    if not info.valid and ext in SUPPORTED_ALL and info.detected in ("unknown", "zip"):
        return _extension_fallback(
            ext,
            reason=info.error or "Содержимое не распознано, конвертация по расширению",
        )
    return info


def _format_entry_fields(path: Path, *, file_size: int | None = None, light: bool = False) -> dict:
    info = inspect_file_format(path, file_size=file_size, light=light)
    return info.to_dict()


def _validate_file_format_or_raise(path: Path) -> FormatInfo:
    info = inspect_file_format(path)
    err = validation_error_message(info, path.name)
    if err:
        expected = info.expected or format_from_extension(path.suffix.lower()) or "?"
        detected = info.detected
        print(
            f"[convert] format reject {path.name}: "
            f"expected={expected} detected={detected} "
            f"valid={info.valid} ext_ok={info.extension_ok} "
            f"err={info.error or info.warning}",
            flush=True,
        )
        raise ValueError(err)
    if info.warning:
        print(
            f"[convert] format warn {path.name}: {info.warning}",
            flush=True,
        )
    return info


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
        item_path = folder / item["name"]
        entry = {"name": item["name"], "path": str(item_path), "type": item["type"]}
        if item["type"] == "file":
            suffix = Path(item["name"]).suffix.lower()
            entry["convertible"] = suffix in SUPPORTED_ALL
            entry["size"] = item["size"]
            if suffix in SUPPORTED_ALL:
                entry.update(
                    _format_entry_fields(item_path, file_size=int(item["size"]), light=True)
                )
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


def _smb_mkdir(virtual_path: Path):
    """Создать папку на SMB."""
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        remote_name = virtual_path.name
        remote_dir, _ = _virtual_smb_remote(virtual_path.parent)
    quoted_remote = remote_name.replace('"', '\\"')
    try:
        _run_smbclient(remote_dir, f'mkdir "{quoted_remote}"', timeout=30)
    except ValueError as e:
        if "NT_STATUS_OBJECT_NAME_COLLISION" not in str(e):
            raise

def _smb_delete(virtual_path: Path, is_dir: bool):
    """Удалить файл или папку на SMB."""
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        remote_name = virtual_path.name
        remote_dir, _ = _virtual_smb_remote(virtual_path.parent)
    quoted_remote = remote_name.replace('"', '\\"')
    
    cmd = f'deltree "{quoted_remote}"' if is_dir else f'rm "{quoted_remote}"'
    try:
        _run_smbclient(remote_dir, cmd, timeout=30)
    except ValueError as e:
        if "NT_STATUS_NO_SUCH_FILE" not in str(e) and "NT_STATUS_OBJECT_NAME_NOT_FOUND" not in str(e):
            raise

def _smb_put_file(local_path: Path, virtual_path: Path) -> Path:
    """Загрузить файл на SMB. Возвращает фактический путь (может отличаться при блокировке)."""
    remote_dir, remote_name = _virtual_smb_remote(virtual_path)
    if not remote_name:
        remote_name = local_path.name
        remote_dir, _ = _virtual_smb_remote(virtual_path.parent)
    quoted_local = str(local_path).replace('"', '\\"')
    quoted_remote = remote_name.replace('"', '\\"')

    last_err = ""
    for attempt in range(4):
        try:
            _run_smbclient(remote_dir, f'put "{quoted_local}" "{quoted_remote}"', timeout=120)
            return virtual_path
        except ValueError as e:
            last_err = str(e)
            if "не перезаписан" not in last_err and "SHARING_VIOLATION" not in last_err:
                raise
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            break

    stem = Path(remote_name).stem
    suffix = Path(remote_name).suffix or ".pdf"
    for n in range(1, 100):
        alt_name = f"{stem}_{n}{suffix}"
        quoted_alt = alt_name.replace('"', '\\"')
        try:
            _run_smbclient(remote_dir, f'put "{quoted_local}" "{quoted_alt}"', timeout=120)
            return virtual_path.parent / alt_name
        except ValueError as e:
            last_err = str(e)
            if "не перезаписан" not in last_err and "SHARING_VIOLATION" not in last_err:
                raise
            continue

    raise ValueError(last_err or "Не удалось сохранить файл на SMB")


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
        if not _smb_configured():
            return {
                "path": "",
                "parent": None,
                "smb_mounted": False,
                "entries": [],
                "message": "SMB не подключён. Подключите сетевую папку выше.",
            }
        base = _smb_mount_base()
        result = _browse_smb_directory(base)
        result["smb_mounted"] = True
        if not result.get("entries"):
            result.setdefault(
                "message",
                "Папка пуста или нет доступа к файлам. Проверьте путь шары (scan/pdf).",
            )
        return result

    folder = Path(path).expanduser().resolve()
    _ensure_under_allowed_roots(folder)

    if _is_smb_path(folder) and not _smb_configured():
        raise ValueError(
            "SMB не подключён. Подключите сетевую папку в блоке «Сетевая папка (SMB)» выше."
        )

    if _is_smb_path(folder) and _smb_configured():
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
                entry = {
                    "name": item.name,
                    "path": str(item.resolve()),
                    "type": "file",
                    "convertible": suffix in SUPPORTED_ALL,
                    "size": item.stat().st_size,
                }
                if suffix in SUPPORTED_ALL:
                    entry.update(_format_entry_fields(item, light=True))
                entries.append(entry)
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
    recursive: bool = True,
    windows_cad_ip: str = "",
    number_pages: bool = False,
    numbering_from_page: int = 1,
    numbering_start: int = 1,
) -> dict:
    """Конвертировать выбранные файлы и папки на сервере."""
    inputs = resolve_ordered_inputs(paths, recursive=recursive)

    if merge:
        folder = inputs[0].parent
        from_page = numbering_from_page if number_pages else None
        start_num = numbering_start if number_pages else 1
        return _convert_folder_merged(
            folder,
            inputs,
            output_name,
            recursive=False,
            windows_cad_ip=windows_cad_ip,
            numbering_from_page=from_page,
            numbering_start=start_num,
        )

    results = []
    stats = {"ok": 0, "skipped": 0, "error": 0}
    for src in inputs:
        check_cancelled()
        item = convert_file_in_place(
            src, 
            windows_cad_ip=windows_cad_ip,
            number_pages=number_pages,
            numbering_from_page=numbering_from_page,
            numbering_start=numbering_start
        )
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
    with _office_sem:
        proc = run_monitored(
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
            timeout=300,
        )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "LibreOffice error").strip())
    pdf = out_dir / f"{src.stem}.pdf"
    if not pdf.exists():
        raise RuntimeError("PDF не создан после конвертации")
    return pdf


def merge_pdfs(
    sources: list[Path],
    dest: Path,
    *,
    numbering_from_page: int | None = None,
    numbering_start: int = 1,
) -> Path:
    """Склеить PDF-файлы в один (порядок — как в списке sources)."""
    if not sources:
        raise ValueError("Нет PDF для сборки")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(sources) == 1:
        shutil.copy2(sources[0], dest)
    elif shutil.which("gs"):
        _merge_pdfs_ghostscript(sources, dest)
    else:
        _merge_pdfs_fitz(sources, dest)

    if numbering_from_page is not None and numbering_from_page > 0:
        _apply_pdf_numbering(
            dest,
            from_page=numbering_from_page,
            start=numbering_start,
        )
    release_memory()
    return dest


def _merge_pdfs_ghostscript(sources: list[Path], dest: Path) -> None:
    """Сборка PDF через Ghostscript — не раздувает RAM процесса Python."""
    cmd = [
        "gs",
        "-dBATCH",
        "-dNOPAUSE",
        "-q",
        "-dSAFER",
        "-sDEVICE=pdfwrite",
        "-dPDFSETTINGS=/default",
        "-dAutoRotatePages=/None",
        f"-sOutputFile={dest}",
        *[str(p) for p in sources],
    ]
    proc = run_monitored(cmd, timeout=600)
    if proc.returncode != 0 or not dest.exists():
        err = (proc.stderr or proc.stdout or "ghostscript error").strip()
        raise RuntimeError(f"Ошибка сборки PDF (gs): {err}")


def _merge_pdfs_fitz(sources: list[Path], dest: Path) -> None:
    """Fallback: PyMuPDF, по одному файлу с принудительной очисткой."""
    import fitz

    out = fitz.open(str(sources[0]))
    try:
        for pdf in sources[1:]:
            with fitz.open(str(pdf)) as doc:
                out.insert_pdf(doc)
            gc.collect()
        out.save(dest, garbage=4, deflate=True)
    finally:
        out.close()
        release_memory()


def _apply_pdf_numbering(path: Path, *, from_page: int, start: int) -> None:
    import fitz

    tmp = path.with_suffix(".numbered.pdf")
    doc = fitz.open(str(path))
    try:
        total = doc.page_count
        page_from = max(1, min(int(from_page), total))
        first_num = max(1, int(start))
        for i, page in enumerate(doc):
            page_num = i + 1
            if page_num < page_from:
                continue
            num = str(first_num + (page_num - page_from))
            rect = page.rect
            page.insert_text(
                (rect.x1 - 35, rect.y0 + 16),
                num,
                fontsize=12,
                fontname="helv",
                color=(0, 0, 0),
            )
        doc.save(tmp, garbage=4, deflate=True)
    finally:
        doc.close()
        gc.collect()
    tmp.replace(path)


def _run_merge_parts(
    inputs: list[Path], tmp: Path, windows_cad_ip: str = ""
) -> list[tuple[int, Path | None, dict]]:
    """Конвертация частей сборки: по умолчанию последовательно (экономия RAM)."""
    workers = min(MERGE_WORKERS, len(inputs))
    part_results: list[tuple[int, Path | None, dict]] = []
    if workers <= 1:
        for idx, src in enumerate(inputs):
            check_cancelled()
            part_results.append(_convert_merge_part(idx, src, tmp, windows_cad_ip=windows_cad_ip))
            release_memory()
        return part_results
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_convert_merge_part, idx, src, tmp, windows_cad_ip) for idx, src in enumerate(inputs)]
        for fut in futs:
            check_cancelled()
            part_results.append(fut.result())
            release_memory()
    return part_results


def _child_memory_limit() -> None:
    """Ограничить RAM дочернего процесса (OOM убивает ребёнка, не uvicorn)."""
    if os.name != "posix":
        return
    import resource

    limit = CONVERT_CHILD_MEM_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        pass


def _convert_timeout_for(src: Path) -> int:
    if src.suffix.lower() in SUPPORTED_CAD:
        return CAD_CONVERT_TIMEOUT_SEC
    return FILE_CONVERT_TIMEOUT_SEC


def convert_file_to_pdf_isolated(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:
    """Конвертация в отдельном процессе — OOM дочернего не роняет uvicorn."""
    import sys
    import json

    timeout_sec = _convert_timeout_for(src)
    meta_file = dest.with_suffix(".meta.json")
    if meta_file.exists():
        try:
            meta_file.unlink()
        except Exception:
            pass

    try:
        proc = run_monitored(
            [sys.executable, str(_WORKER_SCRIPT), str(src), str(dest), windows_cad_ip],
            timeout=timeout_sec,
            preexec_fn=_child_memory_limit,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Таймаут конвертации {src.name} ({timeout_sec} с)"
        ) from e
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "ошибка конвертации").strip()
        if proc.returncode < 0:
            raise RuntimeError(
                f"Процесс конвертации прерван ({src.name}): нехватка памяти или сбой ODA"
            )
        raise RuntimeError(msg)
    if not dest.is_file():
        raise RuntimeError(f"PDF не создан: {src.name}")

    meta = None
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta_file.unlink()
        except Exception:
            pass
    return meta


def _convert_local_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:
    if CONVERT_ISOLATE:
        return convert_file_to_pdf_isolated(src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)
    else:
        return convert_file_to_pdf(src, dest, windows_cad_ip=windows_cad_ip, dsd_path=dsd_path)


def convert_file_to_pdf(src: Path, dest: Path, windows_cad_ip: str = "", dsd_path: str = None) -> dict | None:
    """Конвертировать один локальный файл в указанный PDF. Для CAD возвращает meta."""
    src = src.resolve()
    suffix = src.suffix.lower()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if suffix == ".pdf":
        _validate_file_format_or_raise(src)
        shutil.copy2(src, dest)
        return None

    if suffix not in SUPPORTED_OFFICE and suffix not in SUPPORTED_CAD:
        raise ValueError(f"Формат {suffix} не поддерживается")

    _validate_file_format_or_raise(src)

    tmp = Path(tempfile.mkdtemp(prefix="cvt_one_"))
    try:
        if suffix in SUPPORTED_CAD:
            if not windows_cad_ip:
                raise RuntimeError("Не указан Windows CAD IP для конвертации DWG/DXF")
            with _cad_sem:
                pdf_tmp, cad_meta = convert_cad_to_pdf(str(src), meta={"windows_cad_ip": windows_cad_ip, "dsd_path": dsd_path})
            try:
                shutil.move(str(pdf_tmp), str(dest))
            finally:
                shutil.rmtree(pdf_tmp.parent, ignore_errors=True)
            return cad_meta
            
        if windows_cad_ip and suffix in {".doc", ".docx", ".xls", ".xlsx", ".rtf"}:
            try:
                ip = windows_cad_ip
                if not ip.startswith("http"):
                    ip = "http://" + ip
                if ip.count(':') == 1:
                    ip += ":8000"
                url = f"{ip.rstrip('/')}/convert-office"
                print(f"Отправляем {src.name} на Windows Server MS Office ({url})...")
                
                cmd = [
                    'curl', '-s', '-o', str(dest),
                    '-F', f'file=@{str(src)}',
                    '-w', '%{http_code}',
                    url
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                http_code = res.stdout.strip()
                
                if http_code == '200' and dest.exists() and dest.stat().st_size > 0:
                    return None
                else:
                    print(f"Windows server Office error: HTTP {http_code}")
                    if dest.exists():
                        dest.unlink()
            except Exception as e:
                print(f"Windows server Office fallback: {e}")
                
        pdf_tmp = _convert_with_libreoffice(src, tmp)
        shutil.move(str(pdf_tmp), str(dest))
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _convert_source_to_temp_pdf(src: Path, dest: Path, windows_cad_ip: str = "") -> dict | None:
    """Конвертировать файл с сервера (локальный или SMB) во временный PDF."""
    if _is_smb_path(src) and _smb_mounted():
        with _smb_local_file(src) as local_src:
            return _convert_local_file_to_pdf(local_src, dest, windows_cad_ip=windows_cad_ip)
    return _convert_local_file_to_pdf(src, dest, windows_cad_ip=windows_cad_ip)


def _save_merged_pdf(local_pdf: Path, dest: Path) -> Path:
    """Сохранить собранный PDF в целевую папку (SMB или локально)."""
    if _is_smb_path(dest) and _smb_mounted():
        return _smb_put_file(local_pdf, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_pdf, dest)
    return dest


def convert_file_in_place(
    src: Path,
    windows_cad_ip: str = "",
    number_pages: bool = False,
    numbering_from_page: int | None = None,
    numbering_start: int = 1,
) -> dict:
    """Конвертирует файл и кладёт PDF в ту же папку: doc.docx → doc.pdf."""
    src = src.resolve()
    suffix = src.suffix.lower()
    dest = src.with_suffix(".pdf")

    if suffix == ".pdf":
        if number_pages:
            try:
                if _is_smb_path(src) and _smb_mounted():
                    with _smb_local_file(src) as local_src:
                        _apply_pdf_numbering(local_src, from_page=numbering_from_page or 1, start=numbering_start)
                        saved = _smb_put_file(local_src, src)
                else:
                    _apply_pdf_numbering(src, from_page=numbering_from_page or 1, start=numbering_start)
                    saved = src
                return {
                    "source": str(src),
                    "pdf": str(saved),
                    "status": "ok",
                    "message": "Пронумеровано",
                }
            except Exception as e:
                return {
                    "source": str(src),
                    "pdf": None,
                    "status": "error",
                    "message": str(e),
                }
        else:
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
        cad_meta: dict | None = None
        if _is_smb_path(src) and _smb_mounted():
            with _smb_local_file(src) as local_src:
                tmp_pdf = local_src.with_suffix(".pdf")
                cad_meta = _convert_local_file_to_pdf(local_src, tmp_pdf, windows_cad_ip=windows_cad_ip)
                if number_pages and tmp_pdf.exists():
                    _apply_pdf_numbering(tmp_pdf, from_page=numbering_from_page or 1, start=numbering_start)
                saved = _smb_put_file(tmp_pdf, dest)
        else:
            cad_meta = _convert_local_file_to_pdf(src, dest, windows_cad_ip=windows_cad_ip)
            if number_pages and dest.exists():
                _apply_pdf_numbering(dest, from_page=numbering_from_page or 1, start=numbering_start)
            saved = dest
        msg = "Сконвертировано"
        if cad_meta and cad_meta.get("engine") == "ezdxf" and cad_meta.get("fallback"):
            msg = "Сконвертировано (запасной режим ezdxf — качество может быть ниже ODA)"
        if cad_meta and cad_meta.get("render_mode") == "frames":
            n = cad_meta.get("frames_rendered") or 0
            if n:
                msg += f", рамок: {n}"
        if str(saved) != str(dest):
            msg = (
                f"Файл «{dest.name}» не перезаписан — используется в другой программе. "
                f"Сохранено как «{saved.name}»"
            )
        return {
            "source": str(src),
            "pdf": str(saved),
            "status": "ok",
            "message": msg,
            **({"cad": cad_meta} if cad_meta else {}),
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


def _collect_smb_inputs(folder: Path, recursive: bool) -> list[Path]:
    base = _smb_mount_base()
    remote_dir, remote_name = _virtual_smb_remote(folder)
    if remote_name:
        return []

    files: list[Path] = []

    def walk(remote: str, virtual: Path) -> None:
        output = _run_smbclient(remote, "ls")
        for item in _parse_smbclient_ls(output):
            vpath = virtual / item["name"]
            if item["type"] == "dir":
                if recursive:
                    sub_remote = (
                        f"{remote}/{item['name']}"
                        if remote not in (".", "")
                        else item["name"]
                    )
                    walk(sub_remote, vpath)
            elif Path(item["name"]).suffix.lower() in SUPPORTED_ALL:
                files.append(vpath)

    walk(remote_dir, folder if folder.resolve() != base else base)
    return sorted(files, key=lambda p: str(p).lower())


def _collect_inputs(folder: Path, recursive: bool) -> list[Path]:
    if _is_smb_path(folder) and _smb_mounted():
        return _collect_smb_inputs(folder, recursive)
    files: list[Path] = []
    for f in iter_files(folder, recursive):
        if f.name.startswith("."):
            continue
        if f.suffix.lower() not in SUPPORTED_ALL:
            continue
        files.append(f)
    return files


def resolve_ordered_inputs(
    paths: list[str],
    *,
    recursive: bool = True,
) -> list[Path]:
    """Развернуть выбранные файлы и папки в упорядоченный список файлов."""
    if not paths:
        raise ValueError("Не выбрано ни одного файла или папки")

    inputs: list[Path] = []
    seen: set[str] = set()

    for raw in paths:
        p = Path(raw).expanduser().resolve()
        _ensure_under_allowed_roots(p)
        is_dir = False
        if _is_smb_path(p) and _smb_mounted():
            _, remote_name = _virtual_smb_remote(p)
            is_dir = remote_name is None
        elif p.is_dir():
            is_dir = True

        if is_dir:
            folder = validate_folder(str(p))
            for f in _collect_inputs(folder, recursive):
                key = str(f)
                if key not in seen:
                    seen.add(key)
                    inputs.append(f)
        else:
            fp = validate_file(str(p))
            if fp.suffix.lower() not in SUPPORTED_ALL:
                raise ValueError(f"Формат не поддерживается: {fp.name}")
            key = str(fp)
            if key not in seen:
                seen.add(key)
                inputs.append(fp)

    if not inputs:
        raise ValueError("Нет поддерживаемых файлов для конвертации")
    return inputs


def resolve_ordered_inputs_with_format(
    paths: list[str],
    *,
    recursive: bool = True,
) -> list[dict]:
    """Развернуть выбор в список файлов с информацией о формате."""
    files = resolve_ordered_inputs(paths, recursive=recursive)
    out: list[dict] = []
    for f in files:
        size = _smb_file_size(f) if _is_smb_path(f) and _smb_mounted() else None
        info = inspect_file_format(f, file_size=size)
        out.append(
            {
                "path": str(f),
                "name": f.name,
                "parent": f.parent.name,
                **info.to_dict(),
            }
        )
    return out


def convert_folder(
    folder_path: str,
    recursive: bool = True,
    *,
    merge: bool = False,
    output_name: str = "сборка.pdf",
    windows_cad_ip: str = "",
    number_pages: bool = False,
    numbering_from_page: int = 1,
    numbering_start: int = 1,
) -> dict:
    folder = validate_folder(folder_path)
    inputs = _collect_inputs(folder, recursive)

    if merge:
        from_page = numbering_from_page if number_pages else None
        start_num = numbering_start if number_pages else 1
        return _convert_folder_merged(
            folder,
            inputs,
            output_name,
            recursive,
            windows_cad_ip=windows_cad_ip,
            numbering_from_page=from_page,
            numbering_start=start_num,
        )

    results = []
    stats = {"ok": 0, "skipped": 0, "error": 0}

    for f in inputs:
        check_cancelled()
        item = convert_file_in_place(f, windows_cad_ip=windows_cad_ip)
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


def _convert_merge_part(idx: int, src: Path, tmp: Path, windows_cad_ip: str = "") -> tuple[int, Path | None, dict]:
    part = tmp / f"{idx:04d}.pdf"
    h = get_file_content_hash(src)
    cache_dir = get_local_cache_dir(src)
    cached_pdf = cache_dir / f"{h}.pdf"

    # Пытаемся взять готовый PDF из локального кэша
    if cached_pdf.exists():
        try:
            shutil.copy2(cached_pdf, part)
            return idx, part, {
                "source": str(src),
                "pdf": str(part),
                "status": "ok",
                "message": "Включён в сборку (из кэша)",
                "hash": h,
            }
        except Exception:
            pass

    # Стандартная конвертация
    try:
        _convert_source_to_temp_pdf(src, part, windows_cad_ip=windows_cad_ip)
        # Сохраняем результат в кэш для последующих сборок
        try:
            shutil.copy2(part, cached_pdf)
        except Exception:
            pass
        return idx, part, {
            "source": str(src),
            "pdf": str(part),
            "status": "ok",
            "message": "Включён в сборку",
            "hash": h,
        }
    except Exception as e:
        return idx, None, {
            "source": str(src),
            "pdf": None,
            "status": "error",
            "message": str(e),
            "hash": h,
        }


def _convert_folder_merged(
    folder: Path,
    inputs: list[Path],
    output_name: str,
    recursive: bool,
    *,
    windows_cad_ip: str = "",
    numbering_from_page: int | None = None,
    numbering_start: int = 1,
    download_to: Path | None = None,
) -> dict:
    if not inputs:
        raise ValueError("В папке нет поддерживаемых файлов для сборки")

    safe_name = Path(output_name).name or "сборка.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"

    merged_path = folder / safe_name
    tmp = Path(tempfile.mkdtemp(prefix="cvt_merge_"))
    local_merged = download_to if download_to else (tmp / safe_name)
    results: list[dict] = []
    pdf_parts: list[Path] = []
    stats = {"ok": 0, "skipped": 0, "error": 0}

    try:
        part_results = _run_merge_parts(inputs, tmp, windows_cad_ip=windows_cad_ip)

        part_results.sort(key=lambda x: x[0])
        for _, part, item in part_results:
            results.append(item)
            if part is not None:
                pdf_parts.append(part)
                stats["ok"] += 1
            else:
                stats["error"] += 1

        if not pdf_parts:
            details = "; ".join(
                f"{Path(r['source']).name}: {r.get('message', 'ошибка')}"
                for r in results
                if r.get("status") == "error"
            )
            raise ValueError(
                "Не удалось сконвертировать ни одного файла для сборки"
                + (f" ({details})" if details else "")
            )

        check_cancelled()
        merge_pdfs(
            pdf_parts,
            local_merged,
            numbering_from_page=numbering_from_page,
            numbering_start=numbering_start,
        )
        if download_to:
            saved_path = download_to
        else:
            saved_path = _save_merged_pdf(local_merged, merged_path)
            # Сохраняем манифест сборки (.cache.json) рядом с итоговым PDF
            try:
                manifest_path = saved_path.with_suffix(".pdf.cache.json")
                manifest_sources = {}
                for r in results:
                    if r.get("status") == "ok" and "hash" in r:
                        src_p = Path(r["source"])
                        manifest_sources[str(src_p)] = {
                            "hash": r["hash"],
                            "mtime": src_p.stat().st_mtime if src_p.exists() else 0.0
                        }
                with open(manifest_path, "w", encoding="utf-8") as mf:
                    json.dump({
                        "output_file": saved_path.name,
                        "sources": manifest_sources
                    }, mf, ensure_ascii=False, indent=2)
            except Exception:
                pass
            
        ret = {
            "folder": str(folder),
            "recursive": recursive,
            "merge": True,
            "merged_pdf": str(saved_path),
            "merged_pdf_requested": str(download_to or merged_path),
            "saved_as_alt": not download_to and str(saved_path) != str(merged_path),
            "pages_from": len(pdf_parts),
            "total": len(results),
            "stats": stats,
            "files": list(results),
            "numbering_from_page": numbering_from_page,
            "numbering_start": numbering_start,
            "download": download_to is not None,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        pdf_parts.clear()
        results.clear()
        release_memory()
        
    return ret


def convert_paths_merged_download(
    paths: list[str],
    output_name: str = "сборка.pdf",
    *,
    recursive: bool = True,
    numbering_from_page: int | None = None,
    numbering_start: int = 1,
    windows_cad_ip: str = "",
) -> tuple[Path, Path, dict]:
    """Собрать PDF во временный файл для скачивания (без записи на SMB)."""
    inputs = resolve_ordered_inputs(paths, recursive=recursive)
    if not inputs:
        raise ValueError("Нет файлов для сборки")
    safe_name = Path(output_name).name or "сборка.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    tmp_parent = Path(tempfile.mkdtemp(prefix="cvt_dl_"))
    dest = tmp_parent / safe_name
    result = _convert_folder_merged(
        inputs[0].parent,
        inputs,
        safe_name,
        False,
        windows_cad_ip=windows_cad_ip,
        numbering_from_page=numbering_from_page,
        numbering_start=numbering_start,
        download_to=dest,
    )
    return dest, tmp_parent, result


def convert_uploads_to_merged_pdf(
    sources: list[Path],
    dest: Path,
    *,
    numbering_from_page: int | None = None,
    numbering_start: int = 1,
    windows_cad_ip: str = "",
) -> dict:
    """Сконвертировать загруженные файлы и собрать в один PDF."""
    if not sources:
        raise ValueError("Не передано ни одного файла")

    tmp = Path(tempfile.mkdtemp(prefix="cvt_up_merge_"))
    results: list[dict] = []
    pdf_parts: list[Path] = []
    stats = {"ok": 0, "error": 0}

    try:
        part_results = _run_merge_parts(sources, tmp, windows_cad_ip=windows_cad_ip)

        part_results.sort(key=lambda x: x[0])
        for _, part, item in part_results:
            results.append(item)
            if part is not None:
                pdf_parts.append(part)
                stats["ok"] += 1
            else:
                stats["error"] += 1

        if not pdf_parts:
            raise ValueError("Не удалось сконвертировать ни одного файла")

        merge_pdfs(
            pdf_parts,
            dest,
            numbering_from_page=numbering_from_page,
            numbering_start=numbering_start,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "merged_pdf": str(dest),
        "pages_from": len(pdf_parts),
        "stats": stats,
        "files": results,
        "numbering_from_page": numbering_from_page,
        "numbering_start": numbering_start,
    }

def number_pdf_file(
    file_path: str,
    numbering_from_page: int = 1,
    numbering_start: int = 1,
) -> dict:
    """Пронумеровать существующий PDF файл на сервере."""
    fp = validate_file(file_path)
    if fp.suffix.lower() != ".pdf":
        raise ValueError("Файл не является PDF")

    _apply_pdf_numbering(fp, from_page=numbering_from_page, start=numbering_start)

    return {
        "status": "ok",
        "file_path": str(fp),
        "numbering_from_page": numbering_from_page,
        "numbering_start": numbering_start,
    }
