import os
import subprocess
import time
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn
import shutil
import glob

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
    return r"C:\Program Files\Autodesk\AutoCAD 2022\accoreconsole.exe"

ACAD_PATH = find_accoreconsole()
WORK_DIR = os.path.abspath("cad_server_workdir")
os.makedirs(WORK_DIR, exist_ok=True)

@app.post("/convert")
async def convert_cad(file: UploadFile = File(...), ctb: str = Form("monochrome.ctb")):
    # 1. Сохраняем входящий чертеж
    safe_filename = file.filename.replace(" ", "_")
    dwg_path = os.path.join(WORK_DIR, safe_filename)
    pdf_path = dwg_path.replace(".dwg", ".pdf")
    
    # Удаляем старый PDF, если есть
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    with open(dwg_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Формируем SCRIPT для надежной печати (одной строкой для обхода защиты AutoCAD)
    import tempfile
    import uuid
    safe_uid = uuid.uuid4().hex
    temp_dir = tempfile.gettempdir()
    
    # Копируем DWG во временную папку с безопасным ASCII именем
    safe_dwg_path = os.path.join(temp_dir, f"temp_{safe_uid}.dwg")
    safe_pdf_path = os.path.join(temp_dir, f"temp_{safe_uid}.pdf")
    shutil.copy2(dwg_path, safe_dwg_path)

    scr_path = os.path.join(temp_dir, f"print_{safe_uid}.scr")
    # Тот самый старый стабильный скрипт с EXPORT PDF, но с добавлением EXPERT 5
    # и без команды QUIT (AutoCAD сам закрывается при окончании скрипта, если он в одну строку)
    lisp_code = f"""(setvar "FILEDIA" 0) (setvar "CMDDIA" 0) (setvar "PROXYNOTICE" 0) (setvar "EXPERT" 5) (setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT")) (while (setq item (assoc 350 dict)) (setq ent (cdr item)) (setq edata (entget ent)) (if (assoc 7 edata) (setq edata (subst (cons 7 "{ctb}") (assoc 7 edata) edata)) (setq edata (append edata (list (cons 7 "{ctb}"))))) (setq flags (cdr (assoc 70 edata))) (if flags (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))) (entmod edata) (setq dict (cdr (member item dict)))) (command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace("\\", "/")}")"""
    
    # Записываем скрипт в одну строку в кодировке ANSI для стабильности
    with open(scr_path, "w", encoding="cp1251") as f:
        f.write(lisp_code + "\n")

    # 3. Запускаем AutoCAD Core Console в фоне
    print(f"Печатаем {safe_filename} с помощью {ACAD_PATH} (безопасный путь: {safe_dwg_path})...")
    cmd = f'"{ACAD_PATH}" /i "{safe_dwg_path}" /l ru-RU /s "{scr_path}"'
    
    start_time = time.time()
    try:
        # Добавили таймаут как в новой версии
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore", timeout=180)
    except subprocess.TimeoutExpired:
        subprocess.run('taskkill /F /IM accoreconsole.exe', shell=True)
        return JSONResponse(status_code=504, content={"error": "AutoCAD timeout 180s. Process killed.", "log": ""})
    
    # Копируем PDF обратно
    if os.path.exists(safe_pdf_path):
        shutil.copy2(safe_pdf_path, pdf_path)
        
    # Убираем за собой
    try:
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

if __name__ == "__main__":
    print("--------------------------------------------------")
    print(" Сервер AutoCAD Core Console запущен! (Стабильная версия)")
    print(" Ожидание чертежей на порту 8000...")
    print("--------------------------------------------------")
    uvicorn.run(app, host="0.0.0.0", port=8000)
