import subprocess
import os

dwg = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\11-2025-1-ТКР9-1 Кальмиус варианты.dwg"
scr = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_server_workdir\test_entmod.scr"

lisp_code = """
(setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
(while (setq item (assoc 350 dict))
  (setq ent (cdr item))
  (setq edata (entget ent))
  (if (assoc 7 edata)
    (setq edata (subst (cons 7 "monochrome.ctb") (assoc 7 edata) edata))
    (setq edata (append edata (list (cons 7 "monochrome.ctb"))))
  )
  (setq flags (cdr (assoc 70 edata)))
  (if flags
    (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))
  )
  (entmod edata)
  (setq dict (cdr (member item dict)))
)
(command "_.-EXPORT" "_PDF" "_All" "C:/Users/makeden/Documents/test_entmod.pdf")
(command "_.QUIT" "_Y")
"""

with open(scr, "w", encoding="cp1251") as f:
    f.write(lisp_code)

cmd = f'"E:\\Autodesk\\acad\\AutoCAD 2022\\accoreconsole.exe" /i "{dwg}" /l ru-RU /s "{scr}"'
res = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
print("PDF exists:", os.path.exists(r"C:\Users\makeden\Documents\test_entmod.pdf"))
print(res.stdout)
