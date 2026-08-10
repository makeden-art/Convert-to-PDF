import re

with open('cad_converter.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = re.sub(r'if p\.layer and \"подпис\" in str\(p\.layer\)\.lower\(\) and p\.color:\s+p\.color = p\.color \+ \"_KEEP\"', 'if p.color == \"#0000a5\":\n            p.color = p.color + \"_KEEP\"', c)

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(c)