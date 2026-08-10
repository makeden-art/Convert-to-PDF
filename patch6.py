import sys
with open('cad_converter.py', 'r', encoding='utf-8') as f:
    c = f.read()

import re
c = re.sub(r'if p\.layer and p\.layer in \(".*?",\) and p\.color:', 'if p.layer and p.layer in ("ИС 7. Текст",) and p.color:', c)

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(c)