import sys
import glob
sys.path.insert(0, '/app')
import ezdxf
from cad_converter import _safe_virtual_block_reference_entities
from ezdxf.path import make_path
from ezdxf import bbox

dxf_file = glob.glob('/tmp/smb_get_465jwcoi/*.dxf')
if not dxf_file:
    dxf_file = glob.glob('/tmp/smb_get_465jwcoi/*.dwg')
    print("NO DXF FOUND, checking dwg:", dxf_file)
    sys.exit(1)

doc = ezdxf.readfile(dxf_file[0])
flattened = []
def flatten(entities):
    for e in entities:
        if e.dxftype() == 'INSERT':
            try:
                for virt_e in _safe_virtual_block_reference_entities(e, skipped_entity_callback=lambda *args: None):
                    if virt_e.dxftype() in ('INSERT', 'VirtualInsert'):
                        flatten([virt_e])
                    else:
                        flattened.append(virt_e)
            except Exception:
                pass
        else:
            flattened.append(e)

flatten(doc.modelspace())
print(f"Flattened entities: {len(flattened)}")

precalculated_entities = []
for entity in flattened:
    try:
        box = bbox.extents([entity])
        if box.has_data:
            precalculated_entities.append((entity, box.extmin.x, box.extmax.x, box.extmin.y, box.extmax.y))
            continue
        xs, ys = [], []
        if hasattr(entity, 'get_points'):
            pts = list(entity.get_points('xy'))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
        elif entity.dxftype() in ('LWPOLYLINE', 'VirtualLWPolyline'):
            xs = [p[0] for p in entity]
            ys = [p[1] for p in entity]
        elif entity.dxftype() in ('LINE', 'VirtualLine'):
            xs = [entity.dxf.start.x, entity.dxf.end.x]
            ys = [entity.dxf.start.y, entity.dxf.end.y]
        elif entity.dxftype() in ('TEXT', 'MTEXT', 'VirtualText', 'VirtualMText'):
            ip = entity.dxf.insert
            xs, ys = [ip.x], [ip.y]
        elif entity.dxftype() in ('SOLID', 'TRACE', '3DFACE', 'VirtualSolid'):
            for attr in ('vtx0', 'vtx1', 'vtx2', 'vtx3'):
                if entity.dxf.hasattr(attr):
                    v = entity.dxf.get(attr)
                    xs.append(v.x)
                    ys.append(v.y)
        elif entity.dxftype() in ('HATCH', 'VirtualHatch', 'MPOLYGON', 'VirtualMPolygon', 'SPLINE', 'VirtualSpline', 'ARC', 'VirtualArc', 'CIRCLE', 'VirtualCircle', 'ELLIPSE', 'VirtualEllipse', 'POLYLINE', 'VirtualPolyline'):
            try:
                p = make_path(entity)
                b = p.bbox()
                if b.has_data:
                    xs = [b.extmin.x, b.extmax.x]
                    ys = [b.extmin.y, b.extmax.y]
            except Exception:
                pass
        if xs and ys:
            precalculated_entities.append((entity, min(xs), max(xs), min(ys), max(ys)))
            continue
    except Exception:
        pass
    precalculated_entities.append((entity, -float('inf'), float('inf'), -float('inf'), float('inf')))

f_xmin = 6869720.465574428
f_xmax = 6870349.36856477
f_ymin = -613088.6753319133
f_ymax = -612364.065585934

in_frame = 0
for entity, e_xmin, e_xmax, e_ymin, e_ymax in precalculated_entities:
    if e_xmax < f_xmin or e_xmin > f_xmax:
        continue
    if e_ymax < f_ymin or e_ymin > f_ymax:
        continue
    in_frame += 1

print(f"Entities in frame: {in_frame}")
