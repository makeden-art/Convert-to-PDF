import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox

print('Loading...')
doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
cache = bbox.Cache()

print('Searching for massive INSERTs...')
huge_inserts = 0
for e in doc.modelspace():
    if e.dxftype() == 'INSERT':
        try:
            box = bbox.extents([e], cache=cache)
            width = box.extmax.x - box.extmin.x
            height = box.extmax.y - box.extmin.y
            if width > 10000 or height > 10000:
                print(f"Huge INSERT: {width}x{height} at {box.extmin.x},{box.extmin.y}")
                huge_inserts += 1
        except Exception:
            pass
            
print(f"Total huge inserts: {huge_inserts}")
