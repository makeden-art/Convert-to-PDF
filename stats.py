import json
import os
import threading
from pathlib import Path
from typing import Any

# Нормативы времени на ручную конвертацию 1 файла (в секундах)
TIME_NORMS = {
    "DWG": 180,  # 3 минуты на открытие Автокада, выбор принтера, рамку, печать
    "DOCX": 90,  # 1.5 минуты на Word
    "XLSX": 90,  # 1.5 минуты на Excel
    "PDF": 10,   # 10 секунд на обработку/копирование
    "IMAGE": 30, # 30 секунд на печать картинки
    "OTHER": 30
}

STATS_FILE = Path(os.getenv("CONVERT_JOBS_DIR", "/data/convert-jobs")) / "stats.json"
_stats_lock = threading.Lock()

def _load_stats() -> dict[str, Any]:
    if not STATS_FILE.exists():
        return {"total_files": 0, "saved_seconds": 0, "by_format": {}}
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        if "by_format" not in data:
            data["by_format"] = {}
        if "saved_seconds" not in data:
            data["saved_seconds"] = 0
        return data
    except Exception:
        return {"total_files": 0, "saved_seconds": 0, "by_format": {}}

def _save_stats(data: dict[str, Any]) -> None:
    STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        STATS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def get_stats() -> dict[str, Any]:
    with _stats_lock:
        return _load_stats()

def update_stats_from_result(result: dict[str, Any]) -> None:
    """Обновляет статистику на основе результата конвертации задания."""
    if not isinstance(result, dict):
        return
        
    files = result.get("files", [])
    if not files or not isinstance(files, list):
        return

    with _stats_lock:
        data = _load_stats()
        
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            if file_item.get("status") != "ok":
                continue
                
            src = file_item.get("source", "")
            if not src:
                continue
                
            ext = Path(src).suffix.lstrip(".").upper()
            if not ext:
                ext = "OTHER"
                
            data["by_format"][ext] = data["by_format"].get(ext, 0) + 1
            data["total_files"] = data.get("total_files", 0) + 1
            
            # Подсчет сэкономленного времени
            norm = TIME_NORMS.get(ext, TIME_NORMS["OTHER"])
            data["saved_seconds"] = data.get("saved_seconds", 0) + norm
            
        _save_stats(data)
