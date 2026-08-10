with open('app.py', 'a', encoding='utf-8') as f:
    f.write('''

@app.get("/editor")
async def get_editor_page(request: Request):
    template_path = Path(__file__).parent / "editor_page.html"
    if not template_path.exists():
        return HTMLResponse("<h1>Editor page not found</h1>", status_code=404)
    with open(template_path, encoding="utf-8") as ft:
        html = ft.read()
    from app import _version
    html = html.replace("{{VERSION}}", _version())
    return HTMLResponse(html)

class DraftFinalizeRequest(BaseModel):
    page_order: list[int]
    deleted_pages: list[int] = Field(default_factory=list)
    number_pages: bool = False
    numbering_from_page: int | None = None
    numbering_start: int | None = None

DRAFTS_DIR = Path(os.getenv("CONVERT_JOBS_DIR", "/data/convert-jobs")) / "drafts"

@app.post("/api/convert-draft")
async def api_convert_draft(body: PathsRequest):
    if not body.paths:
        raise HTTPException(status_code=400, detail="Укажите файлы для сборки")
    
    draft_id = str(uuid.uuid4())
    DRAFTS_DIR.mkdir(exist_ok=True, parents=True)
    draft_dir = DRAFTS_DIR / draft_id
    draft_dir.mkdir(exist_ok=True)
    
    tmp_parent = None
    try:
        dest, tmp_parent, _ = await asyncio.to_thread(
            convert_paths_merged_download,
            body.paths,
            "draft.pdf",
            recursive=body.recursive,
            numbering_from_page=None,
            numbering_start=None,
            windows_cad_ip=body.windows_cad_ip,
        )
        draft_pdf = draft_dir / "draft.pdf"
        shutil.copy2(dest, draft_pdf)
        
        # Get page count
        import fitz
        doc = fitz.open(str(draft_pdf))
        page_count = len(doc)
        doc.close()
        
        return {"draft_id": draft_id, "page_count": page_count}
    except Exception as e:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        if tmp_parent:
            shutil.rmtree(tmp_parent, ignore_errors=True)

@app.get("/api/draft/{draft_id}/pages")
async def api_draft_pages(draft_id: str):
    draft_pdf = DRAFTS_DIR / draft_id / "draft.pdf"
    if not draft_pdf.exists():
        raise HTTPException(status_code=404, detail="Черновик не найден")
    import fitz
    doc = fitz.open(str(draft_pdf))
    page_count = len(doc)
    doc.close()
    return {"draft_id": draft_id, "page_count": page_count}

@app.post("/api/draft/{draft_id}/finalize")
async def api_draft_finalize(draft_id: str, body: DraftFinalizeRequest):
    draft_pdf = DRAFTS_DIR / draft_id / "draft.pdf"
    if not draft_pdf.exists():
        raise HTTPException(status_code=404, detail="Черновик не найден")
        
    final_pdf = DRAFTS_DIR / draft_id / "final.pdf"
    
    def _finalize():
        import fitz
        from converter import _apply_pdf_numbering
        doc = fitz.open(str(draft_pdf))
        
        new_doc = fitz.open()
        for idx in body.page_order:
            if idx - 1 < len(doc) and idx not in body.deleted_pages:
                new_doc.insert_pdf(doc, from_page=idx-1, to_page=idx-1)
                
        new_doc.save(str(final_pdf), garbage=4, deflate=True)
        new_doc.close()
        doc.close()
        
        if body.number_pages:
            _apply_pdf_numbering(final_pdf, from_page=body.numbering_from_page or 1, start=body.numbering_start or 1)
            
    try:
        await asyncio.to_thread(_finalize)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    
    return FileResponse(
        path=str(final_pdf),
        media_type="application/pdf",
        filename="сборка.pdf",
    )
''')
