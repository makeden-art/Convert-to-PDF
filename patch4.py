import re

with open('cad_converter.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = re.sub(r'if p\.color == \"#0000a5\":\s+p\.color = p\.color \+ \"_KEEP\"', 'if p.layer and p.layer in (\"ИС_ТаБл\", \"ИС 7. Текст\") and p.color:\n            p.color = p.color + \"_KEEP\"', c)

with open('cad_converter.py', 'w', encoding='utf-8') as f:
    f.write(c)