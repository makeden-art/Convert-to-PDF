import subprocess
import os

dwg = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\Несколько_рамок_в_принудительном_порядке_в_листе.dwg"
scr = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\test.scr"

code = '(setvar "CTAB" (car (layoutlist))) (command "_.-PLOT" "_Yes" "" "DWG To PDF.pc3" "" "_Millimeters" "_Landscape" "_No" "_Layout" "1:1" "_Yes" "monochrome.ctb" "_Yes" "_No" "_No" "_No" "C:/Users/makeden/Documents/test.pdf" "_No" "_Yes") (command "_.QUIT" "_Y")\n'
with open(scr, "w", encoding="cp1251") as f:
    f.write(code)

cmd = f'"E:\\Autodesk\\acad\\AutoCAD 2022\\accoreconsole.exe" /i "{dwg}" /l ru-RU /s "{scr}"'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
print(res.stdout)
