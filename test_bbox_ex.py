import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import _safe_virtual_block_reference_entities

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
print(f"Flattened: {len(flattened)}")

ex_count = 0
fallback_count = 0
for entity in flattened:
    try:
        box = bbox.extents([entity])
        if box.has_data:
            pass
        else:
            fallback_count += 1
    except Exception as e:
        ex_count += 1

print(f"Exceptions in bbox: {ex_count}")
print(f"Fallbacks in bbox: {fallback_count}")
