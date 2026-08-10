import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import detect_frames_in_doc, choose_render_frames

doc = ezdxf.readfile('/tmp/cad_pdf_id7oy2hw/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
frames = detect_frames_in_doc(doc)
render_frames = choose_render_frames(doc, frames)

print("Pre-calculating boxes...")
cache = {}
precalc = []
for e in doc.modelspace():
    try:
        box = bbox.extents([e], cache=cache)
        if box.has_data:
            precalc.append((e, box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y))
    except:
        pass
print(f"Precalculated {len(precalc)} entities.")

total = 0
for i, f in enumerate(render_frames[:10]):
    f_xmin, f_xmax, f_ymin, f_ymax = f.xmin, f.xmax, f.ymin, f.ymax
    entities = []
    for e, e_xmin, e_xmax, e_ymin, e_ymax in precalc:
        if e_xmax < f_xmin or e_xmin > f_xmax:
            continue
        if e_ymax < f_ymin or e_ymin > f_ymax:
            continue
        entities.append(e)
    print(f"Frame {i}: {len(entities)} entities selected")
    total += len(entities)

print(f"Total for 10 frames: {total}")
