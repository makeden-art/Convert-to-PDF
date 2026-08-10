import sys

with open('cad_converter.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

out = []
in_func = False
for line in lines:
    if 'def new_resolve_all(self, entity):' in line:
        in_func = True
        out.append(line)
        out.append('        p = properties._orig_resolve_all(self, entity)\n')
        out.append('        \n')
        out.append('        # ACI based color preservation\n')
        out.append('        keep_color = False\n')
        out.append('        c = entity.dxf.color\n')
        out.append('        if entity.dxf.hasattr("true_color"):\n')
        out.append('            tc = entity.dxf.true_color\n')
        out.append('            r, g, b = (tc >> 16) & 0xFF, (tc >> 8) & 0xFF, tc & 0xFF\n')
        out.append('            if r < 100 and g < 150 and b > 150:\n')
        out.append('                keep_color = True\n')
        out.append('        elif c not in (0, 256) and c in {5}:\n')
        out.append('            keep_color = True\n')
        out.append('        elif c == 256 and entity.doc:\n')
        out.append('            layer = entity.doc.layers.get(entity.dxf.layer)\n')
        out.append('            if layer:\n')
        out.append('                if layer.dxf.hasattr("true_color"):\n')
        out.append('                    tc = layer.dxf.true_color\n')
        out.append('                    r, g, b = (tc >> 16) & 0xFF, (tc >> 8) & 0xFF, tc & 0xFF\n')
        out.append('                    if r < 100 and g < 150 and b > 150:\n')
        out.append('                        keep_color = True\n')
        out.append('                elif layer.color in {5}:\n')
        out.append('                    keep_color = True\n')
        out.append('                    \n')
        out.append('        if keep_color and p.color:\n')
        out.append('            p.color = p.color + "_KEEP"\n')
        out.append('            \n')
        out.append('        return p\n')
        continue
    if in_func and 'properties.RenderContext.resolve_all = new_resolve_all' in line:
        in_func = False
        out.append(line)
        continue
    if not in_func:
        out.append(line)

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(''.join(out))