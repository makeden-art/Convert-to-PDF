from fastapi import FastAPI, Form, File, UploadFile
from fastapi.testclient import TestClient

app = FastAPI()

@app.post('/convert')
def convert_cad(file: UploadFile = File(None), smb_dwg_path: str = Form(None)):
    return {'smb_dwg_path': smb_dwg_path, 'file': file.filename if file else None}

client = TestClient(app)

print(client.post('/convert', data={'smb_dwg_path': r'\\192.168.88.14\share\test.dwg'}).json())
