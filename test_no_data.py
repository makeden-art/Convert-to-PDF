import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox

doc = ezdxf.readfile('/tmp/cad_pdf_id7oy2hw/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
cache = {}

no_data_types = {}
for e in doc.modelspace():
    box = bbox.extents([e], cache=cache)
    if not box.has_data:
        no_data_types[e.dxftype()] = no_data_types.get(e.dxftype(), 0) + 1

print("Entities with NO DATA:")
for t, c in no_data_types.items():
    print(f"{t}: {c}")
