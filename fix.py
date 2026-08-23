import re

with open('convert_page.html', 'r', encoding='utf-8') as f:
    html = f.read()

pattern = r'(<div class="chk"><input type="checkbox" id="smart-search" /><label for="smart-search" style="margin:0">[^<]+</label></div>)'
replacement = r'\1\n          <p class="hint" style="margin-top: -6px; margin-bottom: 10px; margin-left: 24px; color: #94a3b8; font-size: 13px;">Если в имени файла есть <b>_модель</b> или <b>_model</b>, листы (Layouts) игнорируются, печать рамок производится из пространства Модели.</p>'
html = re.sub(pattern, replacement, html)

with open('convert_page.html', 'w', encoding='utf-8') as f:
    f.write(html)
