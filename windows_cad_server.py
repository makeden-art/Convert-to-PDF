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
def convert_cad(file: UploadFile = File(None), ctb: str = Form("monochrome.ctb"), smb_dwg_path: str = Form(None)):
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
    else:
        return JSONResponse(status_code=400, content={"error": "Нет файла и нет пути"})
        
    safe_pdf_path = os.path.join(temp_dir, f"temp_{safe_uid}.pdf")

    scr_path = os.path.join(temp_dir, f"print_{safe_uid}.scr")
    # Скрипт печатает ТОЛЬКО Листы (Paper Space). Функции layoutlist в accoreconsole нет.
    lisp_code = f"""(setvar "FILEDIA" 0) (setvar "CMDDIA" 0) (setvar "PROXYNOTICE" 0) (setvar "EXPERT" 5) (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT")) (while (setq item (assoc 350 dict)) (setq ent (cdr item)) (setq edata (entget ent)) (if (assoc 7 edata) (setq edata (subst (cons 7 "{ctb}") (assoc 7 edata) edata)) (setq edata (append edata (list (cons 7 "{ctb}"))))) (setq flags (cdr (assoc 70 edata))) (if flags (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))) (entmod edata) (setq dict (cdr (member item dict)))) (setvar "TILEMODE" 0) (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace("\\", "/")}") (command "_.QUIT" "_Y")"""
    
    # Записываем скрипт в одну строку в кодировке ANSI для стабильности
    with open(scr_path, "w", encoding="cp1251") as f:
        f.write(lisp_code + "\n")

    # 3. Запускаем AutoCAD Core Console в фоне
    print(f"Печатаем {safe_filename} с помощью {ACAD_PATH} (безопасный путь: {safe_dwg_path})...")
    cmd = [ACAD_PATH, "/i", safe_dwg_path, "/l", "ru-RU", "/s", scr_path]
    
    start_time = time.time()
    try:
        # Убрали shell=True, чтобы процесс не осиротел при таймауте, иначе Python не сможет убить его
        result = subprocess.run(cmd, shell=False, capture_output=True, text=True, errors="ignore", timeout=300)
    except subprocess.TimeoutExpired:
        print(f"ТАЙМАУТ ПЕЧАТИ (300 сек)! Убиваем зависший процесс AutoCAD для файла: {safe_filename}")
        subprocess.run('taskkill /F /IM accoreconsole.exe', shell=True)
        return JSONResponse(status_code=504, content={"error": "AutoCAD timeout 300s. Process killed.", "log": ""})
    
    # Проверяем, не выдал ли AutoCAD ошибку об отсутствии листов
    if "ERROR_NO_LAYOUTS" in result.stdout:
        print(f"ОШИБКА: В чертеже {safe_filename} нет настроенных листов!")
        return JSONResponse(status_code=400, content={"error": "В чертеже нет ни одного листа (Layout).", "log": result.stdout})

    
    # Копируем PDF обратно
    if os.path.exists(safe_pdf_path):
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
        print(result.stdout)
        return JSONResponse(status_code=500, content={"error": "Не удалось создать PDF. Проверьте консоль сервера.", "log": result.stdout})

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
            doc.SaveAs(pdf_path, FileFormat=17) # wdFormatPDF
            doc.Close(SaveChanges=False)
            word.Quit()
            
        elif ext in [".xls", ".xlsx"]:
            print(f"Конвертация Excel: {safe_filename}")
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(in_path, ReadOnly=True)
            wb.ExportAsFixedFormat(0, pdf_path) # xlTypePDF
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
