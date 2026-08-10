import sys

converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\converter.py"
cad_converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_converter.py"

c = open(converter_path, 'r', encoding='utf-8').read()
c = c.replace('color_mode: str = "color"', 'windows_cad_ip: str = ""')
c = c.replace('meta={"color_mode": color_mode}', 'meta={"windows_cad_ip": windows_cad_ip}')
open(converter_path, 'w', encoding='utf-8').write(c)

cad = open(cad_converter_path, 'r', encoding='utf-8').read()
# Let's see what is inside cad_converter.py for windows_cad_ip
# I will first read it.
