"""Р С™Р С•Р Р…Р Р†Р ВµРЎР‚РЎвЂљР В°РЎвЂ Р С‘РЎРЏ DWG/DXF Р Р† PDF: ODA, Р В·Р В°РЎвЂљР ВµР С РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚ Р С—Р С• РЎР‚Р В°Р СР С”Р В°Р С Р С‘Р В»Р С‘ layout."""
from __future__ import annotations

import gc
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from frame_detect import CadFrame, choose_render_frames, detect_frames_in_doc, frames_summary
from job_control import check_cancelled, run_monitored

logger = logging.getLogger("convert.cad")
logging.getLogger("ezdxf").setLevel(logging.ERROR)


# Monkey patch ezdxf block reference explosion to catch transform/scaling exceptions gracefully.
# This prevents a single buggy entity (like a ZeroDivisionError in a MLEADER) from breaking 
# rendering for the entire block (such as an entire table or title stamp).
def _safe_virtual_block_reference_entities(
    block_ref,
    *,
    skipped_entity_callback=None,
    redraw_order=False,
    copy_strategy=None,
) -> Iterable:
    import sys
    import ezdxf.explode
    from ezdxf.explode import default_copy, default_logging_callback
    from ezdxf.entities import Ellipse
    from ezdxf.math import NonUniformScalingError, InsertTransformationError

    if copy_strategy is None:
        copy_strategy = default_copy

    assert block_ref.dxftype() == "INSERT"
    skipped_entity_callback = skipped_entity_callback or default_logging_callback

    def disassemble(layout) -> Iterable:
        for entity in layout.entities_in_redraw_order() if redraw_order else layout:
            if entity.dxftype() == "ATTDEF":
                continue
            try:
                copy = entity.copy(copy_strategy=copy_strategy)
            except Exception as e:
                skipped_entity_callback(entity, f"non copyable: {e}")
                if hasattr(entity, "virtual_entities"):
                    try:
                        yield from entity.virtual_entities()
                    except Exception:
                        pass
            else:
                if hasattr(copy, "remove_association"):
                    copy.remove_association()
                yield copy

    def transform(entities):
        for entity in entities:
            try:
                entity.transform(m)
            except NotImplementedError:
                skipped_entity_callback(entity, "non transformable")
            except NonUniformScalingError:
                dxftype = entity.dxftype()
                if dxftype in {"ARC", "CIRCLE"}:
                    try:
                        if abs(entity.dxf.radius) > 1e-12:
                            yield Ellipse.from_arc(entity).transform(m)
                        else:
                            skipped_entity_callback(entity, "Invalid radius")
                    except Exception as e:
                        skipped_entity_callback(entity, f"arc transform error: {e}")
                elif dxftype in {"LWPOLYLINE", "POLYLINE"}:
                    try:
                        yield from transform(entity.virtual_entities())
                    except Exception as e:
                        skipped_entity_callback(entity, f"polyline virtual_entities error: {e}")
                else:
                    skipped_entity_callback(entity, "unsupported non-uniform scaling")
            except InsertTransformationError:
                try:
                    yield from transform(
                        _safe_virtual_block_reference_entities(
                            entity, skipped_entity_callback=skipped_entity_callback, copy_strategy=copy_strategy
                        )
                    )
                except Exception as e:
                    skipped_entity_callback(entity, f"insert transform error: {e}")
            except Exception as e:
                # Bypasses ZeroDivisionError and other math/structure exceptions inside specific entities
                skipped_entity_callback(entity, f"transform error: {e}")
            else:
                yield entity

    m = block_ref.matrix44()
    block_layout = block_ref.block()
    if block_layout is None:
        return

    yield from transform(disassemble(block_layout))


def _safe_draw_viewports(frontend, viewports) -> None:
    # Sort viewports by status
    viewports.sort(key=lambda e: e.dxf.status)
    # Remove all invisible viewports:
    viewports = [vp for vp in viewports if vp.dxf.status > 0]
    if not viewports:
        return

    # Find the paper space viewport (ID == 1)
    ps_vp_idx = None
    for idx, vp in enumerate(viewports):
        if vp.dxf.get("id") == 1:
            ps_vp_idx = idx
            break

    if ps_vp_idx is not None:
        viewports.pop(ps_vp_idx)
    else:
        # Fallback to ezdxf's original behavior if ID 1 is not found
        if viewports[0].dxf.get("status", 1) == 1:
            viewports.pop(0)

    # Draw all remaining viewports
    for viewport in viewports:
        try:
            frontend.draw_viewport(viewport)
        except Exception as e:
            logger.warning("Error rendering viewport %s: %s", viewport.dxf.handle, e)


# Apply the monkey patch dynamically to all imported ezdxf modules containing it
try:
    import sys
    import ezdxf.explode
    import ezdxf.entities.insert
    import ezdxf.addons.drawing.frontend
    for name, module in list(sys.modules.items()):
        if name.startswith("ezdxf") and hasattr(module, "virtual_block_reference_entities"):
            setattr(module, "virtual_block_reference_entities", _safe_virtual_block_reference_entities)
    ezdxf.addons.drawing.frontend._draw_viewports = _safe_draw_viewports
    logger.info("Successfully applied safe ezdxf block reference and viewport monkey-patches.")
except Exception as e:
    logger.error("Failed to apply ezdxf monkey-patch: %s", e)

CAD_EXTENSIONS = {".dwg", ".dxf"}
CAD_RENDER_DPI = max(72, int(os.getenv("CONVERT_CAD_DPI", "150")))
CAD_RENDER_MODE = os.getenv("CONVERT_CAD_RENDER_MODE", "auto").strip().lower()
ODA_DXF_TIMEOUT_SEC = int(os.getenv("CONVERT_ODA_DXF_TIMEOUT_SEC", "180"))
ODA_PDF_TIMEOUT_SEC = int(os.getenv("CONVERT_ODA_PDF_TIMEOUT_SEC", "1800"))
CAD_FALLBACK_MIN_MB = float(os.getenv("CONVERT_CAD_FALLBACK_MIN_MB", "0"))
CAD_ALLOW_EZDXF_FALLBACK = os.getenv("CONVERT_CAD_ALLOW_EZDXF_FALLBACK", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
PREVIEW_MAX_ENTITIES = max(1000, int(os.getenv("CONVERT_PREVIEW_MAX_ENTITIES", "25000")))
PREVIEW_CAD_MAX_MB = float(os.getenv("CONVERT_PREVIEW_CAD_MAX_MB", "20"))
PREVIEW_DXF_CACHE_DIR = Path(os.getenv("CONVERT_PREVIEW_DXF_CACHE", "/tmp/cad_preview_dxf_cache"))


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
        raise RuntimeError("ODAFileConverter Р Р…Р Вµ РЎС“РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р В»Р ВµР Р… Р Р† Р С”Р С•Р Р…РЎвЂљР ВµР в„–Р Р…Р ВµРЎР‚Р Вµ.")

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
            "0",
            glob_pattern,
        ]
        try:
            result = run_monitored(cmd, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Р СџРЎР‚Р ВµР Р†РЎвЂ№РЎв‚¬Р ВµР Р…Р С• Р Р†РЎР‚Р ВµР СРЎРЏ Р С•Р В¶Р С‘Р Т‘Р В°Р Р…Р С‘РЎРЏ ODA ({out_format}, {timeout} РЎРѓР ВµР С”)."
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
            raise RuntimeError(f"ODA Р Р…Р Вµ РЎРѓР С•Р В·Р Т‘Р В°Р В» {out_format} Р Т‘Р В»РЎРЏ {input_path.name}.")

        dest_dir = Path(tempfile.mkdtemp(prefix=f"oda_{ext}_"))
        dest = dest_dir / out_files[0].name
        shutil.copy2(out_files[0], dest)
        return dest


def convert_dwg_to_pdf_oda(input_file: str) -> Path:
    """DWG РІвЂ вЂ™ PDF Р Р…Р В°Р С—РЎР‚РЎРЏР СРЎС“РЎР‹ РЎвЂЎР ВµРЎР‚Р ВµР В· ODA (Р В»РЎС“РЎвЂЎРЎв‚¬Р ВµР Вµ Р С”Р В°РЎвЂЎР ВµРЎРѓРЎвЂљР Р†Р С• Р Т‘Р В»РЎРЏ РЎвЂЎР ВµРЎР‚РЎвЂљР ВµР В¶Р ВµР в„– РЎРѓ Р В»Р С‘РЎРѓРЎвЂљР В°Р СР С‘)."""
    input_path = Path(input_file)
    if input_path.suffix.lower() != ".dwg":
        raise ValueError("convert_dwg_to_pdf_oda Р С•Р В¶Р С‘Р Т‘Р В°Р ВµРЎвЂљ .dwg")
    return _oda_convert(
        input_path,
        "PDF",
        timeout=ODA_PDF_TIMEOUT_SEC,
        glob_pattern="*.dwg",
    )


def convert_dwg_to_dxf(input_file: str) -> Path:
    """DWG РІвЂ вЂ™ DXF РЎвЂЎР ВµРЎР‚Р ВµР В· ODAFileConverter."""
    input_path = Path(input_file)
    if input_path.suffix.lower() != ".dwg":
        raise ValueError("convert_dwg_to_dxf Р С•Р В¶Р С‘Р Т‘Р В°Р ВµРЎвЂљ .dwg")
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


def _preview_dxf_cache_path(input_path: Path) -> Path:
    PREVIEW_DXF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    st = input_path.stat()
    safe_name = input_path.stem.replace("/", "_")[:80]
    return PREVIEW_DXF_CACHE_DIR / f"{st.st_mtime_ns}_{st.st_size}_{safe_name}.dxf"


def _dwg_to_dxf_for_preview(input_path: Path) -> Path:
    cached = _preview_dxf_cache_path(input_path)
    if cached.exists() and cached.stat().st_size > 0:
        logger.info("Preview DXF cache hit: %s", input_path.name)
        return cached
    dxf_path = convert_dwg_to_dxf(str(input_path))
    try:
        shutil.copy2(dxf_path, cached)
    finally:
        shutil.rmtree(dxf_path.parent, ignore_errors=True)
    return cached


def _paper_layouts(doc) -> list:
    layouts = []
    for name in doc.layouts.names():
        if name.upper() != "MODEL":
            layouts.append(doc.layouts.get(name))
    return layouts


def _layout_figsize(layout) -> tuple[float, float]:
    """Р В Р В°Р В·Р СР ВµРЎР‚ РЎРѓРЎвЂљРЎР‚Р В°Р Р…Р С‘РЎвЂ РЎвЂ№ Р С‘Р В· Р Р…Р В°РЎРѓРЎвЂљРЎР‚Р С•Р ВµР С” Р В»Р С‘РЎРѓРЎвЂљР В° Р С‘Р В»Р С‘ A3 Р В°Р В»РЎРЉР В±Р С•Р С Р С—Р С• РЎС“Р СР С•Р В»РЎвЂЎР В°Р Р…Р С‘РЎР‹."""
    try:
        page = layout.page_setup
        width = float(getattr(page, "paper_width", 0) or 0)
        height = float(getattr(page, "paper_height", 0) or 0)
        if width > 1 and height > 1:
            w_in = width / 25.4
            h_in = height / 25.4
            if w_in > 0.5 and h_in > 0.5:
                return (w_in, h_in)
    except Exception:
        pass
    return (16.54, 11.69)


def _frame_figsize(frame: CadFrame) -> tuple[float, float]:
    w_mm = abs(frame.xmax - frame.xmin)
    h_mm = abs(frame.ymax - frame.ymin)
    if frame.orientation == "landscape" and w_mm < h_mm:
        w_mm, h_mm = h_mm, w_mm
    elif frame.orientation == "portrait" and w_mm > h_mm:
        w_mm, h_mm = h_mm, w_mm
    return (max(w_mm, 50) / 25.4, max(h_mm, 50) / 25.4)


class SafeFrontend:
    """Р С›Р В±РЎвЂРЎР‚РЎвЂљР С”Р В° Р Р…Р В°Р Т‘ ezdxf Frontend: Р С—РЎР‚Р С•Р С—РЎС“РЎРѓР С”Р В°Р ВµРЎвЂљ Р В±Р С‘РЎвЂљРЎвЂ№Р Вµ РЎРѓРЎС“РЎвЂ°Р Р…Р С•РЎРѓРЎвЂљР С‘ Р Р† Р В±Р В»Р С•Р С”Р В°РЎвЂ¦."""

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
        except Exception as e:
            logger.warning("Error rendering entity %s (%s): %s", entity.dxftype(), getattr(entity, 'handle', '?'), e)
            self._frontend.skip_entity(entity, "render error")

    def draw_composite_entity(self, entity, properties) -> None:
        try:
            self._orig_draw_composite_entity(entity, properties)
        except Exception as e:
            logger.warning("Error rendering composite entity %s (%s): %s", entity.dxftype(), getattr(entity, 'handle', '?'), e)
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


def _is_entity_in_box(
    entity,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    cache: dict | None = None,
) -> bool:
    if cache is not None:
        try:
            h = entity.dxf.handle
        except Exception:
            h = id(entity)
        if h in cache:
            coords = cache[h]
        else:
            coords = None
            try:
                from ezdxf import bbox
                box = bbox.extents([entity])
                if box:
                    coords = (box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y)
            except Exception:
                pass
            cache[h] = coords
            
        if coords is None:
            return True
        exmin, exmax, eymin, eymax = coords
        return not (exmax < xmin or exmin > xmax or eymax < ymin or eymin > ymax)
        
    try:
        from ezdxf import bbox
        box = bbox.extents([entity])
        if box:
            exmin, exmax, eymin, eymax = box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y
            return not (exmax < xmin or exmin > xmax or eymax < ymin or eymin > ymax)
    except Exception:
        pass
    return True


def _modelspace_entity_count(doc) -> int:
    try:
        return len(list(doc.modelspace()))
    except Exception:
        return 0


def _render_modelspace(
    doc,
    ax,
    *,
    max_entities: int | None = None,
    crop_box: tuple[float, float, float, float] | None = None,
    bbox_cache: dict | None = None,
) -> None:
    if max_entities is not None:
        count = _modelspace_entity_count(doc)
        if count > max_entities:
            raise RuntimeError(
                f"Р РЋР В»Р С‘РЎв‚¬Р С”Р С•Р С Р СР Р…Р С•Р С–Р С• Р С•Р В±РЎР‰Р ВµР С”РЎвЂљР С•Р Р† Р Р† Р СР С•Р Т‘Р ВµР В»Р С‘ ({count} > {max_entities}) Р Т‘Р В»РЎРЏ Р С—РЎР‚Р ВµР Т‘Р С—РЎР‚Р С•РЎРѓР СР С•РЎвЂљРЎР‚Р В°. "
                "Р вЂ™РЎвЂ№Р С—Р С•Р В»Р Р…Р С‘РЎвЂљР Вµ Р С”Р С•Р Р…Р Р†Р ВµРЎР‚РЎвЂљР В°РЎвЂ Р С‘РЎР‹ Р Р† PDF Р С‘ Р С•РЎвЂљР С”РЎР‚Р С•Р в„–РЎвЂљР Вµ Р’В«PDF РЎР‚РЎРЏР Т‘Р С•Р СР’В»."
            )

    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LinePolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax, adjust_figure=False)
    config = Configuration(
        color_policy=ColorPolicy.MONOCHROME,
        line_policy=LinePolicy.APPROXIMATE,
        max_flattening_distance=0.15,
        circle_approximation_count=32,
    )
    frontend = Frontend(ctx, out, config=config)
    _patch_frontend(frontend)

    entities = doc.modelspace()
    if crop_box is not None:
        xmin, xmax, ymin, ymax = crop_box
        entities = [e for e in entities if _is_entity_in_box(e, xmin, xmax, ymin, ymax, bbox_cache)]

    frontend.draw_entities(entities)


def _layout_individual_viewport_extents(layout) -> list[dict]:
    """Return per-viewport model+paper extents for each real VIEWPORT in *layout*.

    Skips viewport id==1 (paper-space clipping viewport) and any with
    model extents > 1 000 000 units (sanity filter).

    Each entry has keys:
        model_xmin, model_xmax, model_ymin, model_ymax
        paper_w_mm, paper_h_mm
    """
    result: list[dict] = []
    for vp in layout:
        if vp.dxftype() != "VIEWPORT":
            continue
        try:
            vp_id = vp.dxf.get("id", None)
            if vp_id == 1:
                continue
            vc = vp.dxf.view_center_point
            vh = float(vp.dxf.view_height)
            pw = float(getattr(vp.dxf, "width", 0) or 0)
            ph = float(getattr(vp.dxf, "height", 0) or 0)
            if pw <= 0 or ph <= 0 or vh <= 0:
                continue
            vw = vh * (pw / ph)
            if vw > 1_000_000 or vh > 1_000_000:
                continue
            result.append({
                "model_xmin": vc.x - vw / 2,
                "model_xmax": vc.x + vw / 2,
                "model_ymin": vc.y - vh / 2,
                "model_ymax": vc.y + vh / 2,
                "paper_w_mm": pw,
                "paper_h_mm": ph,
            })
        except Exception:
            continue
    logger.info("_layout_individual_viewport_extents: %d viewports", len(result))
    return result


def _layout_viewport_model_extents(
    layout,
) -> tuple[float, float, float, float] | None:
    """Return the UNION of model-space extents of all real VIEWPORTs (preview use)."""
    vps = _layout_individual_viewport_extents(layout)
    if not vps:
        return None
    return (
        min(v["model_xmin"] for v in vps),
        max(v["model_xmax"] for v in vps),
        min(v["model_ymin"] for v in vps),
        max(v["model_ymax"] for v in vps),
    )

def _patch_mleader_zerodiv() -> None:
    """Monkey-patch ezdxf LeaderData.transform to survive zero-length dogleg vectors.

    Some DWG files contain MLEADER entities with a zero-length dogleg vector.  When
    ezdxf's draw_layout processes such an entity it calls Vec3.normalize() on a zero
    vector, raising ZeroDivisionError and aborting the whole render.  We intercept
    the transform method and silently skip the bad leaders.
    """
    try:
        from ezdxf.entities import mleader as _ml
        if getattr(_ml.LeaderData, "_patched_zerodiv", False):
            return
        _orig = _ml.LeaderData.transform

        def _safe(self, wcs):  # noqa: ANN001
            try:
                return _orig(self, wcs)
            except ZeroDivisionError:
                pass  # zero-length dogleg – skip silently

        _ml.LeaderData.transform = _safe
        _ml.LeaderData._patched_zerodiv = True
        logger.debug("_patch_mleader_zerodiv applied")
    except Exception as exc:
        logger.warning("_patch_mleader_zerodiv failed: %s", exc)


def _render_single_layout(doc, layout, ax) -> None:
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, LinePolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    _patch_mleader_zerodiv()
    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax, adjust_figure=False)
    config = Configuration(
        color_policy=ColorPolicy.MONOCHROME,
        line_policy=LinePolicy.APPROXIMATE,
        max_flattening_distance=0.15,
        circle_approximation_count=32,
    )
    frontend = Frontend(ctx, out, config=config)
    _patch_frontend(frontend)
    frontend.draw_layout(layout)



def _apply_frame_crop(ax, frame: CadFrame) -> None:
    ax.set_xlim(frame.xmin, frame.xmax)
    ax.set_ylim(frame.ymin, frame.ymax)
    ax.set_aspect("auto")
    ax.margins(0)


def _render_frame(
    doc,
    frame: CadFrame,
    ax,
    *,
    preview: bool = False,
    bbox_cache: dict | None = None,
) -> None:
    """Render *frame* onto *ax* (model-space frames only).

    Paper-space frames with viewports are handled upstream by
    _render_paper_layout_viewport_pages; this path is used as a fallback
    (preview or no viewports found).
    """
    entity_limit = PREVIEW_MAX_ENTITIES if preview else None
    crop_box = (frame.xmin, frame.xmax, frame.ymin, frame.ymax)

    if frame.source == "viewport_model":
        _render_modelspace(doc, ax, max_entities=entity_limit, crop_box=crop_box, bbox_cache=bbox_cache)
        _apply_frame_crop(ax, frame)
        return

    if frame.layout.upper() == "MODEL":
        _render_modelspace(doc, ax, max_entities=entity_limit, crop_box=crop_box, bbox_cache=bbox_cache)
        _apply_frame_crop(ax, frame)
        return


    # Paper-space layout fallback (no viewports or preview mode)
    layout = doc.layouts.get(frame.layout)
    _render_single_layout(doc, layout, ax)
    if frame.source in (
        "viewport",
        "polyline",
        "block",
        "sheet_border",
        "viewport_union",
        "stamp_frame",
    ):
        _apply_frame_crop(ax, frame)


def _render_paper_layout_viewport_pages(
    doc,
    frame: CadFrame,
    pdf_pages,
    *,
    bbox_cache: dict | None = None,
) -> bool:
    """Render each viewport in a paper-space layout as a separate PDF page.

    Returns True when at least one page was written, False if no usable viewports
    were found (caller should fall back to the normal _render_frame path).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    layout = doc.layouts.get(frame.layout)
    vp_list = _layout_individual_viewport_extents(layout)
    if not vp_list:
        return False

    logger.info("Paper layout %r: rendering %d viewports as separate pages", frame.layout, len(vp_list))
    for i, vp in enumerate(vp_list):
        check_cancelled()
        figsize = (vp["paper_w_mm"] / 25.4, vp["paper_h_mm"] / 25.4)
        fig = plt.figure(figsize=figsize, dpi=CAD_RENDER_DPI)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        crop = (vp["model_xmin"], vp["model_xmax"], vp["model_ymin"], vp["model_ymax"])
        _render_modelspace(doc, ax, crop_box=crop, bbox_cache=bbox_cache)
        ax.set_xlim(vp["model_xmin"], vp["model_xmax"])
        ax.set_ylim(vp["model_ymin"], vp["model_ymax"])
        ax.set_aspect("auto")
        ax.margins(0)
        _save_figure(pdf_pages, fig)
        plt.close(fig)
        logger.info("  Viewport %d/%d done", i + 1, len(vp_list))

    return True




def _save_figure(pdf, fig) -> None:
    pdf.savefig(fig, bbox_inches=None, pad_inches=0)





def convert_dxf_to_pdf(
    dxf_path: Path,
    pdf_path: Path,
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Р В Р ВµР Р…Р Т‘Р ВµРЎР‚ DXF Р Р† PDF: Р С—Р С• РЎР‚Р В°Р СР С”Р В°Р С (Р С—РЎР‚Р С‘Р С•РЎР‚Р С‘РЎвЂљР ВµРЎвЂљ) Р С‘Р В»Р С‘ Р С—Р С• layout."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    doc = _load_dxf_document(dxf_path)
    all_frames = detect_frames_in_doc(doc)
    render_frames = choose_render_frames(doc, all_frames) if CAD_RENDER_MODE != "layouts" else []
    use_frames = bool(render_frames) and CAD_RENDER_MODE in ("auto", "frames")

    if meta is not None:
        meta["frames_detected"] = len(all_frames)
        meta["frames_rendered"] = len(render_frames) if use_frames else 0
        meta["render_mode"] = "frames" if use_frames else "layouts"
        if all_frames:
            meta["frames"] = frames_summary(all_frames)

    bbox_cache = {}
    with PdfPages(str(pdf_path)) as pdf:
        if use_frames:
            logger.info(
                "DXF %s: РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚ %d РЎР‚Р В°Р СР С•Р С”, dpi=%s",
                dxf_path.name,
                len(render_frames),
                CAD_RENDER_DPI,
            )
            for frame in render_frames:
                check_cancelled()
                is_paper_frame = (
                    frame.layout.upper() != "MODEL"
                    and frame.source in (
                        "stamp_frame", "viewport", "viewport_union", "sheet_border",
                    )
                )
                if is_paper_frame:
                    # PRIMARY: draw_layout renders the full paper-space sheet
                    # (title block, borders, notes + all viewport model content).
                    figsize = _frame_figsize(frame)
                    fig = plt.figure(figsize=figsize, dpi=CAD_RENDER_DPI)
                    ax = fig.add_axes([0, 0, 1, 1])
                    ax.axis("off")
                    try:
                        _render_frame(doc, frame, ax, bbox_cache=bbox_cache)
                        _save_figure(pdf, fig)
                        plt.close(fig)
                        continue
                    except Exception:
                        logger.warning(
                            "draw_layout failed for %r, falling back to per-viewport",
                            frame.layout, exc_info=True,
                        )
                        plt.close(fig)
                    # FALLBACK: per-viewport pages (no title block but shows content)
                    try:
                        done = _render_paper_layout_viewport_pages(
                            doc, frame, pdf, bbox_cache=bbox_cache,
                        )
                    except Exception:
                        logger.exception(
                            "Per-viewport fallback also failed (layout=%r)", frame.layout
                        )
                        done = False
                    if done:
                        continue

                figsize = _frame_figsize(frame)
                fig = plt.figure(figsize=figsize, dpi=CAD_RENDER_DPI)
                ax = fig.add_axes([0, 0, 1, 1])
                ax.axis("off")
                try:
                    _render_frame(doc, frame, ax, bbox_cache=bbox_cache)
                    _save_figure(pdf, fig)
                except Exception:
                    logger.exception(
                        "Error rendering frame %s (layout=%r source=%r)",
                        frame, frame.layout, frame.source,
                    )
                    raise
                finally:
                    plt.close(fig)
        else:
            paper = _paper_layouts(doc)
            render_targets = paper if paper else [doc.modelspace()]
            logger.info(
                "DXF %s: РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚ %d layout(s), dpi=%s",
                dxf_path.name,
                len(render_targets),
                CAD_RENDER_DPI,
            )
            for layout in render_targets:
                check_cancelled()
                figsize = _layout_figsize(layout) if hasattr(layout, "page_setup") else (16.54, 11.69)
                fig = plt.figure(figsize=figsize, dpi=CAD_RENDER_DPI)
                ax = fig.add_axes([0, 0, 1, 1])
                ax.set_aspect("equal")
                ax.axis("off")
                try:
                    if hasattr(layout, "page_setup"):
                        _render_single_layout(doc, layout, ax)
                    else:
                        _render_modelspace(doc, ax)
                    _save_figure(pdf, fig)
                finally:
                    plt.close(fig)

    plt.close("all")
    del doc
    gc.collect()

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF Р Р…Р Вµ РЎРѓР С•Р В·Р Т‘Р В°Р Р… Р С—Р С•РЎРѓР В»Р Вµ РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚Р В° DXF.")
    return pdf_path


def _cad_render_targets(doc) -> tuple[list[Any], str, list[CadFrame]]:
    """Р В¦Р ВµР В»Р С‘ РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚Р В°: РЎР‚Р В°Р СР С”Р С‘ Р С‘Р В»Р С‘ layout'РЎвЂ№."""
    all_frames = detect_frames_in_doc(doc)
    render_frames = choose_render_frames(doc, all_frames) if CAD_RENDER_MODE != "layouts" else []
    use_frames = bool(render_frames) and CAD_RENDER_MODE in ("auto", "frames")
    if use_frames:
        return render_frames, "frames", all_frames
    paper = _paper_layouts(doc)
    targets: list[Any] = paper if paper else [doc.modelspace()]
    return targets, "layouts", all_frames


def render_cad_preview_png(
    input_file: str,
    *,
    page: int = 1,
    dpi: int | None = None,
) -> tuple[bytes, int, dict[str, Any]]:
    """Р С›Р Т‘Р С‘Р Р… Р С”Р В°Р Т‘РЎР‚ CAD (DWG/DXF) Р Р† PNG Р Т‘Р В»РЎРЏ Р С—РЎР‚Р ВµР Т‘Р С—РЎР‚Р С•РЎРѓР СР С•РЎвЂљРЎР‚Р В°. Р вЂ™Р С•Р В·Р Р†РЎР‚Р В°РЎвЂ°Р В°Р ВµРЎвЂљ (png, pages, meta)."""
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    preview_dpi = max(48, min(120, dpi or int(os.getenv("CONVERT_PREVIEW_DPI", "96"))))
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Р С›Р В¶Р С‘Р Т‘Р В°Р ВµРЎвЂљРЎРѓРЎРЏ DWG Р С‘Р В»Р С‘ DXF, Р С—Р С•Р В»РЎС“РЎвЂЎР ВµР Р…Р С•: {suffix}")

    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0
    if size_mb > PREVIEW_CAD_MAX_MB:
        raise RuntimeError(
            f"Р В¤Р В°Р в„–Р В» РЎРѓР В»Р С‘РЎв‚¬Р С”Р С•Р С Р В±Р С•Р В»РЎРЉРЎв‚¬Р С•Р в„– ({size_mb:.0f} Р СљР вЂ) Р Т‘Р В»РЎРЏ Р С—РЎР‚Р ВµР Т‘Р С—РЎР‚Р С•РЎРѓР СР С•РЎвЂљРЎР‚Р В° Р С‘РЎРѓРЎвЂ¦Р С•Р Т‘Р Р…Р С‘Р С”Р В°. "
            f"Р РЋР Р…Р В°РЎвЂЎР В°Р В»Р В° Р Р†РЎвЂ№Р С—Р С•Р В»Р Р…Р С‘РЎвЂљР Вµ Р С”Р С•Р Р…Р Р†Р ВµРЎР‚РЎвЂљР В°РЎвЂ Р С‘РЎР‹ Р Р† PDF Р Р…Р В° РЎРѓРЎвЂљРЎР‚Р В°Р Р…Р С‘РЎвЂ Р Вµ Р’В«Р С™Р С•Р Р…Р Р†Р ВµРЎР‚РЎвЂљР В°РЎвЂ Р С‘РЎРЏР’В» "
            f"(Р В»Р С‘Р СР С‘РЎвЂљ Р С—РЎР‚Р ВµР Т‘Р С—РЎР‚Р С•РЎРѓР СР С•РЎвЂљРЎР‚Р В° РІР‚вЂќ {PREVIEW_CAD_MAX_MB:.0f} Р СљР вЂ)."
        )

    tmp = Path(tempfile.mkdtemp(prefix="cad_preview_"))
    try:
        if suffix == ".dwg":
            if not oda_available():
                raise RuntimeError("ODAFileConverter Р Р…Р Вµ РЎС“РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р В»Р ВµР Р…")
            cached_dxf = _dwg_to_dxf_for_preview(input_path)
            work_dxf = tmp / cached_dxf.name
            shutil.copy2(cached_dxf, work_dxf)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)

        doc = _load_dxf_document(work_dxf)
        targets, mode, all_frames = _cad_render_targets(doc)
        total = max(1, len(targets))
        page_idx = max(1, min(page, total)) - 1
        target = targets[page_idx]

        if mode == "frames":
            frame = target
            figsize = _frame_figsize(frame)
            fig = plt.figure(figsize=figsize, dpi=preview_dpi)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.axis("off")
            try:
                _render_frame(doc, frame, ax, preview=True)
            finally:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, facecolor="white")
                plt.close(fig)
            caption = frame.label or frame.layout
            if frame.layer:
                caption = f"{caption} ({frame.layer})"
        else:
            figsize = _layout_figsize(target) if hasattr(target, "page_setup") else (16.54, 11.69)
            fig = plt.figure(figsize=figsize, dpi=preview_dpi)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_aspect("equal")
            ax.axis("off")
            try:
                if hasattr(target, "page_setup"):
                    _render_single_layout(doc, target, ax)
                else:
                    _render_modelspace(doc, ax)
            finally:
                buf = io.BytesIO()
                fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, facecolor="white")
                plt.close(fig)
            caption = getattr(target, "name", None) or "Model"

        plt.close("all")
        meta = {
            "render_mode": mode,
            "page": page_idx + 1,
            "pages": total,
            "caption": caption,
            "frames_detected": len(all_frames),
        }
        return buf.getvalue(), total, meta
    finally:
        plt.close("all")
        shutil.rmtree(tmp, ignore_errors=True)


def inspect_cad_frames(input_file: str) -> dict[str, Any]:
    """Р РЋР С—Р С‘РЎРѓР С•Р С” РЎР‚Р В°Р СР С•Р С” Р Т‘Р В»РЎРЏ DWG/DXF (Р Т‘Р В»РЎРЏ UI/API)."""
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Р С›Р В¶Р С‘Р Т‘Р В°Р ВµРЎвЂљРЎРѓРЎРЏ DWG Р С‘Р В»Р С‘ DXF, Р С—Р С•Р В»РЎС“РЎвЂЎР ВµР Р…Р С•: {suffix}")

    tmp = Path(tempfile.mkdtemp(prefix="cad_frames_"))
    try:
        if suffix == ".dwg":
            if not oda_available():
                raise RuntimeError("ODAFileConverter Р Р…Р Вµ РЎС“РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р В»Р ВµР Р…")
            dxf_path = convert_dwg_to_dxf(str(input_path))
            work_dxf = tmp / dxf_path.name
            shutil.copy2(dxf_path, work_dxf)
            shutil.rmtree(dxf_path.parent, ignore_errors=True)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)

        doc = _load_dxf_document(work_dxf)
        frames = detect_frames_in_doc(doc)
        chosen = choose_render_frames(doc, frames)
        return {
            "path": str(input_path),
            "detected": frames_summary(frames),
            "will_render": frames_summary(chosen),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_cad_to_pdf(input_file: str) -> tuple[Path, dict[str, Any]]:
    """
    DWG/DXF РІвЂ вЂ™ PDF.
    DWG: ODA РІвЂ вЂ™ PDF; Р С—РЎР‚Р С‘ Р С•РЎв‚¬Р С‘Р В±Р С”Р Вµ РІР‚вЂќ DWG РІвЂ вЂ™ DXF РІвЂ вЂ™ РЎР‚Р ВµР Р…Р Т‘Р ВµРЎР‚ Р С—Р С• РЎР‚Р В°Р СР С”Р В°Р С/layout (ezdxf).
    """
    input_path = Path(input_file)
    suffix = input_path.suffix.lower()
    if suffix not in CAD_EXTENSIONS:
        raise ValueError(f"Р С›Р В¶Р С‘Р Т‘Р В°Р ВµРЎвЂљРЎРѓРЎРЏ DWG Р С‘Р В»Р С‘ DXF, Р С—Р С•Р В»РЎС“РЎвЂЎР ВµР Р…Р С•: {suffix}")

    meta: dict[str, Any] = {"engine": None, "fallback": False}
    tmp = Path(tempfile.mkdtemp(prefix="cad_pdf_"))
    pdf_path = tmp / f"{input_path.stem}.pdf"
    size_mb = input_path.stat().st_size / (1024 * 1024) if input_path.exists() else 0

    if suffix == ".dwg":
        # Bypassed direct ODA PDF conversion to strictly use the frame-detection engine on the _Р РЃРЎвЂљР В°Р СР С—_РЎР‚Р В°Р СР С”Р В° layer.
        meta["fallback"] = True
        meta["engine"] = "ezdxf"

    try:
        if suffix == ".dwg":
            dxf_path = convert_dwg_to_dxf(str(input_path))
            work_dxf = tmp / dxf_path.name
            shutil.copy2(dxf_path, work_dxf)
            shutil.rmtree(dxf_path.parent, ignore_errors=True)
        else:
            work_dxf = tmp / input_path.name
            shutil.copy2(input_path, work_dxf)
            meta.setdefault("engine", "ezdxf")

        convert_dxf_to_pdf(work_dxf, pdf_path, meta=meta)
        return pdf_path, meta
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
