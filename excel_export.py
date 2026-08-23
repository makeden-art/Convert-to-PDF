"""
Excel -> PDF через ExportAsFixedFormat (без Adobe).

Алгоритм:
1. Авто-выбор листов (auto_select_sheets).
2. Экспорт каждого листа отдельно (From/To) — иначе FitToPages игнорируется.
3. Склейка в один PDF, если листов несколько.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pythoncom
import win32com.client as win32
from pypdf import PdfReader, PdfWriter

XL_TYPE_PDF = 0
XL_QUALITY_STANDARD = 0
XL_SHEET_VISIBLE = -1

# Порог: visible_rows / hidden_rows > REPORT_RATIO → полный отчёт (все листы)
REPORT_RATIO = 1.5


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def _used_rows(ws) -> int:
    return ws.UsedRange.Rows.Count


def _pdf_is_empty(pdf_path: Path, min_size: int = 10_000) -> bool:
    """PDF слишком маленький или без текста — вероятно пустой экспорт."""
    if not pdf_path.exists() or pdf_path.stat().st_size < min_size:
        return True
    reader = PdfReader(str(pdf_path))
    text = sum(len(page.extract_text() or "") for page in reader.pages)
    return text < 20


def _fallback_hidden_sheet(wb, visible_index: int) -> int | None:
    """Первый обычный скрытый лист (не veryHidden) — часто это печатная форма."""
    for i in range(1, wb.Worksheets.Count + 1):
        if i == visible_index:
            continue
        ws = wb.Worksheets(i)
        if ws.Visible == 0:  # xlSheetHidden
            return i
    return None


def _pick_best_single_sheet(wb, preferred_index: int, pdf_path: Path) -> int:
    """Экспорт одного листа с fallback, если PDF пустой."""
    _export_range(wb, pdf_path, preferred_index, preferred_index)
    if not _pdf_is_empty(pdf_path):
        return preferred_index

    alt = _fallback_hidden_sheet(wb, preferred_index)
    if alt is not None:
        _export_range(wb, pdf_path, alt, alt)
        if not _pdf_is_empty(pdf_path):
            return alt
    return preferred_index


def auto_select_sheets(wb) -> list[int]:
    """
    Автовыбор листов для экспорта.

    - Несколько видимых листов → все видимые.
    - Один видимый + скрытые:
        visible_rows / hidden_rows > 1.5 → все листы (полный отчёт, напр. 2_1_1 → 3 стр.)
        иначе → только видимый (напр. 2_9_27 → 1 стр.)
    - Нет видимых → все листы.
    """
    total = wb.Worksheets.Count
    visible = [
        i for i in range(1, total + 1) if wb.Worksheets(i).Visible == XL_SHEET_VISIBLE
    ]
    hidden = [
        i for i in range(1, total + 1) if wb.Worksheets(i).Visible != XL_SHEET_VISIBLE
    ]

    if len(visible) > 1:
        return visible
    if len(visible) == 1:
        if not hidden:
            return visible
        vis_ws = wb.Worksheets(visible[0])
        hid_ws = wb.Worksheets(hidden[0])
        ratio = _used_rows(vis_ws) / max(_used_rows(hid_ws), 1)
        if ratio > REPORT_RATIO:
            return list(range(1, total + 1))
        return visible
    return list(range(1, total + 1))


def _sheet_indexes(
    wb,
    sheet_name: str | None = None,
    *,
    all_sheets: bool = False,
    visible_only: bool = False,
) -> list[int]:
    if sheet_name:
        return [wb.Worksheets(sheet_name).Index]
    if all_sheets:
        return list(range(1, wb.Worksheets.Count + 1))
    if visible_only:
        visible = [
            i
            for i in range(1, wb.Worksheets.Count + 1)
            if wb.Worksheets(i).Visible == XL_SHEET_VISIBLE
        ]
        if not visible:
            raise ValueError("В книге нет видимых листов")
        return visible
    return auto_select_sheets(wb)


def _open_excel():
    return win32.gencache.EnsureDispatch("Excel.Application")


def _export_range(wb, pdf_path: Path, from_sheet: int, to_sheet: int) -> None:
    wb.ExportAsFixedFormat(
        Type=XL_TYPE_PDF,
        Filename=str(pdf_path.resolve()),
        Quality=XL_QUALITY_STANDARD,
        IncludeDocProperties=True,
        IgnorePrintAreas=False,
        From=from_sheet,
        To=to_sheet,
        OpenAfterPublish=False,
    )


def excel_to_pdf(
    excel_path: Path,
    pdf_path: Path | None = None,
    sheet_name: str | None = None,
    *,
    all_sheets: bool = False,
    visible_only: bool = False,
) -> Path:
    excel_path = excel_path.resolve()
    if not excel_path.exists():
        raise FileNotFoundError(excel_path)

    pdf_path = (pdf_path or excel_path.with_suffix(".pdf")).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    excel = None
    wb = None
    tmp_dir = pdf_path.parent / f".tmp_{pdf_path.stem}"
    parts: list[Path] = []

    try:
        excel = _open_excel()
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False

        wb = excel.Workbooks.Open(str(excel_path), UpdateLinks=0, ReadOnly=False)
        sheet_indexes = _sheet_indexes(
            wb,
            sheet_name,
            all_sheets=all_sheets,
            visible_only=visible_only,
        )

        if len(sheet_indexes) == 1:
            _pick_best_single_sheet(wb, sheet_indexes[0], pdf_path)
            return pdf_path

        tmp_dir.mkdir(parents=True, exist_ok=True)
        for idx, sheet_index in enumerate(sheet_indexes, start=1):
            part = tmp_dir / f"sheet_{idx}.pdf"
            _export_range(wb, part, sheet_index, sheet_index)
            parts.append(part)

        writer = PdfWriter()
        for part in parts:
            for page in PdfReader(str(part)).pages:
                writer.add_page(page)
        with pdf_path.open("wb") as f:
            writer.write(f)
        return pdf_path
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        for part in parts:
            part.unlink(missing_ok=True)
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except OSError:
                pass
        pythoncom.CoUninitialize()


def convert_files(
    inputs: list[Path],
    output_dir: Path | None = None,
    *,
    all_sheets: bool = False,
    visible_only: bool = False,
) -> list[tuple[Path, Path, int]]:
    """Пакетная обработка. Возвращает [(xlsx, pdf, pages), ...]."""
    out_dir = (output_dir or Path("output")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[Path, Path, int]] = []

    for xlsx in inputs:
        pdf = out_dir / f"{xlsx.stem}.pdf"
        result = excel_to_pdf(
            xlsx,
            pdf,
            all_sheets=all_sheets,
            visible_only=visible_only,
        )
        results.append((xlsx, result, page_count(result)))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Excel -> PDF (авто-алгоритм, без Adobe)")
    parser.add_argument(
        "excel",
        type=Path,
        nargs="*",
        help="Файлы .xlsx (пусто = все .xlsx в текущей папке)",
    )
    parser.add_argument("-o", "--output", type=Path, default=None, help="PDF-файл или папка output")
    parser.add_argument("-s", "--sheet", default=None, help="Конкретный лист по имени")
    parser.add_argument("--all-sheets", action="store_true", help="Все листы принудительно")
    parser.add_argument(
        "--visible-only",
        action="store_true",
        help="Только видимые листы принудительно",
    )
    args = parser.parse_args()

    if args.sheet and (args.all_sheets or args.visible_only):
        print("Нельзя совмещать --sheet с --all-sheets / --visible-only", file=sys.stderr)
        return 1

    files = args.excel or sorted(Path(".").glob("*.xlsx"))
    if not files:
        print("Нет .xlsx файлов", file=sys.stderr)
        return 1

    errors = 0
    out_dir = args.output if args.output and not str(args.output).lower().endswith(".pdf") else None

    for xlsx in files:
        try:
            xlsx_path = Path(xlsx)
            if args.output and str(args.output).lower().endswith(".pdf") and len(files) == 1:
                out = args.output
            elif out_dir:
                out = out_dir / f"{xlsx_path.stem}.pdf"
            else:
                out = Path("output") / f"{xlsx_path.stem}.pdf"

            pdf = excel_to_pdf(
                xlsx_path,
                out,
                args.sheet,
                all_sheets=args.all_sheets,
                visible_only=args.visible_only,
            )
            print(f"OK: {xlsx_path.name} -> {pdf} ({page_count(pdf)} стр.)")
        except Exception as exc:  # noqa: BLE001
            print(f"Ошибка [{Path(xlsx).name}]: {exc}", file=sys.stderr)
            errors += 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
