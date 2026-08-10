import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import detect_frames_in_doc, choose_render_frames, _safe_virtual_block_reference_entities
import time

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
frames = detect_frames_in_doc(doc)
render_frames = choose_render_frames(doc, frames)

print('Flattening...')
flattened = []
def flatten(entities):
    for e in entities:
        if e.dxftype() == 'INSERT':
            try:
                for virt_e in _safe_virtual_block_reference_entities(e, lambda *args: None):
                    if virt_e.dxftype() == 'INSERT':
                        flatten([virt_e])
                    else:
                        flattened.append(virt_e)
            except Exception:
                flattened.append(e)
        else:
            flattened.append(e)

flatten(doc.modelspace())
print(f'Flattened to {len(flattened)} entities')

cache = bbox.Cache()
precalc = []
print('Calculating bboxes...')
for entity in flattened:
    try:
        box = bbox.extents([entity], cache=cache)
        if box.has_data:
            precalc.append((entity, box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y))
            continue
    except Exception:
        pass
    try:
        xs, ys = [], []
        if entity.dxftype() == 'LWPOLYLINE':
            xs = [p[0] for p in entity]
            ys = [p[1] for p in entity]
        elif entity.dxftype() == 'LINE':
            xs = [entity.dxf.start.x, entity.dxf.end.x]
            ys = [entity.dxf.start.y, entity.dxf.end.y]
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
