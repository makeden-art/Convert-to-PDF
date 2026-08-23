import subprocess
with open('temp.scr', 'w') as f:
    f.write('(command "_.QUIT" "_Y")\n')
print('Running AutoCAD...')
try:
    res = subprocess.run(['E:/Autodesk/acad/AutoCAD 2022/accoreconsole.exe', '/i', r'E:\share_test\01ГЧ1\11-2025-1-АД.1.1-02_План.dwg', '/s', 'temp.scr'], capture_output=True, timeout=20)
    print(res.stdout.decode('cp1251', errors='replace'))
except subprocess.TimeoutExpired:
    print('Timed out!')
