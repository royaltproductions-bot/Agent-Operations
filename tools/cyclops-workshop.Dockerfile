FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg imagemagick graphviz librsvg2-bin nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    pillow reportlab openpyxl python-docx python-pptx \
    matplotlib cairosvg svgwrite pypdf fpdf2
WORKDIR /work
USER 65534:65534
