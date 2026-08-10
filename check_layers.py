import ezdxf
try:
    doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
    for layer in doc.layers:
        print(f"Layer: {layer.dxf.name}, ACI: {layer.dxf.color}, RGB: {getattr(layer, 'rgb', 'N/A')}, TrueColor: {layer.dxf.get('true_color', 'N/A')}")
except Exception as e:
    print('Err:', e)
