import ezdxf
try:
    doc = ezdxf.readfile('/data/documents/11-2025-1-ТКР9-1_Кальмиус_варианты.dwg')
    for layout in doc.layouts:
        print(f"Layout: {layout.name}")
        try:
            print(f"  Style: {layout.dxf.current_style_sheet}")
        except Exception as e:
            print(f"  Style Exception: {e}")
            
    print("Checking entities in first layout for true_color...")
    for layout in doc.layouts:
        if layout.name.lower() != "model":
            count = 0
            has_true_color = 0
            for e in layout:
                count += 1
                try:
                    if e.dxf.hasattr('true_color'):
                        has_true_color += 1
                    elif e.dxf.color == 256:
                        layer = doc.layers.get(e.dxf.layer)
                        if layer.dxf.hasattr('true_color'):
                            has_true_color += 1
                except:
                    pass
            print(f"  Layout {layout.name}: {count} entities, {has_true_color} have true color.")

except Exception as e:
    print(f"Error: {e}")
