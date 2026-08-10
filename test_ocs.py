import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import _safe_virtual_block_reference_entities

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
cache = bbox.Cache()

huge = 0
for e in doc.modelspace():
    if e.dxftype() == 'INSERT':
        try:
            for virt_e in _safe_virtual_block_reference_entities(e, lambda *args: None):
                pass
        except Exception as ex:
            try:
                box = bbox.extents([e], cache=cache)
                width = box.extmax.x - box.extmin.x
                if width > 5000:
                    huge += 1
            except Exception:
                pass
print(f"Huge INSERTs with invalid OCS: {huge}")
