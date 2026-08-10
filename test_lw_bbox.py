import sys
sys.path.insert(0, '/app')
import ezdxf

doc = ezdxf.readfile('/tmp/cad_pdf_id7oy2hw/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
e = [x for x in doc.modelspace() if x.dxftype() == 'LWPOLYLINE'][0]

print('LWPOLYLINE:', e)
pts = list(e)
print('Points:', pts[:5])
