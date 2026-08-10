import sys
import tempfile
from pathlib import Path
sys.path.insert(0, '/app')
from converter import convert_file_to_pdf_isolated

src = Path(sys.argv[1])
dest = Path(tempfile.mkdtemp()) / 'out.pdf'

try:
    convert_file_to_pdf_isolated(src, dest)
    print('SUCCESS')
except Exception as e:
    print('ISOLATED FAILED WITH:')
    print(str(e))
