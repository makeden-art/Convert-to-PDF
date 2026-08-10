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
        out.append('        if p.layer and p.layer in ("ИС 7. Текст",) and p.color:\n')
        out.append('            p.color = p.color + "_KEEP"\n')
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