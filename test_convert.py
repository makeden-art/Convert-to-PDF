import sys
from cad_converter import convert_cad_to_pdf
if __name__ == "__main__":
    pdf = convert_cad_to_pdf(sys.argv[1])
    print("DONE:", pdf)
