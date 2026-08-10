import ezdxf
try:
    doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
    for layout in doc.layouts:
        print('Layout:', layout.name)
        if hasattr(layout, 'dxf'):
            print('DXF attribs:', layout.dxf.all_existing_dxf_attribs())
except Exception as e:
    print('Err:', e)
