"""Определение реального формата файла по сигнатурам (не только по расширению)."""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

EXTENSION_TO_FORMAT: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".xls": "xls",
    ".xlsx": "xlsx",
    ".odt": "odt",
    ".ods": "ods",
    ".rtf": "rtf",
    ".dwg": "dwg",
    ".dxf": "dxf",
}

FORMAT_META: dict[str, dict[str, str]] = {
    "pdf": {"label": "PDF", "icon": "📄"},
    "doc": {"label": "Word (DOC)", "icon": "📝"},
    "docx": {"label": "Word (DOCX)", "icon": "📝"},
    "xls": {"label": "Excel (XLS)", "icon": "📊"},
    "xlsx": {"label": "Excel (XLSX)", "icon": "📊"},
    "odt": {"label": "OpenDocument Text", "icon": "📃"},
    "ods": {"label": "OpenDocument Sheet", "icon": "📊"},
    "rtf": {"label": "RTF", "icon": "📃"},
    "dwg": {"label": "AutoCAD (DWG)", "icon": "📐"},
    "dxf": {"label": "AutoCAD (DXF)", "icon": "📐"},
    "ole": {"label": "OLE (старый Office)", "icon": "📦"},
    "zip": {"label": "ZIP-архив", "icon": "🗜"},
    "unknown": {"label": "Неизвестный", "icon": "❓"},
}


@dataclass
class FormatInfo:
    detected: str
    label: str
    icon: str
    extension: str
    expected: str | None
    extension_ok: bool
    valid: bool
    source: str
    error: str | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "format": self.detected,
            "format_label": self.label,
            "format_icon": self.icon,
            "extension": self.extension,
            "expected_format": self.expected,
            "extension_ok": self.extension_ok,
            "format_valid": self.valid,
            "format_source": self.source,
            "format_error": self.error,
            "format_warning": self.warning,
        }


def format_from_extension(ext: str) -> str | None:
    return EXTENSION_TO_FORMAT.get(ext.lower())


def _meta(fmt: str) -> tuple[str, str]:
    m = FORMAT_META.get(fmt, FORMAT_META["unknown"])
    return m["label"], m["icon"]


def _compatible_formats(detected: str, expected: str | None) -> bool:
    if not expected:
        return detected != "unknown"
    if detected == expected:
        return True
    if detected == "ole" and expected in ("doc", "xls"):
        return True
    return False


def _result(
    detected: str,
    extension: str,
    *,
    valid: bool = True,
    source: str = "magic",
    error: str | None = None,
    warning: str | None = None,
) -> FormatInfo:
    label, icon = _meta(detected)
    expected = format_from_extension(extension)
    extension_ok = _compatible_formats(detected, expected)
    if expected and not extension_ok and valid:
        exp_label, _ = _meta(expected)
        warning = warning or (
            f"Расширение {extension} ({exp_label}), фактически {label}"
        )
    elif detected == "ole" and expected in ("doc", "xls"):
        label, icon = _meta(expected)
        detected = expected
    return FormatInfo(
        detected=detected,
        label=label,
        icon=icon,
        extension=extension,
        expected=expected,
        extension_ok=extension_ok,
        valid=valid,
        source=source,
        error=error,
        warning=warning,
    )


def inspect_from_extension_only(extension: str) -> FormatInfo:
    fmt = format_from_extension(extension)
    if not fmt:
        return FormatInfo(
            detected="unknown",
            label=FORMAT_META["unknown"]["label"],
            icon=FORMAT_META["unknown"]["icon"],
            extension=extension,
            expected=None,
            extension_ok=False,
            valid=False,
            source="extension",
            error=f"Расширение {extension} не поддерживается",
        )
    label, icon = _meta(fmt)
    return FormatInfo(
        detected=fmt,
        label=label,
        icon=icon,
        extension=extension,
        expected=fmt,
        extension_ok=True,
        valid=True,
        source="extension",
        warning="Формат определён только по расширению",
    )


def _detect_zip_subtype(data: bytes) -> tuple[str, str | None]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" in names or any(n.startswith("word/") for n in names):
                return "docx", None
            if "xl/workbook.xml" in names or any(n.startswith("xl/") for n in names):
                return "xlsx", None
            if "[Content_Types].xml" in names:
                try:
                    ct = zf.read("[Content_Types].xml").decode("utf-8", errors="ignore").lower()
                except Exception:
                    ct = ""
                if "wordprocessingml" in ct:
                    return "docx", None
                if "spreadsheetml" in ct:
                    return "xlsx", None
                if "presentationml" in ct:
                    return "docx", None
            if "mimetype" in names:
                try:
                    mt = zf.read("mimetype").decode("utf-8", errors="ignore").strip().lower()
                except Exception:
                    mt = ""
                if "opendocument.text" in mt:
                    return "odt", None
                if "opendocument.spreadsheet" in mt:
                    return "ods", None
            return "zip", "ZIP-архив неизвестного формата (не DOCX/XLSX/ODT/ODS)"
    except zipfile.BadZipFile:
        return "unknown", "Файл повреждён или не является ZIP (ожидался DOCX/XLSX/ODT/ODS)"


def _detect_ole_subtype(data: bytes) -> str:
    sample = data[:8192].lower()
    if b"word" in sample or b"msword" in sample:
        return "doc"
    if b"excel" in sample or b"workbook" in sample or b"xl" in sample:
        return "xls"
    return "ole"


def _is_dxf(data: bytes) -> bool:
    try:
        head = data[:512].decode("utf-8", errors="ignore").lstrip()
    except Exception:
        return False
    if not head:
        return False
    if head.startswith("0"):
        return True
    upper = head.upper()
    return upper.startswith("SECTION") or "\nSECTION" in upper[:120]


def _is_dwg(data: bytes) -> bool:
    if len(data) < 6:
        return False
    if data[:4] == b"AC10":
        return True
    if data[:3] == b"AC1":
        return True
    return False


def detect_format_from_bytes(data: bytes, extension: str) -> FormatInfo:
    ext = extension.lower() if extension else ""

    if not data:
        label, icon = _meta("unknown")
        expected = format_from_extension(ext)
        return FormatInfo(
            detected="unknown",
            label=label,
            icon=icon,
            extension=ext,
            expected=expected,
            extension_ok=False,
            valid=False,
            source="magic",
            error="Файл пустой или нечитаемый",
        )

    if data.startswith(b"%PDF"):
        return _result("pdf", ext)

    if data[:5].lower() == b"{\\rtf":
        return _result("rtf", ext)

    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        detected = _detect_ole_subtype(data)
        if ext == ".docx" and detected in ("doc", "ole"):
            return _result(
                "doc",
                ext,
                warning="Файл .docx фактически в старом формате Word (.doc)",
            )
        if ext == ".xlsx" and detected in ("xls", "ole"):
            return _result(
                "xls",
                ext,
                warning="Файл .xlsx фактически в старом формате Excel (.xls)",
            )
        return _result(detected, ext)

    if data[:4] == b"PK\x03\x04":
        detected, err = _detect_zip_subtype(data)
        if err:
            office_zip = format_from_extension(ext)
            if office_zip in ("docx", "xlsx", "odt", "ods"):
                return _result(
                    office_zip,
                    ext,
                    warning=(
                        f"{err}; сигнатура ZIP и расширение {ext} "
                        "(центральный каталог мог не попасть в выборку)"
                    ),
                )
            return _result(
                detected,
                ext,
                valid=False,
                error=err,
            )
        return _result(detected, ext)

    if _is_dwg(data):
        return _result("dwg", ext)

    if _is_dxf(data):
        return _result("dxf", ext)

    expected = format_from_extension(ext)
    if expected:
        exp_label, _ = _meta(expected)
        return FormatInfo(
            detected="unknown",
            label=FORMAT_META["unknown"]["label"],
            icon=FORMAT_META["unknown"]["icon"],
            extension=ext,
            expected=expected,
            extension_ok=False,
            valid=False,
            source="magic",
            error=(
                f"Не удалось определить формат. Ожидался {exp_label} "
                f"({ext}), содержимое не распознано"
            ),
        )

    return FormatInfo(
        detected="unknown",
        label=FORMAT_META["unknown"]["label"],
        icon=FORMAT_META["unknown"]["icon"],
        extension=ext,
        expected=None,
        extension_ok=False,
        valid=False,
        source="magic",
        error="Формат файла не распознан",
    )


def _extension_fallback(ext: str, *, reason: str) -> FormatInfo:
    """Доверять расширению, если magic не сработал (часто на SMB)."""
    info = inspect_from_extension_only(ext)
    info.warning = reason
    return info


def validation_error_message(info: FormatInfo, filename: str) -> str | None:
    if not info.valid:
        return f"«{filename}»: {info.error or 'неверный или повреждённый файл'}"
    if not info.extension_ok and info.expected:
        exp_label, _ = _meta(info.expected)
        return (
            f"«{filename}»: расширение {info.extension} ({exp_label}), "
            f"фактически {info.label}"
        )
    return None
