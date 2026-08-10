import sys, os
sys.path.insert(0, '/app')
from converter import convert_file_to_pdf
from pathlib import Path

# find the file
import glob
files = glob.glob('/data/smb/default/dwg/*.doc')
target = None
for f in files:
    if 'Титул' in f and 'Кальмиус' in f:
        target = f
        break

if not target:
    print('File not found')
    sys.exit(1)

src = Path(target)
dest = Path('/tmp/test_word2.pdf')
try:
    convert_file_to_pdf(src, dest, {'timeout': 300})
    print('Success')
except Exception as e:
    import traceback
    traceback.print_exc()
