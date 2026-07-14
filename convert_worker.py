"""Отдельный процесс для конвертации одного файла (OOM не убивает uvicorn)."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: convert_worker.py <src> <dest.pdf> [color_mode]", file=sys.stderr)
        sys.exit(2)
    src = Path(sys.argv[1])
    dest = Path(sys.argv[2])
    color_mode = sys.argv[3] if len(sys.argv) > 3 else "color"
    from converter import convert_file_to_pdf

    convert_file_to_pdf(src, dest, color_mode=color_mode)
    if not dest.is_file():
        print("PDF не создан", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        print(traceback.format_exc(), file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)
