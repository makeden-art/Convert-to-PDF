"""
Автоматический тестовый стенд для проверки критериев конвертации DWG -> PDF.
Запуск: python run_cad_tests.py
"""
import os
import sys
import time
import requests
import fitz  # PyMuPDF

CAD_SERVER_URL = os.environ.get("CAD_SERVER_URL", "http://127.0.0.1:8000/convert")
STATUS_URL = os.environ.get("CAD_STATUS_URL", "http://127.0.0.1:8000/status")

TEST_CASES = [
    {
        "name": "Листы (Layouts)",
        "file": r"c:\Users\makeden\Documents\югдорпроект_конвертер_PDF\Несколько_рамок_в_принудительном_порядке_в_листе.dwg",
        "expected_mode": "Layouts",
        "min_pages": 1,
        "max_time_sec": 180,
        "description": "Проверка корректной печати всех листов (Layouts) без модели"
    },
    {
        "name": "Рамки в Модели (_model)",
        "file": r"c:\Users\makeden\Documents\dwg_to_pdf\2 этап ПК210-ПК240_mole_model.dwg",
        "expected_mode": "Model (Smart Search)",
        "min_pages": 4,
        "max_time_sec": 180,
        "description": "Автопоиск и нарезка нескольких рамок в пространстве модели"
    },
    {
        "name": "Модель один слой",
        "file": r"c:\Users\makeden\Documents\югдорпроект_конвертер_PDF\Несколько_рамок_в_модели_в_одном_слое_штамп_рамка.dwg",
        "expected_mode": "Model (Smart Search)",
        "min_pages": 1,
        "max_time_sec": 180,
        "description": "Поиск рамок по слоям рамка/штамп"
    },
    {
        "name": "2 этап ПК240-ПК270 Модель",
        "file": r"C:\Users\makeden\Documents\dwg_to_pdf\2 этап ПК240-ПК270_14.08_model.dwg",
        "expected_mode": "Model (Smart Search)",
        "min_pages": 4,
        "max_time_sec": 1800,
        "description": "Поиск и нарезка 171 рамки в пространстве модели (ПК240-ПК270)"
    },
    {
        "name": "2 этап ПК240-ПК270 v2smole",
        "file": r"\\192.168.88.22\cadshare\2 этап ПК240-ПК270_14.08_model_v2smole.dwg",
        "smb_path": r"\\192.168.88.22\cadshare\2 этап ПК240-ПК270_14.08_model_v2smole.dwg",
        "expected_mode": "Model (Smart Search)",
        "min_pages": 2,
        "max_time_sec": 180,
        "description": "Файл с 2 заполненными листами (A3 + A3x2) и пустыми шаблонами"
    },
    {
        "name": "01ГЧ1 План + 17 Xref",
        "file": r"\\192.168.88.22\cadshare\01ГЧ1\11-2025-1-АД.1.1-02_План.dwg",
        "smb_path": r"\\192.168.88.22\cadshare\01ГЧ1\11-2025-1-АД.1.1-02_План.dwg",
        "expected_mode": "Layouts (12 листов + Xref)",
        "min_pages": 10,
        "max_time_sec": 3600,
        "description": "Тяжелый проект: 12 листов, 17 внешних ссылок (SMB-шара 192.168.88.22)"
    }
]

def check_server():
    try:
        r = requests.get(STATUS_URL, timeout=5)
        if r.status_code == 200:
            return True, r.json()
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

def run_conversion_test(test_case):
    filepath = test_case["file"]
    if not os.path.exists(filepath):
        return {
            "name": test_case["name"],
            "status": "SKIPPED",
            "reason": f"Файл не найден: {filepath}",
            "pages": 0,
            "duration": 0
        }

    filename = os.path.basename(filepath)
    is_model = ("_model" in filename.lower() or "модель" in filename.lower())
    smb_path = test_case.get("smb_path")
    
    start_t = time.time()
    try:
        data = {"ctb": "monochrome.ctb"}
        if is_model:
            data["smart_search"] = "true"
        
        files = None
        if smb_path:
            # Передаем как прямой путь к сетевой шаре, сервер сам сделает подключение и ускорение на SSD
            data["smb_dwg_path"] = smb_path
            data["smb_user"] = "caduser"
            data["smb_pass"] = "smbpasswd"
            resp = requests.post(CAD_SERVER_URL, data=data, timeout=test_case.get("max_time_sec", 3600))
        else:
            with open(filepath, "rb") as f:
                files = {"file": (filename, f, "application/octet-stream")}
                resp = requests.post(CAD_SERVER_URL, files=files, data=data, timeout=test_case.get("max_time_sec", 180))
        
        duration = round(time.time() - start_t, 2)
        
        if resp.status_code != 200:
            return {
                "name": test_case["name"],
                "status": "FAIL",
                "reason": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "pages": 0,
                "duration": duration
            }

        pdf_bytes = resp.content
        if len(pdf_bytes) < 1000:
            return {
                "name": test_case["name"],
                "status": "FAIL",
                "reason": f"Слишком малый размер PDF ({len(pdf_bytes)} байт)",
                "pages": 0,
                "duration": duration
            }

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_count = len(doc)
        
        empty_pages = 0
        for i in range(page_count):
            page = doc[i]
            drawings = page.get_drawings()
            text = page.get_text().strip()
            if not drawings and not text:
                empty_pages += 1

        doc.close()

        if empty_pages > 0:
            return {
                "name": test_case["name"],
                "status": "FAIL",
                "reason": f"Обнаружено {empty_pages} абсолютно пустых страниц!",
                "pages": page_count,
                "duration": duration
            }

        if page_count < test_case.get("min_pages", 1):
            return {
                "name": test_case["name"],
                "status": "FAIL",
                "reason": f"Ожидалось >= {test_case['min_pages']} страниц, получено: {page_count}",
                "pages": page_count,
                "duration": duration
            }

        return {
            "name": test_case["name"],
            "status": "PASS",
            "reason": f"Размер {round(len(pdf_bytes)/1024, 1)} KB",
            "pages": page_count,
            "duration": duration
        }

    except requests.exceptions.Timeout:
        duration = round(time.time() - start_t, 2)
        return {
            "name": test_case["name"],
            "status": "FAIL",
            "reason": f"Таймаут (превышен лимит {test_case['max_time_sec']}s)",
            "pages": 0,
            "duration": duration
        }
    except Exception as e:
        duration = round(time.time() - start_t, 2)
        return {
            "name": test_case["name"],
            "status": "FAIL",
            "reason": str(e),
            "pages": 0,
            "duration": duration
        }

def main():
    target_idx = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        target_idx = int(sys.argv[1])

    print("=" * 80)
    print(" АВТОМАТИЧЕСКИЙ ТЕСТ КРИТЕРИЕВ КОНВЕРТАЦИИ DWG -> PDF")
    print("=" * 80)

    ok, info = check_server()
    if not ok:
        print(f"[!] CAD Server недоступен ({STATUS_URL}): {info}")
        sys.exit(1)
    
    print(f"[*] CAD Server доступен. AutoCAD: {info.get('autocad', {}).get('available')}")
    cases_to_run = [TEST_CASES[target_idx]] if target_idx is not None else TEST_CASES
    print(f"[*] Запуск {len(cases_to_run)} проверочных тестов...\n")

    results = []
    for tc in cases_to_run:
        print(f"-> Тестирование: {tc['name']} ({tc['description']})...", end="", flush=True)
        res = run_conversion_test(tc)
        results.append(res)
        print(f" [{res['status']}] ({res['duration']}s)")

    print("\n" + "=" * 80)
    print(f"{'Тест':<28} | {'Статус':<8} | {'Листов':<7} | {'Время':<8} | {'Результат'}")
    print("-" * 80)
    all_ok = True
    for r in results:
        status_str = f"PASS" if r['status'] == "PASS" else r['status']
        if r['status'] != "PASS" and r['status'] != "SKIPPED":
            all_ok = False
        print(f"{r['name']:<28} | {status_str:<8} | {r['pages']:<7} | {str(r['duration'])+'s':<8} | {r['reason']}")
    print("=" * 80)

    if all_ok:
        print("\n ВСЕ КРИТЕРИИ УСПЕШНО ПРОЙДЕНЫ!")
    else:
        print("\n ЕСТЬ ОШИБКИ ПРИ ПРОВЕРКЕ КРИТЕРИЕВ.")

if __name__ == "__main__":
    main()
