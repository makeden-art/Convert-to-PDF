import ezdxf
doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
for layer in doc.layers:
    print(f"Layer: {layer.dxf.name}, hasattr: {layer.dxf.hasattr('true_color')}")
    break
