FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp XDG_CACHE_HOME=/tmp/.cache MPLCONFIGDIR=/tmp/mpl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg imagemagick graphviz librsvg2-bin nodejs fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    pillow reportlab openpyxl python-docx python-pptx \
    matplotlib cairosvg svgwrite pypdf fpdf2 requests
WORKDIR /work
USER 65534:65534
