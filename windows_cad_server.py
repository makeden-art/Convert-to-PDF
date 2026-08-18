import os
import subprocess
import time
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import shutil
import glob
import sys

try:
    import win32com.client
except ImportError:
    print("Устанавливаем pywin32 для поддержки MS Office...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pywin32"])
    import win32com.client

app = FastAPI(title="AutoCAD Print Server")

def find_accoreconsole():
    search_paths = [
        r"C:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"D:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"E:\Program Files\Autodesk\AutoCAD *\accoreconsole.exe",
        r"C:\Autodesk\AutoCAD *\accoreconsole.exe",
        r"D:\Autodesk\AutoCAD *\accoreconsole.exe",
        r"E:\Autodesk\acad\AutoCAD *\accoreconsole.exe"
    ]
    for pattern in search_paths:
        matches = glob.glob(pattern)
        if matches:
            return sorted(matches, reverse=True)[0]
    default_p = r"C:\Program Files\Autodesk\AutoCAD 2022\accoreconsole.exe"
    return default_p if os.path.exists(default_p) else None

def check_word_installed() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"):
            return True
    except Exception:
        return False

def check_excel_installed() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Excel.Application"):
            return True
    except Exception:
        return False

@app.get("/")
@app.get("/status")
def server_status():
    acad_p = find_accoreconsole()
    acad_ok = bool(acad_p and os.path.exists(acad_p))
    word_ok = check_word_installed()
    excel_ok = check_excel_installed()
    return {
        "status": "ok",
        "autocad": {
            "available": acad_ok,
            "path": acad_p if acad_ok else None
        },
        "word": {
            "available": word_ok
        },
        "excel": {
            "available": excel_ok
        }
    }

ACAD_PATH = find_accoreconsole()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SCRIPT_DIR, "cad_server_workdir")
os.makedirs(WORK_DIR, exist_ok=True)

@app.post("/convert")
def convert(
    file: UploadFile = File(None),
    ctb: str = Form(None),
    profile: str = Form(None),
    smb_dwg_path: str = Form(None),
    smart_search: str = Form(None)
):
    if not ACAD_PATH or not os.path.exists(ACAD_PATH):
        return JSONResponse(status_code=400, content={"error": "AutoCAD (accoreconsole.exe) не найден на этом компьютере. Скрипт CAD-сервера должен запускаться на компьютере с установленным AutoCAD."})
    import tempfile
    import uuid
    safe_uid = uuid.uuid4().hex
    temp_dir = tempfile.gettempdir()
    
    dwg_path = None
    if smb_dwg_path:
        if not os.path.exists(smb_dwg_path):
            return JSONResponse(status_code=400, content={"error": f"Сетевой путь недоступен для CAD сервера: {smb_dwg_path}. Возможно, служба запущена от имени System, а не вашего пользователя."})
        print(f"Используем прямой путь к SMB: {smb_dwg_path}")
        safe_dwg_path = smb_dwg_path
        safe_filename = os.path.basename(smb_dwg_path)
        pdf_path = os.path.join(temp_dir, f"result_{safe_uid}.pdf")
    elif file:
        safe_filename = file.filename.replace(" ", "_")
        dwg_path = os.path.join(WORK_DIR, safe_filename)
        pdf_path = dwg_path.replace(".dwg", ".pdf")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        with open(dwg_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        safe_dwg_path = os.path.join(temp_dir, f"temp_{safe_uid}.dwg")
        shutil.copy2(dwg_path, safe_dwg_path)
        
        # МАГИЧЕСКИЙ ПОИСК: Ищем оригинальный файл по размеру и первым байтам на шаре
        try:
            target_size = os.path.getsize(safe_dwg_path)
            found_local = None
            for root, dirs, files_in_dir in os.walk(r"E:\share_test"):
                for f in files_in_dir:
                    if f.lower().endswith('.dwg'):
                        f_path = os.path.join(root, f)
                        if os.path.getsize(f_path) == target_size:
                            # Проверяем первые 4КБ, чтобы избежать коллизий
                            with open(safe_dwg_path, 'rb') as f1, open(f_path, 'rb') as f2:
                                if f1.read(4096) == f2.read(4096):
                                    found_local = f_path
                                    break
                if found_local:
                    break
            
            if found_local:
                print(f"Магический поиск: найден оригинальный файл -> {found_local}")
                safe_dwg_path = found_local
                safe_filename = os.path.basename(found_local)
        except Exception as e:
            print(f"Ошибка магического поиска: {e}")
    else:
        return JSONResponse(status_code=400, content={"error": "Нет файла и нет пути"})
        
    safe_pdf_path = os.path.join(temp_dir, f"temp_{safe_uid}.pdf")

    scr_path = os.path.join(temp_dir, f"print_{safe_uid}.scr")
    # Скрипт печатает ТОЛЬКО Листы (Paper Space). Если ctb не пустой, принудительно ставим его.
    if ctb and ctb.lower() != "none":
        ctb_lisp = f"""(setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT")) (while (setq item (assoc 350 dict)) (setq ent (cdr item)) (setq edata (entget ent)) (if (assoc 7 edata) (setq edata (subst (cons 7 "{ctb}") (assoc 7 edata) edata)) (setq edata (append edata (list (cons 7 "{ctb}"))))) (setq flags (cdr (assoc 70 edata))) (if flags (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))) (entmod edata) (setq dict (cdr (member item dict))))"""
    else:
        ctb_lisp = ""
        
    # ATTSYNC не поддерживается в accoreconsole (вызывает Unknown command ATTSYNC и ломает скрипт)
    attsync_lisp = ""

    # Подключение общих шрифтов и стилей из папки C:\Common (только если НЕ передан профиль)
    if not profile:
        common_path_lisp = """(vl-catch-all-apply 'setenv (list "PrinterStyleSheetDir" "C:\\\\Common\\\\Plot_Styles")) (setq curacad (getenv "ACAD")) (if (not (vl-string-search "C:\\\\Common\\\\Fonts" curacad)) (vl-catch-all-apply 'setenv (list "ACAD" (strcat "C:\\\\Common\\\\Fonts;" curacad))))"""
    else:
        common_path_lisp = ""

    pdf_prefix = safe_pdf_path.replace("\\", "/").replace(".pdf", "")
    force_smart_val = "T" if smart_search and smart_search.lower() == "true" else "nil"
    lisp_code = f"""(setvar "FILEDIA" 0) (setvar "CMDDIA" 0) (setvar "PROXYNOTICE" 0) (setvar "EXPERT" 5) (setvar "PROXYSHOW" 1)
(vl-catch-all-apply 'setvar (list "PDFSHX" 0)) (vl-catch-all-apply 'setvar (list "EPDFSHX" 0)) {common_path_lisp} {attsync_lisp} {ctb_lisp}

(defun GetFrames ( / ss i ent edata pts xmin ymin xmax ymax w h frames)
  (setq frames '())
  (setq ss (ssget "X" '((0 . "LWPOLYLINE") (70 . 1) (410 . "Model"))))
  (if ss
    (progn (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i) edata (entget ent) pts '())
        (foreach item edata (if (= (car item) 10) (setq pts (cons (cdr item) pts))))
        (if (= (length pts) 4)
          (progn (setq xmin (caar pts) xmax (caar pts) ymin (cadar pts) ymax (cadar pts))
            (foreach p (cdr pts) (if (< (car p) xmin) (setq xmin (car p))) (if (> (car p) xmax) (setq xmax (car p))) (if (< (cadr p) ymin) (setq ymin (cadr p))) (if (> (cadr p) ymax) (setq ymax (cadr p))))
            (setq w (- xmax xmin) h (- ymax ymin))
            (if (and (> w 150) (> h 150) (< w 5000) (< h 5000))
              (setq frames (cons (list xmin ymin xmax ymax w h) frames)))))
        (setq i (1+ i)))))
  frames
)

(defun ExportModelFrames ()
  (setvar "TILEMODE" 1)
  (setq frames (GetFrames))
  (setq rowTol 150.0)
  (setq frames (vl-sort frames (function (lambda (a b) (if (> (abs (- (cadr a) (cadr b))) rowTol) (> (cadr a) (cadr b)) (< (car a) (car b)))))))
  (setq idx 1)
  (foreach frm frames
    (setq w (nth 4 frm) h (nth 5 frm))
    (if (> w h)
      (cond ((and (< w 450) (< h 320)) (setq paper "ISO_full_bleed_A3_(420.00_x_297.00_MM)"))
            ((and (< w 620) (< h 450)) (setq paper "ISO_full_bleed_A2_(594.00_x_420.00_MM)"))
            ((and (< w 870) (< h 620)) (setq paper "ISO_full_bleed_A1_(841.00_x_594.00_MM)"))
            (t (setq paper "ISO_full_bleed_A0_(1189.00_x_841.00_MM)")))
      (cond ((and (< w 320) (< h 450)) (setq paper "ISO_full_bleed_A3_(297.00_x_420.00_MM)"))
            ((and (< w 450) (< h 620)) (setq paper "ISO_full_bleed_A2_(420.00_x_594.00_MM)"))
            ((and (< w 620) (< h 870)) (setq paper "ISO_full_bleed_A1_(594.00_x_841.00_MM)"))
            (t (setq paper "ISO_full_bleed_A0_(841.00_x_1189.00_MM)"))))
    (setq outpath (strcat "{pdf_prefix}_" (itoa idx) ".pdf"))
    (command "_.-PLOT" "_Y" "Model" "DWG To PDF.pc3" paper "_M" "_L" "_N" "_W" (list (car frm) (cadr frm)) (list (caddr frm) (cadddr frm)) "_F" "_C" "_Y" "monochrome.ctb" "_Y" "_W" outpath "_N" "_Y")
    (setq idx (1+ idx))
  )
)

(defun ExportPaperSpace ()
  (setvar "TILEMODE" 0)
  (vl-load-com)
  (vl-catch-all-apply
    (function
      (lambda ()
        (vlax-for layout (vla-get-Layouts (vla-get-ActiveDocument (vlax-get-acad-object)))
          (if (= (vla-get-ModelType layout) :vlax-false)
            (progn
              (vla-put-ConfigName layout "DWG To PDF.pc3")
              (vla-put-StandardScale layout 0) ; acScaleToFit
              ; (vla-put-PlotType layout 1) ; acExtents - sometimes cuts off if elements are far
              ; By default we keep the PlotType as is (usually acLayout), but force the PDF printer
            )
          )
        )
      )
    )
  )
  (vl-catch-all-apply (function (lambda () (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace("\\", "/")}"))))
)

(setq force-smart {force_smart_val})

(if force-smart
  (ExportModelFrames)
  (ExportPaperSpace)
)
(command "_.QUIT" "_Y")"""
    
    # Записываем скрипт в одну строку в кодировке ANSI для стабильности
    with open(scr_path, "w", encoding="cp1251") as f:
        # Убираем переносы строк для безопасности, но LISP работает и с ними
        f.write(lisp_code.replace("\\n", " ") + "\n")

    # 3. Запускаем AutoCAD Core Console в фоне
    print(f"Печатаем {safe_filename} с помощью {ACAD_PATH} (безопасный путь: {safe_dwg_path})...")
    cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
    if profile:
        cmd.extend(["/p", profile])
    
    start_time = time.time()
    try:
        # Убрали shell=True, чтобы процесс не осиротел при таймауте, иначе Python не сможет убить его
        result = subprocess.run(cmd, shell=False, capture_output=True, timeout=1200)
        
        # accoreconsole.exe outputs in UTF-16 on Windows
        try:
            stdout_text = result.stdout.decode("utf-16", errors="replace")
        except:
            stdout_text = result.stdout.decode("cp1251", errors="replace")
            
    except subprocess.TimeoutExpired:
        print(f"ТАЙМАУТ ПЕЧАТИ (1200 сек)! Убиваем зависший процесс AutoCAD для файла: {safe_filename}")
        subprocess.run('taskkill /F /IM accoreconsole.exe', shell=True)
        return JSONResponse(status_code=504, content={"error": "AutoCAD timeout 1200s. Process killed.", "log": ""})
    
    # Проверяем, не выдал ли AutoCAD ошибку об отсутствии листов
    if "ERROR_NO_LAYOUTS" in stdout_text:
        print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
        return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": stdout_text})

    # Если был умный поиск, объединяем PDF-файлы
    if smart_search and smart_search.lower() == "true":
        import glob
        from pypdf import PdfWriter, PdfReader
        
        pdf_prefix = safe_pdf_path.replace(".pdf", "")
        pdf_files = glob.glob(f"{pdf_prefix}_*.pdf")
        
        if pdf_files:
            # Сортируем по индексу (по номеру файла _1, _2 и т.д.)
            pdf_files.sort(key=lambda f: int(f.split("_")[-1].replace(".pdf", "")))
            
            try:
                merger = PdfWriter()
                for pdf in pdf_files:
                    merger.append(pdf)
                merger.write(safe_pdf_path)
                merger.close()
            except Exception as e:
                print(f"Ошибка при объединении PDF: {e}")
                
            # Удаляем временные куски
            for pdf in pdf_files:
                try: os.remove(pdf)
                except: pass
        else:
            print(f"Умный поиск не нашел рамок для файла {safe_filename}!")
            return JSONResponse(status_code=400, content={"error": "В Модели не найдено ни одной прямоугольной рамки поперечника.", "log": stdout_text})
    
    # Копируем PDF обратно
    if os.path.exists(safe_pdf_path):
        # Удаляем полностью пустые страницы
        try:
            import fitz
            doc = fitz.open(safe_pdf_path)
            if len(doc) > 1:
                pages_to_keep = []
                for pno in range(len(doc)):
                    page = doc[pno]
                    text = page.get_text().strip()
                    images = page.get_images()
                    drawings = page.get_drawings()
                    # Если есть хоть какой-то текст, картинки или больше 4 элементов векторной графики (рамка + видовой экран)
                    if text or images or len(drawings) > 4:
                        pages_to_keep.append(pno)
                
                if len(pages_to_keep) > 0 and len(pages_to_keep) < len(doc):
                    print(f"Очистка пустых страниц. Оставляем: {pages_to_keep}")
                    new_doc = fitz.open()
                    for pno in pages_to_keep:
                        new_doc.insert_pdf(doc, from_page=pno, to_page=pno)
                    doc.close()
                    # Пересохраняем поверх того же файла
                    tmp_clean = safe_pdf_path + ".clean.pdf"
                    new_doc.save(tmp_clean)
                    new_doc.close()
                    os.replace(tmp_clean, safe_pdf_path)
                else:
                    doc.close()
            else:
                doc.close()
        except Exception as e:
            print(f"Не удалось удалить пустые страницы: {e}")

        shutil.copy2(safe_pdf_path, pdf_path)
        
    # Убираем за собой
    try:
        if dwg_path and os.path.exists(safe_dwg_path):
            os.remove(safe_dwg_path)
        if os.path.exists(safe_pdf_path): os.remove(safe_pdf_path)
        os.remove(scr_path)
    except Exception:
        pass
    
    print(f"Время выполнения: {time.time() - start_time:.1f} сек")
    
    # 4. Возвращаем PDF
    if os.path.exists(pdf_path):
        return FileResponse(path=pdf_path, filename=safe_filename.replace(".dwg", ".pdf"), media_type='application/pdf')
    else:
        print("ОШИБКА ПЕЧАТИ:")
        print(stdout_text)
        return JSONResponse(status_code=500, content={"error": "Не удалось создать PDF. Проверьте консоль сервера.", "log": stdout_text})

@app.post("/convert-office")
def convert_office(file: UploadFile = File(...)):
    safe_filename = file.filename.replace(" ", "_")
    ext = os.path.splitext(safe_filename)[1].lower()
    in_path = os.path.join(WORK_DIR, safe_filename)
    pdf_path = in_path.replace(ext, ".pdf")
    
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    with open(in_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    start_time = time.time()
    import pythoncom
    pythoncom.CoInitialize()
    try:
        if ext in [".doc", ".docx", ".rtf"]:
            print(f"Конвертация Word: {safe_filename}")
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False
            doc = word.Documents.Open(in_path, ReadOnly=True)
            # Используем ExportAsFixedFormat для более надежного и точного сохранения (wdExportFormatPDF = 17)
            doc.ExportAsFixedFormat(
                OutputFileName=pdf_path,
                ExportFormat=17,         # PDF
                OpenAfterExport=False,
                OptimizeFor=0,           # wdExportOptimizeForPrint
                CreateBookmarks=1,       # wdExportCreateHeadingBookmarks
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False
            )
            doc.Close(SaveChanges=False)
            word.Quit()
            
        elif ext in [".xls", ".xlsx"]:
            print(f"Конвертация Excel: {safe_filename}")
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            
            # Switch ActivePrinter ONLY to standard 'Microsoft Print to PDF' (built-in Windows 10/11).
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows NT\CurrentVersion\Devices')
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, i)
                    if 'Microsoft Print to PDF' in name:
                        port = value.split(',')[1]
                        printer_str = f'{name} on {port}'
                        try:
                            excel.ActivePrinter = printer_str
                            break
                        except:
                            continue
            except:
                pass
                
            wb = excel.Workbooks.Open(in_path)
            
            for ws in wb.Worksheets:
                try:
                    ws.PageSetup.PaperSize = 9 # xlPaperA4
                    ws.PageSetup.Zoom = False
                    ws.PageSetup.FitToPagesWide = 1
                    ws.PageSetup.FitToPagesTall = False
                    
                    # Ставим поля строго по 0.5 см, колонтитулы 0, без центрирования
                    cm_to_pts = excel.CentimetersToPoints(0.5)
                    ws.PageSetup.LeftMargin = cm_to_pts
                    ws.PageSetup.RightMargin = cm_to_pts
                    ws.PageSetup.TopMargin = cm_to_pts
                    ws.PageSetup.BottomMargin = cm_to_pts
                    ws.PageSetup.HeaderMargin = 0
                    ws.PageSetup.FooterMargin = 0
                    
                    ws.PageSetup.CenterHorizontally = False
                    ws.PageSetup.CenterVertically = False
                    
                    # Фикс для "пустых страниц": вытаскиваем Print_Area из именованных диапазонов,
                    # так как COM иногда возвращает пустую строку для PageSetup.PrintArea.
                    if not ws.PageSetup.PrintArea:
                        for i in range(1, wb.Names.Count + 1):
                            n = wb.Names(i)
                            if "Print_Area" in n.Name and ws.Name in n.Name:
                                ws.PageSetup.PrintArea = n.RefersTo
                                break
                                
                except Exception as e:
                    print(f"PageSetup Error on sheet {ws.Name}: {e}")
            
            # Используем ExportAsFixedFormat как в вашем скрипте, на уровне всей книги
            wb.ExportAsFixedFormat(
                Type=0,              # xlTypePDF
                Filename=pdf_path,
                Quality=0,           # xlQualityStandard
                IncludeDocProperties=True,
                IgnorePrintAreas=False,  # ВАЖНО: уважать сохраненную область печати!
                OpenAfterPublish=False
            )
            wb.Close(SaveChanges=False)
            excel.Quit()
            
        else:
            return JSONResponse(status_code=400, content={"error": f"Формат {ext} не поддерживается"})
            
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        print(f"ОШИБКА OFFICE:\n{err}")
        subprocess.run('taskkill /F /IM WINWORD.EXE', shell=True)
        subprocess.run('taskkill /F /IM EXCEL.EXE', shell=True)
        return JSONResponse(status_code=500, content={"error": "Ошибка MS Office", "log": err})
    finally:
        try:
            os.remove(in_path)
        except:
            pass
        pythoncom.CoUninitialize()
            
    print(f"Время выполнения: {time.time() - start_time:.1f} сек")
    
    if os.path.exists(pdf_path):
        return FileResponse(path=pdf_path, filename=safe_filename.replace(ext, ".pdf"), media_type='application/pdf')
    else:
        return JSONResponse(status_code=500, content={"error": "PDF не создан.", "log": ""})

if __name__ == "__main__":
    print("--------------------------------------------------")
    print(" Сервер AutoCAD Core Console запущен! (Стабильная версия)")
    print(" Ожидание чертежей на порту 8000...")
    print("--------------------------------------------------")
    uvicorn.run(app, host="0.0.0.0", port=8000)
