import sys
import time
sys.path.insert(0, '/app')
from cad_converter import convert_cad_to_pdf

start_time = time.time()
print(f"Starting rendering at {time.strftime('%H:%M:%S')}")

input_dxf = "/tmp/cad_pdf_6lpwizs2/11-2025-1-ТКР9-4 Общий вид опор (ОК6).dxf"
output_pdf = "/tmp/ok6_test_result.pdf"

try:
    convert_cad_to_pdf(input_dxf, output_pdf)
    elapsed = time.time() - start_time
    print(f"SUCCESS! Rendering finished in {elapsed:.2f} seconds.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"FAILED! Error: {e}")
