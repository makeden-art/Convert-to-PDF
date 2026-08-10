import ezdxf
try:
    doc = ezdxf.readfile('/tmp/test_cad/in.dxf')
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
            has_no_true_color = 0
            for e in layout:
                count += 1
                try:
                    tc = False
                    if e.dxf.hasattr('true_color'):
                        tc = True
                    elif e.dxf.color == 256:
                        layer = doc.layers.get(e.dxf.layer)
                        if layer.dxf.hasattr('true_color'):
                            tc = True
                    if tc:
                        has_true_color += 1
                    else:
                        has_no_true_color += 1
                except:
                    pass
            print(f"  Layout {layout.name}: {count} entities, {has_true_color} have true color, {has_no_true_color} have NO true color.")

except Exception as e:
    print(f"Error: {e}")
