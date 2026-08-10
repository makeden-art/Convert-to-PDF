import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import detect_frames_in_doc, choose_render_frames, _safe_virtual_block_reference_entities
import time

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')

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
                pass
        else:
            flattened.append(e)

flatten(doc.modelspace())
print(f'Flattened to {len(flattened)} entities')

cache = bbox.Cache()
precalc = []
print('Calculating bboxes...')
inf_count = 0
for entity in flattened:
    has_box = False
    try:
        box = bbox.extents([entity], cache=cache)
        if box.has_data:
            precalc.append((entity, box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y))
            has_box = True
    except Exception:
        pass
        
    if not has_box:
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
                has_box = True
        except Exception:
            pass
            
    if not has_box:
        precalc.append((entity, -float('inf'), float('inf'), -float('inf'), float('inf')))
        inf_count += 1

print(f"Entities with infinite bbox: {inf_count}")
