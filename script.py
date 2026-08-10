import json

with open("/opt/road-pdf-platform/portal_design/portal/app.py") as f:
    lines = f.readlines()

for i, l in enumerate(lines):
    if "def mount_smb(" in l:
        print("".join(lines[i:i+60]))
        break
