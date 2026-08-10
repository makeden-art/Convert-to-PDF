import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import _safe_virtual_block_reference_entities

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
cache = bbox.Cache()
for e in doc.modelspace():
    if e.dxftype() == 'INSERT':
        try:
            for virt_e in _safe_virtual_block_reference_entities(e, lambda *args: None):
                if virt_e.dxftype() == 'LWPOLYLINE':
                    print("Found virtual LWPOLYLINE!")
                    try:
                        box = bbox.extents([virt_e], cache=cache)
                        print("Box has data:", box.has_data)
                    except Exception as ex:
                        import traceback
                        traceback.print_exc()
                        print("Exception bounding box:", type(ex), ex)
                    sys.exit(0)
        except Exception:
            pass
