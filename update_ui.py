import sys
import re

html_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\convert_page.html"
app_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\app.py"
converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\converter.py"
cad_converter_path = r"C:\Users\makeden\Documents\югдорпроект_конвертер_PDF\cad_converter.py"

html = open(html_path, 'r', encoding='utf-8').read()

js_addition = """
    /* Windows Server setup */
    const winToggle = document.getElementById('win-toggle');
    const winBody = document.getElementById('win-body');
    const winIpInput = document.getElementById('win-ip');
    const btnWinCheck = document.getElementById('btn-win-check');
    const winStatus = document.getElementById('win-status');

    winToggle.onclick = () => {
      const c = winBody.classList.toggle('is-collapsed');
      winToggle.setAttribute('aria-expanded', c ? 'false' : 'true');
    };

    let savedWinIp = localStorage.getItem('win_cad_ip') || '';
    if (savedWinIp) winIpInput.value = savedWinIp;

    winIpInput.addEventListener('change', () => {
        localStorage.setItem('win_cad_ip', winIpInput.value.trim());
    });

    btnWinCheck.onclick = async () => {
        const ip = winIpInput.value.trim();
        if (!ip) {
            winStatus.textContent = 'Статус: введите IP-адрес';
            return;
        }
        localStorage.setItem('win_cad_ip', ip);
        btnWinCheck.disabled = true;
        winStatus.textContent = 'Статус: проверка...';
        try {
            // ping through our backend because browser CORS might block direct access
            const r = await fetch('/api/cad-server-ping?ip=' + encodeURIComponent(ip));
            if (r.ok) {
                winStatus.textContent = 'Статус сервера: 🟢 Доступен';
                winStatus.style.color = '#4ade80';
            } else {
                winStatus.textContent = 'Статус сервера: 🔴 Недоступен (' + r.status + ')';
                winStatus.style.color = '#f87171';
            }
        } catch (e) {
            winStatus.textContent = 'Статус сервера: 🔴 Ошибка проверки';
            winStatus.style.color = '#f87171';
        }
        btnWinCheck.disabled = false;
    };
"""

# Insert JS logic
html = html.replace("/* SMB */", js_addition + "\n    /* SMB */")

# Add windows_cad_ip to payloads
html = html.replace("recursive, color_mode, merge", "recursive, windows_cad_ip: winIpInput.value.trim(), merge")
html = html.replace("recursive, merge", "recursive, windows_cad_ip: winIpInput.value.trim(), merge")
html = html.replace("fd.append('recursive', recursive);", "fd.append('recursive', recursive);\n      fd.append('windows_cad_ip', winIpInput.value.trim());")

open(html_path, 'w', encoding='utf-8').write(html)

app = open(app_path, 'r', encoding='utf-8').read()
app = app.replace("class PathsRequest(BaseModel):", "class PathsRequest(BaseModel):\n    windows_cad_ip: str = \"\"\n")
app = app.replace("class FolderRequest(BaseModel):", "class FolderRequest(BaseModel):\n    windows_cad_ip: str = \"\"\n")
app = app.replace("color_mode=body.color_mode,", "windows_cad_ip=body.windows_cad_ip,")
app = app.replace("color_mode=\"color\",", "windows_cad_ip=body.windows_cad_ip,")

# Add ping endpoint
ping_endpoint = """
@app.get("/api/cad-server-ping")
async def ping_cad_server(ip: str):
    import httpx
    if not ip:
        raise HTTPException(status_code=400, detail="No IP")
    if not ip.startswith("http"):
        ip = "http://" + ip
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(ip)
            return {"status": "ok", "code": resp.status_code}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/cad-server-script")
async def download_cad_server():
    return FileResponse("windows_cad_server.py", media_type="text/x-python", filename="windows_cad_server.py")
"""
app = app + "\n" + ping_endpoint
open(app_path, 'w', encoding='utf-8').write(app)

print("HTML and app.py updated successfully!")
