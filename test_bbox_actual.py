import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox

try:
    print('Loading DXF...')
    doc = ezdxf.readfile('/tmp/cad_pdf_id7oy2hw/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
    print('Loaded. Calculating bbox for all entities...')
    cache = {}
    success = 0
    failed = 0
    
    for entity in doc.modelspace():
        try:
            box = bbox.extents([entity], cache=cache)
            success += 1
        except Exception as e:
            failed += 1
            if failed == 1:
                print(f"FIRST ERROR on {entity.dxftype()}: {e}")
                import traceback
                traceback.print_exc()
                
    print(f'Finished. Success: {success}, Failed: {failed}')
except Exception as e:
    print('Error:', e)
