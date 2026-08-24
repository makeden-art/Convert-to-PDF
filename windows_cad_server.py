import os
import subprocess
import time
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import shutil
import glob
import sys
import tempfile
import uuid
import threading

try:
    import win32com.client
except ImportError:
    print("Устанавливаем pywin32 для работы MS Office...")
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

SHARE_LOCAL_PATH = os.environ.get("SHARE_LOCAL_PATH", r"E:\share_test")
SHARE_UNC_PATH = os.environ.get("SHARE_UNC_PATH", r"\\192.168.88.14\share_test")
_office_lock = threading.Lock()
ACAD_TIMEOUT_SEC = 900

def unc_to_local(path: str) -> str:
    normalized = path.replace("/", "\\")
    unc_norm = SHARE_UNC_PATH.replace("/", "\\").rstrip("\\")
    local_norm = SHARE_LOCAL_PATH.rstrip("\\")
    if normalized.lower().startswith(unc_norm.lower()):
        return local_norm + normalized[len(unc_norm):]
    return path

def _generate_lisp_script(safe_pdf_path: str, force_smart: bool, ctb: str = None) -> str:
    """Генерирует LISP-код для печати DWG -> PDF."""
    ctb_lisp = ""
    if ctb and ctb.lower() != "none":
        ctb_lisp = f"""
(vl-catch-all-apply 'setvar (list "PLOTSTYLEMODE" 0))
(vl-catch-all-apply 'setvar (list "CPROFILE" "<<VANILLA>>"))
(vl-catch-all-apply
  (function
    (lambda ()
      (vlax-for layout (vla-get-Layouts (vla-get-ActiveDocument (vlax-get-acad-object)))
        (vla-put-StyleSheet layout "{ctb}")
      )
    )
  )
)
"""

    attsync_lisp = """
(vl-catch-all-apply
  (function
    (lambda ()
      (setq blks (vla-get-Blocks (vla-get-ActiveDocument (vlax-get-acad-object))))
      (vlax-for blk blks
        (if (= (vla-get-HasAttributes blk) :vlax-true)
          (command "_.ATTSYNC" "_N" (vla-get-Name blk))
        )
      )
    )
  )
)
"""

    # Динамический поиск C:\Common
    common_path_lisp = """
(setq paths (getenv "ACAD"))
(if (and (findfile "C:\\\\Common") (not (vl-string-search "C:\\\\Common" paths)))
  (setenv "ACAD" (strcat paths ";C:\\\\Common;C:\\\\Common\\\\Support;C:\\\\Common\\\\Fonts;C:\\\\Common\\\\LISP"))
)
"""

    force_smart_val = "T" if force_smart else "nil"
    pdf_prefix = safe_pdf_path.replace("\\", "/").replace(".pdf", "")
    
    lisp_code = f"""(vl-load-com) (setvar "FILEDIA" 0) (setvar "BACKGROUNDPLOT" 0) (setvar "CMDDIA" 0) (setvar "PROXYNOTICE" 0) (setvar "EXPERT" 5) (setvar "PROXYSHOW" 1)
(vl-catch-all-apply 'setvar (list "PDFSHX" 0)) (vl-catch-all-apply 'setvar (list "EPDFSHX" 0)) {common_path_lisp} {attsync_lisp} {ctb_lisp}

(defun GetFrames ( / ss i ent edata pts xmin ymin xmax ymax w h frames obj ll ur blkName layName)
  (princ "\n[DEBUG] Starting GetFrames...")
  (setq frames (quote ()))
  
  (defun IsValidName (name)
    (if (not name) (setq name ""))
    (setq name (strcase name))
    (or (vl-string-search "ФОРМАТ" name) 
        (vl-string-search "РАМКА" name) 
        (vl-string-search "ШТАМП" name)
        (vl-string-search "FORM" name) 
        (vl-string-search "FRAME" name) 
        (vl-string-search "STAMP" name))
  )

  (princ "\n[DEBUG] Searching for LWPOLYLINE...")
  ;; 1. Search for LWPOLYLINE
  (setq ss (ssget "X" (quote ((0 . "LWPOLYLINE") (410 . "Model")))))
  (if ss
    (progn 
      (princ (strcat "\n[DEBUG] Found " (itoa (sslength ss)) " LWPOLYLINEs."))
      (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i) edata (entget ent))
        (setq layName (cdr (assoc 8 edata)))
        (if (IsValidName layName)
          (progn
            (setq obj (vlax-ename->vla-object ent))
            (setq ll (vlax-make-safearray vlax-vbDouble (quote (0 . 2))) ur (vlax-make-safearray vlax-vbDouble (quote (0 . 2))))
            (if (not (vl-catch-all-error-p (vl-catch-all-apply (quote vla-GetBoundingBox) (list obj (quote ll) (quote ur)))))
              (progn
                (setq ll (vlax-safearray->list ll) ur (vlax-safearray->list ur))
                (setq xmin (car ll) ymin (cadr ll) xmax (car ur) ymax (cadr ur) w (- xmax xmin) h (- ymax ymin))
                (if (and (> w 150) (> h 150) (< w 5000) (< h 5000))
                  (setq frames (cons (list xmin ymin xmax ymax w h) frames)))
              )
            )
          )
        )
        (setq i (1+ i))
      )
    )
    (princ "\n[DEBUG] No LWPOLYLINEs found.")
  )
        
  (princ "\n[DEBUG] Searching for INSERTs...")
  ;; 2. Search for INSERT
  (setq ss (ssget "X" (quote ((0 . "INSERT") (410 . "Model")))))
  (if ss
    (progn 
      (princ (strcat "\n[DEBUG] Found " (itoa (sslength ss)) " INSERTs."))
      (setq i 0)
      (while (< i (sslength ss))
        (setq ent (ssname ss i) edata (entget ent))
        (setq layName (cdr (assoc 8 edata)))
        (setq obj (vlax-ename->vla-object ent))
        (setq blkName "")
        (if (vlax-property-available-p obj (quote EffectiveName))
          (setq blkName (vla-get-EffectiveName obj))
        )
        (if (or (IsValidName layName) (IsValidName blkName))
          (progn
            (setq ll (vlax-make-safearray vlax-vbDouble (quote (0 . 2))) ur (vlax-make-safearray vlax-vbDouble (quote (0 . 2))))
            (if (not (vl-catch-all-error-p (vl-catch-all-apply (quote vla-GetBoundingBox) (list obj (quote ll) (quote ur)))))
              (progn
                (setq ll (vlax-safearray->list ll) ur (vlax-safearray->list ur))
                (setq xmin (car ll) ymin (cadr ll) xmax (car ur) ymax (cadr ur) w (- xmax xmin) h (- ymax ymin))
                (if (and (> w 150) (> h 150) (< w 5000) (< h 5000))
                  (setq frames (cons (list xmin ymin xmax ymax w h) frames)))
              )
            )
          )
        )
        (setq i (1+ i))
      )
    )
    (princ "\n[DEBUG] No INSERTs found.")
  )
        
  (princ "\n[DEBUG] Filtering duplicates...")
  (setq frames (vl-sort frames (function (lambda (a b) (if (> (abs (- (cadr a) (cadr b))) 10.0) (> (cadr a) (cadr b)) (< (car a) (car b)))))))
  (setq uniqFrames (quote ()))
  (setq lastFrm nil)
  (foreach frm frames
    (if (not lastFrm)
      (progn (setq uniqFrames (cons frm uniqFrames)) (setq lastFrm frm))
      (if (or (> (abs (- (car frm) (car lastFrm))) 50.0) (> (abs (- (cadr frm) (cadr lastFrm))) 50.0))
        (progn (setq uniqFrames (cons frm uniqFrames)) (setq lastFrm frm))
      )
    )
  )
  (princ (strcat "\n[DEBUG] Finished GetFrames. Found valid frames: " (itoa (length uniqFrames))))
  uniqFrames
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
  (vl-catch-all-apply
    (function (lambda ()
      (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace(chr(92), chr(47))}")
    ))
  )
)

(setq force-smart {force_smart_val})
(if force-smart
  (ExportModelFrames)
  (ExportPaperSpace)
)
(command "_.QUIT" "_Y")"""
    return lisp_code.replace("\n", " ") + "\n"

def _merge_pdf_parts(pdf_prefix_base: str, output_pdf_path: str):
    """Склеивает промежуточные PDF файлы (от поиска рамок) в один."""
    from pypdf import PdfWriter
    pdf_files = sorted(
        glob.glob(f"{pdf_prefix_base}_[0-9]*.pdf"),
        key=lambda f: int(f.rsplit("_", 1)[-1].replace(".pdf", ""))
    )
    if pdf_files:
        try:
            merger = PdfWriter()
            for pdf in pdf_files:
                merger.append(pdf)
            merger.write(output_pdf_path)
            merger.close()
        except Exception as e:
            print(f"Ошибка при объединении PDF (smart): {e}")
        
        # Удаляем промежуточные куски
        for pdf in pdf_files:
            try: os.remove(pdf)
            except: pass
    else:
        raise ValueError("В Модели не найдено ни одной прямоугольной рамки поперечника.")

def _remove_empty_pages(pdf_path: str):
    """Рендерит страницы в мини-картинки и удаляет пустые (шаблонные) листы."""
    if not os.path.exists(pdf_path):
        return
        
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if len(doc) <= 1:
            doc.close()
            return
            
        pages_to_keep = []
        mat = fitz.Matrix(0.08, 0.08)  # 8% масштаб
        
        for pno in range(len(doc)):
            page = doc[pno]
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            total = len(pix.samples)
            if total == 0:
                continue
            white = sum(1 for b in pix.samples if b > 245)
            if white / total < 0.98:  # есть реальный контент
                pages_to_keep.append(pno)

        print(f"Страниц: {len(doc)}, непустых: {len(pages_to_keep)}")
        if 0 < len(pages_to_keep) < len(doc):
            new_doc = fitz.open()
            for pno in pages_to_keep:
                new_doc.insert_pdf(doc, from_page=pno, to_page=pno)
            doc.close()
            
            tmp_clean = pdf_path + ".clean.pdf"
            new_doc.save(tmp_clean)
            new_doc.close()
            os.replace(tmp_clean, pdf_path)
        else:
            doc.close()
    except Exception as e:
        print(f"Не удалось удалить пустые страницы: {e}")

@app.post("/convert")
def convert(
    file: UploadFile = File(None),
    ctb: str = Form(None),
    profile: str = Form(None),
    smb_dwg_path: str = Form(None),
    smart_search: str = Form(None)
):
    if not ACAD_PATH or not os.path.exists(ACAD_PATH):
        return JSONResponse(status_code=400, content={"error": "AutoCAD (accoreconsole.exe) не найден на сервере."})

    safe_uid = uuid.uuid4().hex
    temp_dir = tempfile.gettempdir()
    
    dwg_path = None
    safe_dwg_path = None
    safe_pdf_path = None
    scr_path = None
    stdout_text = ""
    is_smart = False
    
    try:
        # 1. Определение путей и копирование файла
        if smb_dwg_path:
            local_path = unc_to_local(smb_dwg_path)
            if os.path.exists(local_path):
                safe_dwg_path = local_path
                safe_filename = os.path.basename(local_path)
            else:
                return JSONResponse(status_code=400, content={"error": f"Файл не найден по локальному пути: {local_path}"})
        elif file:
            safe_filename = file.filename.replace(" ", "_")
            dwg_path = os.path.join(temp_dir, f"{safe_uid}_{safe_filename}")
            with open(dwg_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            safe_dwg_path = dwg_path
        else:
            return JSONResponse(status_code=400, content={"error": "Не передан файл."})

        # Итоговые пути
        safe_pdf_path = os.path.join(temp_dir, f"{safe_uid}_{safe_filename}.pdf")
        scr_path = os.path.join(WORK_DIR, f"print_{safe_uid}.scr")

        # 2. Флаги логики
        name_lower = safe_filename.lower()
        if "_model" in name_lower or "модель" in name_lower or "поперечник" in name_lower:
            smart_search = "true"
        is_smart = (smart_search and smart_search.lower() == "true")

        # 3. Генерация и запись LISP скрипта
        lisp_code = _generate_lisp_script(safe_pdf_path, is_smart, ctb)
        with open(scr_path, "w", encoding="cp1251") as f:
            f.write(lisp_code)

        # 4. Запуск AutoCAD
        print(f"Печатаем {safe_filename} с помощью {ACAD_PATH} (путь: {safe_dwg_path})...")
        cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
        if profile:
            cmd.extend(["/p", profile])
        
        start_time = time.time()
        try:
            result = subprocess.run(cmd, shell=False, capture_output=True, timeout=ACAD_TIMEOUT_SEC)
            try:
                stdout_text = result.stdout.decode("utf-16", errors="replace")
            except Exception:
                stdout_text = result.stdout.decode("cp1251", errors="replace")
        except subprocess.TimeoutExpired as e:
            subprocess.run('taskkill /F /IM accoreconsole.exe', shell=True)
            out = ""
            if e.stdout: out += e.stdout.decode('cp1251', errors='replace')
            if e.stderr: out += "\n" + e.stderr.decode('cp1251', errors='replace')
            print("="*50)
            print("AUTOCAD TIMEOUT LOG:")
            print(out)
            print("="*50)
            try:
                with open("C:\timeout_log.txt", "w", encoding="utf-8") as f:
                    f.write(out)
            except: pass
            return JSONResponse(status_code=504, content={"error": f"AutoCAD timeout {ACAD_TIMEOUT_SEC}s. Process killed.", "log": out})

        if "ERROR_NO_LAYOUTS" in stdout_text:
            print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
            return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": stdout_text})

        # 5. Сборка PDF и удаление пустых листов
        if is_smart:
            try:
                pdf_prefix_base = safe_pdf_path.replace(".pdf", "")
                _merge_pdf_parts(pdf_prefix_base, safe_pdf_path)
            except ValueError as e:
                return JSONResponse(status_code=400, content={"error": str(e), "log": stdout_text})
        else:
            # Чистим мусорные файлы _ps_ на всякий случай
            for f in glob.glob(safe_pdf_path.replace(".pdf", "") + "_ps_*.pdf"):
                try: os.remove(f)
                except Exception: pass

        _remove_empty_pages(safe_pdf_path)

        # 6. Возврат результата
        print(f"Время выполнения: {time.time() - start_time:.1f} сек")
        if os.path.exists(safe_pdf_path):
            return FileResponse(path=safe_pdf_path, filename=safe_filename.replace(".dwg", ".pdf"), media_type='application/pdf')
        else:
            print("ОШИБКА ПЕЧАТИ:")
            print(stdout_text)
            return JSONResponse(status_code=500, content={"error": "Не удалось создать PDF. Проверьте консоль сервера.", "log": stdout_text})

    finally:
        # 7. Гарантированная очистка (в любом случае: и при успехе, и при ошибке)
        try:
            if dwg_path and os.path.exists(dwg_path):
                os.remove(dwg_path)
            if scr_path and os.path.exists(scr_path):
                os.remove(scr_path)
            
            # Удаляем PDF-ку, так как FileResponse может держать её открытой только если мы передаем background task,
            # но FastAPI FileResponse сам не удаляет файл по умолчанию. 
            # ВАЖНО: Если мы удалим файл ДО того как FastAPI его отправит, пользователь получит 0 байт!
            # Идеально было бы использовать BackgroundTasks, но пока оставим как было, только удалим scr_path
            # Для безопасноти оставляем safe_pdf_path висеть во временной папке (temp_dir), ОС сама её почистит
        except Exception as e:
            print(f"Ошибка при очистке временных файлов: {e}")

@app.post("/convert-office")
def convert_office(file: UploadFile = File(...)):
    if not _office_lock.acquire(timeout=600):
        return JSONResponse(status_code=503, content={"error": "Office конвертер занят, попробуйте позже."})
    try:
        return _convert_office_impl(file)
    finally:
        _office_lock.release()

def _convert_office_impl(file: UploadFile):
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
    try:
        if ext in [".doc", ".docx", ".rtf"]:
            print(f"Конвертация Word: {safe_filename}")
            pythoncom.CoInitialize()
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False
            doc = word.Documents.Open(in_path, ReadOnly=True)
            doc.ExportAsFixedFormat(
                OutputFileName=pdf_path,
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=0,
                CreateBookmarks=1,
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False
            )
            doc.Close(SaveChanges=False)
            word.Quit()
            
        elif ext in [".xls", ".xlsx"]:
            print(f"Конвертация Excel: {safe_filename}")
            import excel_export
            from pathlib import Path
            excel_export.excel_to_pdf(Path(in_path), Path(pdf_path))
            
            if excel_export._pdf_is_empty(Path(pdf_path)):
                return JSONResponse(status_code=500, content={"error": "Получен пустой PDF после экспорта из Excel", "log": ""})

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
        except Exception:
            pass
        if ext in [".doc", ".docx", ".rtf"]:
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
