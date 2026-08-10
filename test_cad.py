import os
import subprocess
import threading
import sys

ctb = 'monochrome.ctb'
safe_pdf_path = 'C:/Users/makeden/Documents/югдорпроект_конвертер_PDF/out.pdf'
lisp_code = f'(setvar "FILEDIA" 0) (setvar "CMDDIA" 0) (setvar "PROXYNOTICE" 0) (setvar "EXPERT" 5) (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT")) (while (setq item (assoc 350 dict)) (setq ent (cdr item)) (setq edata (entget ent)) (if (assoc 7 edata) (setq edata (subst (cons 7 "{ctb}") (assoc 7 edata) edata)) (setq edata (append edata (list (cons 7 "{ctb}"))))) (setq flags (cdr (assoc 70 edata))) (if flags (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))) (entmod edata) (setq dict (cdr (member item dict)))) (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path}") (command "_.QUIT" "_Y")'

with open('test.scr', 'w', encoding='cp1251') as f:
    f.write(lisp_code + '\n')

scr_abspath = os.path.abspath('test.scr')
cmd = [r"E:\Autodesk\acad\AutoCAD 2022\accoreconsole.exe", "/i", r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\test.dwg", "/l", "ru-RU", "/s", scr_abspath]

p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="ignore")

def print_output(stream):
    for line in stream:
        sys.stdout.write(line)
        sys.stdout.flush()

t1 = threading.Thread(target=print_output, args=(p.stdout,))
t2 = threading.Thread(target=print_output, args=(p.stderr,))
t1.start()
t2.start()

try:
    p.wait(timeout=30)
except subprocess.TimeoutExpired:
    print("\n\nTIMEOUT EXPIRED! KILLING PROCESS...")
    p.kill()

t1.join()
t2.join()
