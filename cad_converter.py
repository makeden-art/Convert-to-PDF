"""Конвертация DWG/DXF в PDF: ODA DWG→PDF, иначе DWG→DXF→PDF (ezdxf, по листу на страницу)."""
from __future__ import annotations

import gc
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from job_control import check_cancelled, run_monitored

logger = logging.getLogger("convert.cad")

CAD_EXTENSIONS = {".dwg", ".dxf"}
CAD_RENDER_DPI = max(72, int(os.getenv("CONVERT_CAD_DPI", "150")))
ODA_DXF_TIMEOUT_SEC = int(os.getenv("CONVERT_ODA_DXF_TIMEOUT_SEC", "180"))
ODA_PDF_TIMEOUT_SEC = int(os.getenv("CONVERT_ODA_PDF_TIMEOUT_SEC", "300"))


def oda_available() -> bool:
    return shutil.which("ODAFileConverter") is not None


def _oda_convert(
    input_path: Path,
    out_format: str,
    *,
    timeout: int,
    glob_pattern: str,
) -> Path:
    if not oda_available():
        raise RuntimeError("ODAFileConverter не установлен в контейнере.")

    with tempfile.TemporaryDirectory(prefix="oda_in_") as in_dir, tempfile.TemporaryDirectory(
        prefix="oda_out_"
    ) as out_dir:
        shutil.copy2(input_path, Path(in_dir) / input_path.name)
        cmd = [
            "xvfb-run",
            "-a",
            "ODAFileConverter",
            in_dir,
            out_dir,
            "ACAD2018",
            out_format,
            "0",
            "1",
            glob_pattern,
        ]
        try:
            result = run_monitored(cmd, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Превышено время ожидания ODA ({out_format}, {timeout} сек)."
            ) from e

        if result.returncode != 0:
            raise RuntimeError(
                f"ODAFileConverter ({out_format}): {(result.stderr or result.stdout or '').strip()}"
            )

        ext = out_format.lower()
        if ext == "dxf":
            out_files = list(Path(out_dir).glob("*.dxf"))
        elif ext == "pdf":
            out_files = list(Path(out_dir).glob("*.pdf"))
        else:
            out_files = list(Path(out_dir).glob(f"*.{ext}"))

        if not out_files:
            raise RuntimeError(f"ODA не создал {out_format} для {input_path.name}.")

        dest_dir = Path(tempfile.mkdtemp(prefix=f"oda_{ext}_"))
        dest = dest_dir / out_files[0].name
        shutil.copy2(out_files[0], dest)
        return dest


def convert_dwg_to_pdf_oda(input_file: str) -> Path:
    """DWG → PDF напрямую через ODA (лучшее качество для чертежей с листами)."""
    input_path = Path(input_file)
    if input_path.suffix.lower() != ".dwg":
        raise ValueError("convert_dwg_to_pdf_oda ожидает .dwg")
    return _oda_convert(
        input_path,
        "PDF",
        timeout=ODA_PDF_TIMEOUT_SEC,
        glob_pattern="*.dwg",
    )


def convert_dwg_to_dxf(input_file: str) -> Path:
    """DWG → DXF через ODAFileConverter."""
    input_path = Path(input_file)
    if input_path.suffix.lower() != ".dwg":
        raise ValueError("convert_dwg_to_dxf ожидает .dwg")
    return _oda_convert(
        input_path,
        "DXF",
        timeout=ODA_DXF_TIMEOUT_SEC,
        glob_pattern="*.dwg",
    )


def _load_dxf_document(dxf_path: Path):
    import ezdxf
    from ezdxf import recover

    try:
        return ezdxf.readfile(str(dxf_path))
    except ezdxf.DXFError:
        doc, _ = recover.readfile(str(dxf_path))
        return doc


def _paper_layouts(doc) -> list:
    layouts = []
    for name in doc.layouts.names():
        if name.upper() != "MODEL":
            layouts.append(doc.layouts.get(name))
    return layouts


def _render_targets(doc) -> list:
    """Каждый лист (paperspace) — отдельная страница PDF."""
    paper = _paper_layouts(doc)
    return paper if paper else [doc.modelspace()]


def _layout_figsize(layout) -> tuple[float, float]:
    """Размер страницы из настроек листа или A3 альбом по умолчанию."""
    try:
        page = layout.page_setup
        width = float(getattr(page, "paper_width", 0) or 0)
        height = float(getattr(page, "paper_height", 0) or 0)
        if width > 1 and height > 1:
            # DXF: мм → дюймы для matplotlib
            w_in = width / 25.4
            h_in = height / 25.4
            if w_in > 0.5 and h_in > 0.5:
                return (w_in, h_in)
    except Exception:
        pass
    return (16.54, 11.69)  # A3 landscape


class SafeFrontend:
    """Обёртка над ezdxf Frontend: пропускает битые сущности в блоках (MLEADER и т.п.)."""

    def __init__(self, frontend) -> None:
        self._frontend = frontend
        self._orig_draw_entity = frontend.draw_entity
        self._orig_draw_composite_entity = frontend.draw_composite_entity
        self._orig_draw_entities = frontend.draw_entities

    def draw_layout(self, layout, finalize: bool = True) -> None:
        self._frontend.draw_layout(layout, finalize=finalize)

    def draw_entities(self, entities: Iterable, filter_func=None) -> None:
        from ezdxf.addons.drawing.frontend import _draw_entities

        safe = []
        for entity in entities:
            try:
                safe.append(entity)
            except Exception:
                continue
        if safe:
            _draw_entities(self._frontend, self._frontend.ctx, safe, filter_func=filter_func)

    def draw_entity(self, entity, properties) -> None:
        try:
            self._orig_draw_entity(entity, properties)
        except Exception:
            self._frontend.skip_entity(entity, "render error")

    def draw_composite_entity(self, entity, properties) -> None:
        try:
            self._orig_draw_composite_entity(entity, properties)
        except Exception:
            self._frontend.skip_entity(entity, "composite render error")


def _patch_frontend(frontend) -> SafeFrontend:
    safe = SafeFrontend(frontend)
    import types

    frontend.draw_entities = types.MethodType(SafeFrontend.draw_entities, safe)  # type: ignore[method-assign]
    frontend.draw_entity = types.MethodType(SafeFrontend.draw_entity, safe)  # type: ignore[method-assign]
    frontend.draw_composite_entity = types.MethodType(  # type: ignore[method-assign]
        SafeFrontend.draw_composite_entity, safe
    )
    return safe


def _render_single_layout(doc, layout, ax) -> None:
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    frontend = Frontend(ctx, out)
    _patch_frontend(frontend)
    frontend.draw_layout(layout)


def convert_dxf_to_pdf(dxf_path: Path, pdf_path: Path) -> Path:
    """Рендер DXF в PDF: один layout = одна страница (без наложения листов)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    doc = _load_dxf_document(dxf_path)
    render_targets = _render_targets(doc)
    logger.info(
        "DXF %s: рендер %d layout(s), dpi=%s",
        dxf_path.name,
        len(render_targets),
        CAD_RENDER_DPI,
    )

    with PdfPages(str(pdf_path)) as pdf:
        for layout in render_targets:
            check_cancelled()
            figsize = _layout_figsize(layout)
            fig = plt.figure(figsize=figsize, dpi=CAD_RENDER_DPI)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_aspect("equal")
            ax.axis("off")
            try:
                _render_single_layout(doc, layout, ax)
                pdf.savefig(fig, bbox_inches="tight", pad_inches=0.05)
            finally:
                plt.close(fig)

    plt.close("all")
    del doc
    gc.collect()

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF не создан после рендера DXF.")
    return pdf_path


def convert_cad_to_pdf(input_file: str) -> Path:
    """
    DWG/DXF → PDF.
    DWG: сначала ODA → PDF; при ошибке — DWG → DXF → ezdxf (по листу на страницу).
    """
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Ожидается DWG или DXF, получено: {suffix}")

    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"

    if suffix == ".dwg":
        try:
            oda_pdf = convert_dwg_to_pdf_oda(str(input_path))
            shutil.copy2(oda_pdf, pdf_path)
            shutil.rmtree(oda_pdf.parent, ignore_errors=True)
            logger.info("DWG %s: ODA PDF OK", input_path.name)
            return pdf_path
        except Exception as e:
            logger.warning("DWG %s: ODA PDF failed (%s), fallback DXF+ezdxf", input_path.name, e)

    try:
        if suffix == ".dwg":
            dxf_path = convert_dwg_to_dxf(str(input_path))
            work_dxf = tmp / dxf_path.name
            shutil.copy2(dxf_path, work_dxf)
            shutil.rmtree(dxf_path.parent, ignore_errors=True)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)

        convert_dxf_to_pdf(work_dxf, pdf_path)
        return pdf_path
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
