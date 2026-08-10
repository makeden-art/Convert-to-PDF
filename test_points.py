import sys
sys.path.insert(0, '/app')
import ezdxf
from ezdxf import bbox
from cad_converter import detect_frames_in_doc, choose_render_frames, _safe_virtual_block_reference_entities

doc = ezdxf.readfile('/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf')
for e in doc.modelspace():
    if e.dxftype() == 'INSERT':
        try:
            for virt_e in _safe_virtual_block_reference_entities(e, lambda *args: None):
                if virt_e.dxftype() == 'LWPOLYLINE':
                    print("Found virtual LWPOLYLINE!")
                    try:
                        pts = virt_e.get_points('xy')
                        print(f"Points from get_points('xy'): {pts[:3]}")
                    except Exception as e:
                        print("Exception get_points:", e)
                    try:
                        box = bbox.extents([virt_e])
                        print("bbox extents:", box.extmin, box.extmax)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        print("Exception bbox:", e)
                    sys.exit(0)
        except Exception:
            pass
