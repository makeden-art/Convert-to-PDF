import ezdxf
doc = ezdxf.readfile('/opt/road-pdf-platform/shared/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dxf')
aci_colors = set()
true_colors = set()
for entity in doc.modelspace():
    if entity.dxf.hasattr('true_color'):
        true_colors.add(entity.dxf.true_color)
    else:
        aci_colors.add(entity.dxf.color)
print(f'Modelspace ACI colors: {aci_colors}')
print(f'Modelspace True colors: {true_colors}')
