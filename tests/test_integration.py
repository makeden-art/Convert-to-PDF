import pytest
import sys
import os
import fitz  # PyMuPDF
import tempfile
import shutil
import subprocess

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

@pytest.fixture
def test_files():
    dwg_folder = r"C:\Users\makeden\Documents\dwg_to_pdf"
    mole_dwg = os.path.join(dwg_folder, "2 этап ПК210-ПК240_mole_model.dwg")
    
    temp_dir = tempfile.mkdtemp()
    mole_pdf = os.path.join(temp_dir, "mole_out.pdf")
    
    yield mole_dwg, mole_pdf
    
    try:
        shutil.rmtree(temp_dir)
    except:
        pass

def test_convert_mole_model(test_files):
    mole_dwg, mole_pdf = test_files
    
    if not os.path.exists(mole_dwg):
        pytest.skip(f"Test file not found: {mole_dwg}")
        
    script_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "convert_one.py")
    
    # Run the CLI tool
    result = subprocess.run([sys.executable, script_path, mole_dwg, mole_pdf], capture_output=True, text=True)
    
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    
    assert result.returncode == 0, f"Script failed with code {result.returncode}"
    assert os.path.exists(mole_pdf), "Output PDF was not generated"
    
    doc = fitz.open(mole_pdf)
    page_count = len(doc)
    doc.close()
    
    assert page_count == 6, f"Expected 6 pages, got {page_count}"

