import ezdxf
doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
for layer in doc.layers:
    if layer.dxf.hasattr('true_color'):
        print(layer.dxf.name, layer.dxf.true_color)
