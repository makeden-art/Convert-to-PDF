import os
print(os.path.exists('/data/smb/default/dwg/11-2025-1-ТКР9-1 Кальмиус варианты.pdf'))
print(os.listdir('/data/smb/default/dwg') if os.path.exists('/data/smb/default/dwg') else 'No dwg dir')
