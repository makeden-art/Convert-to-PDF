"""Поиск ГОСТ-рамок в DWG/DXF: viewport, полилинии, блоки, граница листа."""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

logger = logging.getLogger("convert.frames")

GOST_SIZES_MM: tuple[tuple[float, float, str], ...] = (
    (1189.0, 841.0, "A0"),
    (841.0, 594.0, "A1"),
    (594.0, 420.0, "A2"),
    (420.0, 297.0, "A3"),
    (297.0, 210.0, "A4"),
)

FRAME_LAYER_RE = re.compile(
    r"(рамк|format|gost|form|frame|border|лист|sheet|штамп|title|табл|table|форм)",
    re.IGNORECASE,
)
# Явные слои контура чертежа (приоритет над viewport и прочими «рамками»).
PRIORITY_FRAME_LAYER_RE = re.compile(
    r"(_штамп_рамк|штамп_рамк|stamp.?frame|pdf_frame|frame_export|рамка_pdf|convert_frame|batchplot|export)",
    re.IGNORECASE,
)
FRAME_BLOCK_RE = re.compile(
    r"(a[0-4]|format|gost|рамк|form|sheet|лист|штамп|title)",
    re.IGNORECASE,
)

SIZE_TOL_MM = 8.0
SIZE_TOL_RATIO = 0.025
MIN_FRAME_MM = 150.0
MAX_FRAME_MM = 3200.0


@dataclass(frozen=True)
class CadFrame:
    source: str
    layout: str
    label: str
    width_mm: float
    height_mm: float
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    layer: str | None = None
    handle: str | None = None
    orientation: str = "landscape"

    @property
    def area(self) -> float:
        return max(0.0, self.xmax - self.xmin) * max(0.0, self.ymax - self.ymin)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_priority_frame_layer(layer: str | None) -> bool:
    return bool(layer and PRIORITY_FRAME_LAYER_RE.search(layer))


def _orientation_label(w: float, h: float) -> str:
    return "landscape" if w >= h else "portrait"


def _match_gost(w: float, h: float) -> tuple[str, float, float] | None:
    w = abs(w)
    h = abs(h)
    if w < 50 or h < 50:
        return None
    for gw, gh, name in GOST_SIZES_MM:
        for aw, ah in ((w, h), (h, w)):
            tw = max(SIZE_TOL_MM, gw * SIZE_TOL_RATIO)
            th = max(SIZE_TOL_MM, gh * SIZE_TOL_RATIO)
            if abs(aw - gw) <= tw and abs(ah - gh) <= th:
                return name, gw, gh
    return None


def _is_drawing_frame_size(w: float, h: float) -> bool:
    w = abs(w)
    h = abs(h)
    if w < MIN_FRAME_MM or h < MIN_FRAME_MM:
        return False
    if w > MAX_FRAME_MM or h > MAX_FRAME_MM:
        return False
    if _match_gost(w, h):
        return True
    long_side = max(w, h)
    short_side = min(w, h)
    if 350 <= long_side <= 1400 and 200 <= short_side <= 900:
        ratio = long_side / short_side if short_side else 99
        return 1.15 <= ratio <= 2.2
    return False


def _dedupe(frames: list[CadFrame]) -> list[CadFrame]:
    out: list[CadFrame] = []
    for frame in sorted(frames, key=lambda f: (-f.area, f.layout, f.xmin, f.ymin)):
        duplicate = False
        for kept in out:
            if frame.layout != kept.layout:
                continue
            overlap_x = max(0.0, min(frame.xmax, kept.xmax) - max(frame.xmin, kept.xmin))
            overlap_y = max(0.0, min(frame.ymax, kept.ymax) - max(frame.ymin, kept.ymin))
            overlap = overlap_x * overlap_y
            min_area = min(frame.area, kept.area) or 1.0
            if overlap / min_area > 0.85:
                duplicate = True
                break
        if not duplicate:
            out.append(frame)
    return sorted(out, key=lambda f: (f.layout, -f.area, f.ymin, f.xmin))


def _points_xy(entity) -> list[tuple[float, float]]:
    if entity.dxftype() == "LWPOLYLINE":
        return [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
    if entity.dxftype() == "POLYLINE":
        return [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
    return []


def _is_closed_poly_rect(entity) -> bool:
    if entity.dxftype() not in ("LWPOLYLINE", "POLYLINE"):
        return False
    if entity.dxftype() == "LWPOLYLINE":
        if not entity.closed:
            return False
        pts = _points_xy(entity)
        return len(pts) in (4, 5)
    if entity.is_closed is False:
        return False
    pts = _points_xy(entity)
    return len(pts) in (4, 5)


def _bbox_from_points(pts: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _frame_from_rect(
    *,
    source: str,
    layout: str,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    layer: str | None = None,
    handle: str | None = None,
    force: bool = False,
) -> CadFrame | None:
    w = xmax - xmin
    h = ymax - ymin
    matched = _match_gost(w, h)
    layer_bonus = bool(layer and FRAME_LAYER_RE.search(layer))
    priority_layer = _is_priority_frame_layer(layer)
    if not matched and not force and not layer_bonus and not priority_layer and not _is_drawing_frame_size(w, h):
        return None
    if matched:
        name, _, _ = matched
        label = f"{name} ({_orientation_label(w, h)})"
    else:
        label = f"{round(w)}×{round(h)} мм"
    return CadFrame(
        source=source,
        layout=layout,
        label=label,
        width_mm=round(max(w, h), 1),
        height_mm=round(min(w, h), 1),
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        layer=layer,
        handle=handle,
        orientation=_orientation_label(w, h),
    )


def _collect_closed_rects(layout) -> list[tuple[float, float, float, float, str | None, str | None]]:
    rects: list[tuple[float, float, float, float, str | None, str | None]] = []
    for entity in layout:
        if not _is_closed_poly_rect(entity):
            continue
        pts = _points_xy(entity)
        if len(pts) == 5 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) != 4:
            continue
        xmin, ymin, xmax, ymax = _bbox_from_points(pts)
        w = xmax - xmin
        h = ymax - ymin
        if w < 30 or h < 30:
            continue
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else None
        handle = getattr(entity.dxf, "handle", None)
        rects.append((xmin, ymin, xmax, ymax, layer, handle))
    return rects


def _detect_sheet_border_frames(layout, layout_name: str) -> list[CadFrame]:
    """Внешняя рамка листа: самый большой прямоугольник, без пристроек справа (таблицы)."""
    if layout_name.upper() == "MODEL":
        return []

    rects = _collect_closed_rects(layout)
    if not rects:
        return []

    candidates: list[CadFrame] = []
    for xmin, ymin, xmax, ymax, layer, handle in rects:
        w = xmax - xmin
        h = ymax - ymin
        if not _is_drawing_frame_size(w, h):
            continue
        frame = _frame_from_rect(
            source="sheet_border",
            layout=layout_name,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            layer=layer,
            handle=handle,
            force=True,
        )
        if frame:
            candidates.append(frame)

    if not candidates:
        return []

    outer = max(candidates, key=lambda f: f.area)
    outer_w = outer.xmax - outer.xmin
    right_blocks = [
        r
        for r in rects
        if (r[2] - r[0]) <= outer_w * 0.45
        and r[0] >= outer.xmin + outer_w * 0.35
        and (r[2] - r[0]) * (r[3] - r[1]) < outer.area * 0.55
    ]
    if right_blocks:
        cut_x = min(r[0] for r in right_blocks)
        if outer.xmin + outer_w * 0.4 < cut_x < outer.xmax:
            clipped = _frame_from_rect(
                source="sheet_border",
                layout=layout_name,
                xmin=outer.xmin,
                ymin=outer.ymin,
                xmax=cut_x,
                ymax=outer.ymax,
                layer=outer.layer,
                handle=outer.handle,
                force=True,
            )
            if clipped:
                logger.info(
                    "Лист %s: рамка %s, обрезка справа до x=%.1f",
                    layout_name,
                    clipped.label,
                    cut_x,
                )
                return [clipped]

    return [outer]


def _detect_polyline_frames(layout, layout_name: str) -> list[CadFrame]:
    frames: list[CadFrame] = []
    for xmin, ymin, xmax, ymax, layer, handle in _collect_closed_rects(layout):
        frame = _frame_from_rect(
            source="polyline",
            layout=layout_name,
            xmin=xmin,
            ymin=ymin,
            xmax=xmax,
            ymax=ymax,
            layer=layer,
            handle=handle,
            force=_is_priority_frame_layer(layer),
        )
        if frame:
            frames.append(frame)
    return frames


def _detect_viewport_frames(layout, layout_name: str) -> list[CadFrame]:
    frames: list[CadFrame] = []
    for vp in layout.query("VIEWPORT"):
        if vp.dxf.id == 1:
            continue
        width = float(vp.dxf.width)
        height = float(vp.dxf.height)
        if width < 10 or height < 10:
            continue
        cx = float(vp.dxf.center.x)
        cy = float(vp.dxf.center.y)
        xmin = cx - width / 2
        xmax = cx + width / 2
        ymin = cy - height / 2
        ymax = cy + height / 2

        matched = _match_gost(width, height)
        label = (
            f"{matched[0]} viewport ({_orientation_label(width, height)})"
            if matched
            else f"Viewport {round(width)}×{round(height)} мм"
        )

        frames.append(
            CadFrame(
                source="viewport",
                layout=layout_name,
                label=label,
                width_mm=round(max(width, height), 1),
                height_mm=round(min(width, height), 1),
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                layer=None,
                handle=getattr(vp.dxf, "handle", None),
                orientation=_orientation_label(width, height),
            )
        )
    return frames


def _detect_block_frames(layout, layout_name: str, doc) -> list[CadFrame]:
    frames: list[CadFrame] = []
    try:
        from ezdxf import bbox
    except ImportError:
        return frames

    for ins in layout.query("INSERT"):
        name = ins.dxf.name or ""
        layer = ins.dxf.layer or ""
        if not FRAME_BLOCK_RE.search(name) and not _is_priority_frame_layer(layer):
            continue
        try:
            ext = bbox.extents([ins], fast=True)
        except Exception:
            continue
        if not ext.has_data:
            continue
        source = "stamp_frame" if _is_priority_frame_layer(layer) else "block"
        frame = _frame_from_rect(
            source=source,
            layout=layout_name,
            xmin=ext.extmin.x,
            ymin=ext.extmin.y,
            xmax=ext.extmax.x,
            ymax=ext.extmax.y,
            layer=layer,
            handle=getattr(ins.dxf, "handle", None),
            force=True,
        )
        if frame:
            frames.append(frame)
    return frames


def detect_frames_in_doc(doc) -> list[CadFrame]:
    frames: list[CadFrame] = []

    # 1. Поиск замкнутых прямоугольников на целевом слое _Штамп_рамка
    target_layers = {"_штамп_рамка", "штамп_рамка", "_штамп_рамк", "штамп_рамк"}
    for layout_name in doc.layouts.names():
        layout = doc.layouts.get(layout_name)
        for xmin, ymin, xmax, ymax, layer, handle in _collect_closed_rects(layout):
            if layer and layer.strip().lower() in target_layers:
                w = xmax - xmin
                h = ymax - ymin
                if w < 30 or h < 30:
                    continue
                label = f"{round(w)}×{round(h)} мм"
                frames.append(CadFrame(
                    source="stamp_frame",
                    layout=layout_name,
                    label=label,
                    width_mm=round(max(w, h), 1),
                    height_mm=round(min(w, h), 1),
                    xmin=xmin,
                    ymin=ymin,
                    xmax=xmax,
                    ymax=ymax,
                    layer=layer,
                    handle=handle,
                    orientation=_orientation_label(w, h),
                ))

    # Если рамки на слое _Штамп_рамка найдены, возвращаем только их
    if frames:
        deduped = _dedupe(frames)
        logger.info("Найдено приоритетных рамок на слое _Штамп_рамка: %d", len(deduped))
        return deduped

    # Иначе выполняем стандартный поиск рамок
    for layout_name in doc.layouts.names():
        layout = doc.layouts.get(layout_name)
        frames.extend(_detect_sheet_border_frames(layout, layout_name))
        frames.extend(_detect_viewport_frames(layout, layout_name))
        frames.extend(_detect_polyline_frames(layout, layout_name))
        frames.extend(_detect_block_frames(layout, layout_name, doc))

    deduped = _dedupe(frames)
    logger.info("Найдено рамок: %d", len(deduped))
    return deduped


def _clip_poly_frame(doc, outer: CadFrame) -> CadFrame | None:
    """Обрезать справа таблицы внутри листа (читаем все poly до dedupe)."""
    try:
        layout = doc.layouts.get(outer.layout)
    except Exception:
        return None
    outer_w = outer.xmax - outer.xmin
    attachments = []
    for xmin, ymin, xmax, ymax, _layer, _handle in _collect_closed_rects(layout):
        w = xmax - xmin
        h = ymax - ymin
        area = w * h
        if w <= outer_w * 0.45 and xmin >= outer.xmin + outer_w * 0.35 and area < outer.area * 0.55:
            if abs(xmin - outer.xmin) < 2 and abs(xmax - outer.xmax) < 2:
                continue
            attachments.append((xmin, ymin, xmax, ymax))
    if not attachments:
        return None
    cut_x = min(a[0] for a in attachments)
    if not (outer.xmin + outer_w * 0.4 < cut_x < outer.xmax):
        return None
    w = cut_x - outer.xmin
    h = outer.ymax - outer.ymin
    return CadFrame(
        source="sheet_border",
        layout=outer.layout,
        label=f"{round(w)}×{round(h)} мм (без таблиц справа)",
        width_mm=round(max(w, h), 1),
        height_mm=round(min(w, h), 1),
        xmin=outer.xmin,
        ymin=outer.ymin,
        xmax=cut_x,
        ymax=outer.ymax,
        layer=outer.layer,
        handle=outer.handle,
        orientation=_orientation_label(w, h),
    )


def choose_render_frames(doc, frames: list[CadFrame]) -> list[CadFrame]:
    """По одной рамке на каждый layout (лист)."""
    if not frames:
        return []

def sort_frames_reading_order(frames: list[CadFrame]) -> list[CadFrame]:
    """Сортирует рамки по порядку чтения: сначала по листам, затем по слоям (_рамка_2, _рамка_3) для очередности,
    а при равенстве слоев — построчно сверху вниз, слева направо."""
    if not frames:
        return []

    # Группируем рамки по layout (листам)
    by_layout: dict[str, list[CadFrame]] = {}
    for f in frames:
        by_layout.setdefault(f.layout, []).append(f)

    # Вспомогательная функция для парсинга номера в конце имени слоя
    def parse_layer_seq(layer_name: str) -> int:
        if not layer_name:
            return 1
        m = re.search(r'[-_]?(\d+)$', layer_name)
        if m:
            return int(m.group(1))
        return 1

    sorted_all: list[CadFrame] = []
    # Сортируем листы по алфавиту/натуральному порядку
    for layout_name in sorted(by_layout.keys()):
        layout_frames = by_layout[layout_name]
        if not layout_frames:
            continue

        # Группируем рамки этого листа по номеру слоя
        by_seq: dict[int, list[CadFrame]] = {}
        for f in layout_frames:
            seq = parse_layer_seq(f.layer)
            by_seq.setdefault(seq, []).append(f)

        # Сортируем группы слоев по возрастанию номера (1, 2, 3...)
        for seq_num in sorted(by_seq.keys()):
            seq_frames = by_seq[seq_num]

            # Для рамок на одном слое применяем пространственную сортировку сверху вниз, слева направо
            # Сортируем рамки сверху вниз по Y-центру
            seq_frames.sort(key=lambda f: (f.ymin + f.ymax) / 2, reverse=True)

            # Группируем в строки с допуском 50% от высоты рамки
            rows: list[dict] = []
            for f in seq_frames:
                y_center = (f.ymin + f.ymax) / 2
                h = abs(f.ymax - f.ymin)

                placed = False
                for row in rows:
                    row_y_center = row["y_center_sum"] / len(row["frames"])
                    row_h = row["height_sum"] / len(row["frames"])
                    if abs(y_center - row_y_center) < (row_h * 0.5):
                        row["frames"].append(f)
                        row["y_center_sum"] += y_center
                        row["height_sum"] += h
                        placed = True
                        break
                if not placed:
                    rows.append({
                        "frames": [f],
                        "y_center_sum": y_center,
                        "height_sum": h
                    })

            # Сортируем сами строки сверху вниз
            rows.sort(key=lambda r: r["y_center_sum"] / len(r["frames"]), reverse=True)

            # Внутри каждой строки сортируем слева направо по X-центру
            for row in rows:
                row["frames"].sort(key=lambda f: (f.xmin + f.xmax) / 2)
                sorted_all.extend(row["frames"])

    return sorted_all


def choose_render_frames(doc, frames: list[CadFrame]) -> list[CadFrame]:
    """По одной рамке на каждый layout (лист) или все рамки из Model, если их несколько."""
    if not frames:
        return []

    # 1. Отбираем рамки в Model
    model_frames = [
        f for f in frames
        if f.layout.upper() == "MODEL"
        and (f.source == "stamp_frame" or _is_drawing_frame_size(f.xmax - f.xmin, f.ymax - f.ymin))
    ]

    # 2. Списки layouts
    layout_names = [n for n in doc.layouts.names() if n.upper() != "MODEL"]

    # Если в модели обнаружено более 1 рамки чертежа (мульти-листовой чертеж в пространстве модели),
    # приоритет отдаем модели!
    prefer_model = len(model_frames) > 1

    if prefer_model:
        model_stamps = [f for f in model_frames if f.source == "stamp_frame"]
        if model_stamps:
            return sort_frames_reading_order(model_stamps)
        return sort_frames_reading_order(model_frames)

    # 3. Иначе рендерим по layouts (по одной рамке на каждый layout)
    chosen: list[CadFrame] = []
    if layout_names:
        for layout_name in layout_names:
            local = [f for f in frames if f.layout == layout_name]
            if not local:
                continue

            priority = [
                f
                for f in local
                if _is_priority_frame_layer(f.layer)
                and f.source in ("stamp_frame", "block", "polyline", "sheet_border")
            ]
            if priority:
                best = max(priority, key=lambda f: f.area)
                clipped = _clip_poly_frame(doc, best) if best.source != "stamp_frame" else None
                chosen.append(clipped or best)
                continue

            sheet = [f for f in local if f.source == "sheet_border"]
            if sheet:
                chosen.append(max(sheet, key=lambda f: f.area))
                continue

            polys = [
                f
                for f in local
                if f.source == "polyline"
                and _is_drawing_frame_size(f.xmax - f.xmin, f.ymax - f.ymin)
            ]
            if polys:
                best = max(polys, key=lambda f: f.area)
                clipped = _clip_poly_frame(doc, best)
                chosen.append(clipped or best)
                continue

            vps = [f for f in local if f.source == "viewport"]
            if len(vps) == 1:
                chosen.append(vps[0])
                continue
            if len(vps) > 1:
                xmin = min(v.xmin for v in vps)
                ymin = min(v.ymin for v in vps)
                xmax = max(v.xmax for v in vps)
                ymax = max(v.ymax for v in vps)
                w = xmax - xmin
                h = ymax - ymin
                chosen.append(
                    CadFrame(
                        source="viewport_union",
                        layout=layout_name,
                        label=f"Область viewport ({round(w)}×{round(h)} мм)",
                        width_mm=round(max(w, h), 1),
                        height_mm=round(min(w, h), 1),
                        xmin=xmin,
                        ymin=ymin,
                        xmax=xmax,
                        ymax=ymax,
                        orientation=_orientation_label(w, h),
                    )
                )

    if chosen:
        return _dedupe(chosen)

    # 4. Если в леяутах ничего не найдено, возвращаем рамки из модели (даже если она одна)
    if model_frames:
        model_stamps = [f for f in model_frames if f.source == "stamp_frame"]
        if model_stamps:
            return sort_frames_reading_order(model_stamps)
        return sort_frames_reading_order(model_frames)

    return []


def frames_summary(frames: list[CadFrame]) -> dict[str, Any]:
    return {
        "count": len(frames),
        "frames": [f.to_dict() for f in frames[:100]],
        "truncated": len(frames) > 100,
    }
