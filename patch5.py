import re

with open('cad_converter.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = c.replace('if p.layer and p.layer in ("ИС_ТаБл", "ИС 7. Текст") and p.color:', 'if p.layer and p.layer in ("ИС 7. Текст",) and p.color:')

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(c)