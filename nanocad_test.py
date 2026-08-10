import sys
import os
import win32com.client
import time

def test_nanocad(dwg_path):
    print("Starting nanoCAD COM API test...")
    try:
        # Connect to NanoCAD (or AutoCAD)
        # NanoCAD's ProgID is usually "nanoCAD.Application"
        # AutoCAD's ProgID is "AutoCAD.Application"
        try:
            cad = win32com.client.Dispatch("nanoCAD.Application")
            print("Connected to nanoCAD")
        except Exception as e:
            print("Failed to dispatch nanoCAD:", e)
            cad = win32com.client.Dispatch("AutoCAD.Application")
            print("Connected to AutoCAD")

        cad.Visible = False

        print(f"Opening {dwg_path}...")
        doc = cad.Documents.Open(dwg_path)
        print("Opened successfully.")

        # List layouts
        for i in range(doc.Layouts.Count):
            layout = doc.Layouts.Item(i)
            print(f"Layout {i}: {layout.Name}")

        # Let's plot the active layout to PDF
        doc.ActiveLayout.ConfigName = "DWG To PDF.pc3"
        print("ConfigName set")

        pdf_path = os.path.splitext(dwg_path)[0] + "_com_test.pdf"
        # In AutoCAD, PlotToFile takes the plot file name
        doc.Plot.PlotToFile(pdf_path)
        print(f"Plotted to {pdf_path}")

        doc.Close(False)
        print("Closed document.")
        cad.Quit()
        print("Quit CAD.")
    except Exception as e:
        print("Error:", e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_nanocad(sys.argv[1])
