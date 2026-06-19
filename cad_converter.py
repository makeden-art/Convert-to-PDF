"""Конвертация DWG/DXF в PDF: DWG→DXF через ODA (как в lisp_Nikolay), DXF→PDF через ezdxf."""
from __future__ import annotations

import gc
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CAD_EXTENSIONS = {".dwg", ".dxf"}
CAD_RENDER_DPI = max(72, int(os.getenv("CONVERT_CAD_DPI", "72")))


def oda_available() -> bool:
    return shutil.which("ODAFileConverter") is not None


def convert_dwg_to_dxf(input_file: str) -> Path:
    """DWG → DXF через ODAFileConverter (та же схема, что в lisp_Nikolay/dwg_converter.py)."""
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"Файл {input_file} не найден.")
    if input_path.suffix.lower() != ".dwg":
        raise ValueError("convert_dwg_to_dxf ожидает .dwg")

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
            "DXF",
            "0",
            "1",
            "*.dwg",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("Превышено время ожидания конвертации DWG в DXF (120 сек).") from e

        if result.returncode != 0:
            raise RuntimeError(
                f"ODAFileConverter (DWG→DXF): {(result.stderr or result.stdout or '').strip()}"
            )

        out_files = list(Path(out_dir).glob("*.dxf"))
        if not out_files:
            raise RuntimeError("DXF не создан после конвертации DWG.")

        dest = Path(tempfile.mkdtemp(prefix="dxf_")) / f"{input_path.stem}.dxf"
        shutil.copy2(out_files[0], dest)
        return dest


def convert_dxf_to_pdf(dxf_path: Path, pdf_path: Path) -> Path:
    """Рендер DXF в PDF через ezdxf + matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except ezdxf.DXFError:
        from ezdxf import recover

        doc, _ = recover.readfile(str(dxf_path))

    fig = plt.figure(figsize=(11.69, 8.27), dpi=CAD_RENDER_DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    ax.axis("off")
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)
    try:
        Frontend(ctx, out).draw_layout(doc.modelspace())
        fig.savefig(str(pdf_path), format="pdf", bbox_inches="tight", pad_inches=0.05)
    finally:
        plt.close(fig)
        plt.close("all")
        del doc
        gc.collect()

    if not pdf_path.exists():
        raise RuntimeError("PDF не создан после рендера DXF.")
    return pdf_path


def convert_cad_to_pdf(input_file: str) -> Path:
    """
    DWG/DXF → PDF.
    DWG сначала переводится в DXF через ODA, затем DXF рендерится в PDF.
    """
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Ожидается DWG или DXF, получено: {suffix}")

    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    try:
        if suffix == ".dwg":
            dxf_path = convert_dwg_to_dxf(str(input_path))
            work_dxf = tmp / dxf_path.name
            shutil.copy2(dxf_path, work_dxf)
            shutil.rmtree(dxf_path.parent, ignore_errors=True)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)

        pdf_path = tmp / f"{input_path.stem}.pdf"
        convert_dxf_to_pdf(work_dxf, pdf_path)
        return pdf_path
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
