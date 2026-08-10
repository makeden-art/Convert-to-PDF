import ezdxf
doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
count = 0
for layer in doc.layers:
    if layer.dxf.hasattr('true_color'): count += 1
print(f'Layers with true_color: {count}/{len(doc.layers)}')
