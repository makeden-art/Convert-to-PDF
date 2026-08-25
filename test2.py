import re

with open('windows_cad_server.py', 'r', encoding='utf-8') as f:
    text = f.read()

start_marker = "lisp_code = _generate_lisp_script(safe_pdf_path, is_smart, ctb)"
end_marker = "if not os.path.exists(safe_pdf_path):"

start_idx = text.find(start_marker)
end_idx = text.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print('Failed to find bounds')
    sys.exit(1)
    
print('Found bounds:', start_idx, end_idx)
