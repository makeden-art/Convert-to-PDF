import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox

print('Loading...')
doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
cache = bbox.Cache()

print('Evaluating LWPOLYLINEs fallback...')
success_fallback = 0
failed_fallback = 0

for i, e in enumerate(doc.modelspace()):
    if e.dxftype() == 'LWPOLYLINE':
        box = bbox.extents([e], cache=cache)
        if not box.has_data:
            try:
                xs = [p[0] for p in e]
                ys = [p[1] for p in e]
                if xs and ys:
                    success_fallback += 1
                else:
                    failed_fallback += 1
            except Exception:
                failed_fallback += 1
print(f"Fallback success: {success_fallback}, failed: {failed_fallback}")
