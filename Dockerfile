FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    xvfb xauth \
    libxcb-util1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
    libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 \
    libfontconfig1 libfreetype6 libgl1 libglib2.0-0 \
    fonts-dejavu-core \
    curl ca-certificates smbclient \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/lib/x86_64-linux-gnu/libxcb-util.so.1 /usr/lib/x86_64-linux-gnu/libxcb-util.so.0 || true

# ODA для DWG→DXF (deb кладётся в контекст сборки CI или platform root)
COPY ODAFileConverter*.deb /tmp/oda.deb
RUN dpkg -i /tmp/oda.deb || (apt-get update && apt-get install -f -y) && rm -f /tmp/oda.deb

COPY app.py converter.py cad_converter.py format_detect.py convert_page.html VERSION ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
