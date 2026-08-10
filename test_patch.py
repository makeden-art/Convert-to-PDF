def _patch_filter_vp_entities():
    import ezdxf.addons.drawing.pipeline as pipeline
    
    # Store original to avoid double-patching
    if hasattr(pipeline, '_orig_filter_vp_entities'):
        return
    pipeline._orig_filter_vp_entities = pipeline.filter_vp_entities
    
    def _fast_filter_vp_entities(msp, limits, bbox_cache=None):
        min_x, min_y, max_x, max_y = limits
        
        for e in msp:
            dxftype = e.dxftype()
            try:
                if dxftype == "LINE":
                    s, en = e.dxf.start, e.dxf.end
                    exmax = s.x if s.x > en.x else en.x
                    exmin = s.x if s.x < en.x else en.x
                    eymax = s.y if s.y > en.y else en.y
                    eymin = s.y if s.y < en.y else en.y
                    if exmax < min_x or exmin > max_x or eymax < min_y or eymin > max_y:
                        continue
                elif dxftype == "CIRCLE":
                    c, r = e.dxf.center, e.dxf.radius
                    if (c.x + r) < min_x or (c.x - r) > max_x or (c.y + r) < min_y or (c.y - r) > max_y:
                        continue
                elif dxftype == "LWPOLYLINE":
                    # LWPOLYLINE points are (x, y, [start_width, [end_width, [bulge]]])
                    # so p[0] is x, p[1] is y
                    points = e.get_points(format="xy")
                    if points:
                        xs = [p[0] for p in points]
                        ys = [p[1] for p in points]
                        exmax, exmin = max(xs), min(xs)
                        eymax, eymin = max(ys), min(ys)
                        if exmax < min_x or exmin > max_x or eymax < min_y or eymin > max_y:
                            continue
                elif dxftype == "POINT":
                    p = e.dxf.location
                    if p.x < min_x or p.x > max_x or p.y < min_y or p.y > max_y:
                        continue
            except Exception:
                pass
            
            # For HATCH, SPLINE, INSERT, ARC, TEXT, MTEXT, we just let them pass
            # Matplotlib's internal clipping will handle them.
            yield e

    pipeline.filter_vp_entities = _fast_filter_vp_entities

