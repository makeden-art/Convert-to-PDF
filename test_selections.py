import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import detect_frames_in_doc, choose_render_frames

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
frames = detect_frames_in_doc(doc)
render_frames = choose_render_frames(doc, frames)

cache = bbox.Cache()
precalc = []
for entity in doc.modelspace():
    box = bbox.extents([entity], cache=cache)
    if box.has_data:
        precalc.append((entity, box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y))
        continue
    try:
        xs, ys = [], []
        if entity.dxftype() == 'LWPOLYLINE':
            xs = [p[0] for p in entity]
            ys = [p[1] for p in entity]
        if xs and ys:
            precalc.append((entity, min(xs), max(xs), min(ys), max(ys)))
            continue
    except Exception:
        pass
    precalc.append((entity, -float('inf'), float('inf'), -float('inf'), float('inf')))

total = 0
for i, f in enumerate(render_frames[:10]):
    f_xmin, f_xmax, f_ymin, f_ymax = f.xmin, f.xmax, f.ymin, f.ymax
    entities = []
    for entity, e_xmin, e_xmax, e_ymin, e_ymax in precalc:
        if e_xmax < f_xmin or e_xmin > f_xmax:
            continue
        if e_ymax < f_ymin or e_ymin > f_ymax:
            continue
        entities.append(entity)
    print(f"Frame {i}: {len(entities)} entities selected")
    total += len(entities)

print(f"Total for 10 frames: {total}")
