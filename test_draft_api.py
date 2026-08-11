import httpx
import sys

def test_api():
    base_url = "http://localhost:8084"
    
    # 1. Upload files to create a draft
    with open("test1.pdf", "rb") as f1, open("test2.pdf", "rb") as f2:
        files = [
            ("files", ("test1.pdf", f1, "application/pdf")),
            ("files", ("test2.pdf", f2, "application/pdf")),
        ]
        resp = httpx.post(f"{base_url}/api/convert-draft", files=files)
        
    print(f"Draft creation response: {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text)
        sys.exit(1)
        
    data = resp.json()
    draft_id = data.get("draft_id")
    page_count = data.get("page_count")
    print(f"Draft created: {draft_id}, Pages: {page_count}")
    
    # 2. Finalize draft
    # Reverse page order and number them
    payload = {
        "page_order": [2, 1],
        "deleted_pages": [],
        "add_numbering": True
    }
    
    resp2 = httpx.post(f"{base_url}/api/draft/{draft_id}/finalize", json=payload)
    print(f"Finalize response: {resp2.status_code}")
    if resp2.status_code != 200:
        print(resp2.text)
        sys.exit(1)
        
    with open("result.pdf", "wb") as f:
        f.write(resp2.content)
        
    print("Test completed successfully.")

if __name__ == "__main__":
    test_api()
