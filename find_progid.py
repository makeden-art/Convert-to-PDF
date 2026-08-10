import winreg

k = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "")
i = 0
res = []
while True:
    try:
        name = winreg.EnumKey(k, i)
        i += 1
        if "nanocad" in name.lower() and "application" in name.lower():
            res.append(name)
    except OSError:
        break

print("Found ProgIDs:", res)
