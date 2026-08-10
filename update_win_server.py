import re

content = open(r'C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\windows_cad_server.py', 'r', encoding='utf-8').read()

new_func = """@app.post("/convert")
async def convert_cad(file: UploadFile = File(...), ctb: str = Form("monochrome.ctb"), dsd_file: UploadFile = File(None)):
    # 1. Сохраняем входящий чертеж
    safe_filename = file.filename.replace(" ", "_")
    dwg_path = os.path.join(WORK_DIR, safe_filename)
    pdf_path = dwg_path.replace(".dwg", ".pdf")
    
    # Удаляем старый PDF, если есть
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    with open(dwg_path, "wb") as buffer:
        import shutil
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Формируем окружение
    import tempfile
    import uuid
    safe_uid = uuid.uuid4().hex
    temp_dir = tempfile.gettempdir()
    
    safe_dwg_path = os.path.join(temp_dir, f"temp_{safe_uid}.dwg")
    safe_pdf_path = os.path.join(temp_dir, f"temp_{safe_uid}.pdf")
    shutil.copy2(dwg_path, safe_dwg_path)
    
    # 3. Обработка DSD файла, если он передан
    scr_path = os.path.join(temp_dir, f"print_{safe_uid}.scr")
    safe_dsd_path = None
    
    if dsd_file and dsd_file.filename:
        safe_dsd_path = os.path.join(temp_dir, f"temp_{safe_uid}.dsd")
        dsd_content = await dsd_file.read()
        try:
            dsd_text = dsd_content.decode('utf-8')
        except UnicodeDecodeError:
            dsd_text = dsd_content.decode('cp1251', errors='ignore')
            
        # Подменяем пути DWG на наш локальный временный файл
        lines = dsd_text.splitlines()
        new_lines = []
        for line in lines:
            if line.upper().startswith("DWG="):
                new_lines.append(f"DWG={safe_dwg_path}")
            else:
                new_lines.append(line)
        
        with open(safe_dsd_path, "w", encoding="utf-8") as f:
            f.write("\\n".join(new_lines))
            
        # Формируем SCRIPT для пакетной печати через PUBLISH
        # Запускаем PUBLISH с файлом DSD
        lisp_code = f'(command "_.-PUBLISH" "{safe_dsd_path.replace("\\\\", "/")}")\\n(command "_.QUIT" "_Y")\\n'
    else:
        # Стандартная логика EXPORT PDF All
        lisp_code = f\"\"\"(setq dict (dictsearch (namedobjdict) "ACAD_LAYOUT"))
(while (setq item (assoc 350 dict))
  (setq ent (cdr item))
  (setq edata (entget ent))
  (if (assoc 7 edata)
    (setq edata (subst (cons 7 "{ctb}") (assoc 7 edata) edata))
    (setq edata (append edata (list (cons 7 "{ctb}"))))
  )
  (setq flags (cdr (assoc 70 edata)))
  (if flags
    (setq edata (subst (cons 70 (logior flags 32)) (assoc 70 edata) edata))
  )
  (entmod edata)
  (setq dict (cdr (member item dict)))
)
(command "_.-EXPORT" "_PDF" "_All" "{safe_pdf_path.replace("\\\\", "/")}")
(command "_.QUIT" "_Y")
\"\"\"

    scr_code = lisp_code.replace("\\n", " ")
    with open(scr_path, "w", encoding="cp1251") as f:
        f.write(lisp_code)

    # 4. Запускаем AutoCAD Core Console
    print(f"Печатаем {safe_filename} с помощью {ACAD_PATH} (безопасный путь: {safe_dwg_path})...")
    cmd = f'"{ACAD_PATH}" /i "{safe_dwg_path}" /l ru-RU /s "{scr_path}"'
    
    start_time = time.time()
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, errors="ignore")
    
    # 5. Копируем PDF обратно
    # Если мы использовали PUBLISH, Автокад мог сохранить PDF рядом с DSD или там, где указано в DSD.
    # Обычно PUBLISH по умолчанию сохраняет в ту же папку, либо в профиль.
    # Если safe_pdf_path не существует, проверим, не создал ли PUBLISH файл с именем чертежа.
    import glob
    if not os.path.exists(safe_pdf_path) and safe_dsd_path:
        # PUBLISH мог создать PDF с именем DSD файла или DWG файла в temp_dir
        possible_pdfs = glob.glob(os.path.join(temp_dir, f"temp_{safe_uid}*.pdf"))
        if possible_pdfs:
            safe_pdf_path = possible_pdfs[0]
            
    if os.path.exists(safe_pdf_path):
        shutil.copy2(safe_pdf_path, pdf_path)
        
    # Убираем за собой
    try:
        os.remove(safe_dwg_path)
        if os.path.exists(safe_pdf_path): os.remove(safe_pdf_path)
        os.remove(scr_path)
        if safe_dsd_path and os.path.exists(safe_dsd_path): os.remove(safe_dsd_path)
    except Exception:
        pass
    
    print(f"Время выполнения: {time.time() - start_time:.1f} сек")
    
    # 6. Возвращаем PDF
    if os.path.exists(pdf_path):
        return FileResponse(path=pdf_path, filename=safe_filename.replace(".dwg", ".pdf"), media_type='application/pdf')
    else:
        print("ОШИБКА ПЕЧАТИ:")
        print(result.stdout)
        return {"error": "Не удалось создать PDF. Проверьте консоль сервера.", "log": result.stdout}
"""

lines = content.splitlines()
start_idx = -1
for i, line in enumerate(lines):
    if line.startswith("@app.post(\"/convert\")"):
        start_idx = i
        break

end_idx = -1
for i in range(start_idx+1, len(lines)):
    if lines[i].startswith("if __name__ == \"__main__\":"):
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    before = lines[:start_idx]
    after = lines[end_idx:]
    new_content = "\\n".join(before) + "\\n" + new_func + "\\n" + "\\n".join(after)
    open(r'C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\windows_cad_server.py', 'w', encoding='utf-8').write(new_content)
    print("Patched windows_cad_server.py")
