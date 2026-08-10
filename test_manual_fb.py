import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from ezdxf.path import make_path
from cad_converter import _safe_virtual_block_reference_entities
import collections

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
flattened = []
def flatten(entities):
    for e in entities:
        if e.dxftype() == 'INSERT':
            try:
                for virt_e in _safe_virtual_block_reference_entities(e, skipped_entity_callback=lambda *args: None):
                    if virt_e.dxftype() == 'INSERT':
                        flatten([virt_e])
                    else:
                        flattened.append(virt_e)
            except Exception:
                pass
        else:
            flattened.append(e)
flatten(doc.modelspace())

types = collections.Counter()
absolute_fallback_count = 0

for entity in flattened:
    box = bbox.extents([entity])
    if box.has_data:
        continue
    try:
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
            p = make_path(entity)
            b = p.bbox()
            if b.has_data:
                xs = [b.extmin.x, b.extmax.x]
                ys = [b.extmin.y, b.extmax.y]
            
        if xs and ys:
            continue
    except Exception:
        pass
    
    types[entity.dxftype()] += 1
    absolute_fallback_count += 1

print(f"Absolute fallbacks: {absolute_fallback_count}")
print(types.most_common())
