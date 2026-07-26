"""
Convierte Reporte - KARD.md a HTML con imagenes embebidas en base64.
Luego abre el HTML en el navegador para imprimir como PDF con Ctrl+P.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import base64, re, os, webbrowser, sys

DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
MD_FILE  = os.path.join(DOCS_DIR, "Reporte - KARD.md")
OUT_FILE = os.path.join(DOCS_DIR, "Reporte - KARD - PRINT.html")

# ── 1. Leer el markdown ──────────────────────────────────────────────────────
with open(MD_FILE, encoding="utf-8") as f:
    md_text = f.read()

# ── 2. Convertir a HTML (con la lib markdown de Python) ─────────────────────
try:
    import markdown
    body_html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "codehilite"]
    )
except ImportError:
    # Fallback manual muy simple si markdown no está instalado
    body_html = "<pre>" + md_text.replace("&","&amp;").replace("<","&lt;") + "</pre>"
    print("⚠ Instala 'pip install markdown' para mejor calidad.")

# ── 3. Embeber imágenes locales como base64 ──────────────────────────────────
def embed_image(match):
    src = match.group(1)
    img_path = os.path.join(DOCS_DIR, src.lstrip("./"))
    if os.path.exists(img_path):
        with open(img_path, "rb") as img:
            b64 = base64.b64encode(img.read()).decode()
        print(f"  OK embebida: {os.path.basename(img_path)}")
        return f'src="data:image/png;base64,{b64}"'
    else:
        print(f"  WARN no encontrada: {img_path}")
        return match.group(0)

body_html = re.sub(r'src="([^"]+\.png)"', embed_image, body_html)

# ── 4. Armar HTML completo con estilos de impresión ─────────────────────────
html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"/>
<title>KARD — Reporte</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono&display=swap');

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Inter', Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.65;
    color: #1a1a2e;
    background: #fff;
    padding: 40px 60px;
    max-width: 900px;
    margin: 0 auto;
  }}

  h1 {{ font-size: 22pt; font-weight: 800; margin: 0 0 12px; color: #1e1b4b; border-bottom: 3px solid #6366f1; padding-bottom: 10px; }}
  h2 {{ font-size: 15pt; font-weight: 700; margin: 32px 0 10px; color: #312e81; border-left: 4px solid #6366f1; padding-left: 10px; }}
  h3 {{ font-size: 11.5pt; font-weight: 600; margin: 20px 0 8px; color: #4338ca; }}
  p  {{ margin: 8px 0; }}

  table {{
    width: 100%; border-collapse: collapse;
    margin: 12px 0; font-size: 9.5pt;
  }}
  th {{
    background: #eef2ff; color: #3730a3;
    font-weight: 700; font-size: 8.5pt;
    text-transform: uppercase; letter-spacing: 0.4px;
    padding: 7px 10px; border: 1px solid #c7d2fe; text-align: left;
  }}
  td {{ padding: 6px 10px; border: 1px solid #e0e7ff; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #f5f3ff; }}

  img {{ max-width: 100%; height: auto; display: block; margin: 16px auto; border: 1px solid #e0e7ff; border-radius: 6px; }}

  pre, code {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 8.5pt; background: #1e1b4b;
    color: #e2e8f0; border-radius: 6px;
  }}
  pre {{ padding: 14px 16px; margin: 12px 0; overflow-x: auto; }}
  code {{ padding: 1px 5px; }}
  pre code {{ background: none; padding: 0; color: inherit; }}

  hr {{ border: none; border-top: 1.5px solid #e0e7ff; margin: 28px 0; }}

  strong {{ color: #312e81; }}
  blockquote {{ border-left: 3px solid #6366f1; padding-left: 12px; color: #4338ca; margin: 10px 0; }}

  @media print {{
    body {{ padding: 20px 30px; font-size: 9.5pt; }}
    h1 {{ font-size: 18pt; }}
    h2 {{ font-size: 13pt; page-break-after: avoid; }}
    h3 {{ page-break-after: avoid; }}
    img {{ max-width: 100%; page-break-inside: avoid; }}
    pre {{ page-break-inside: avoid; font-size: 7.5pt; }}
    table {{ page-break-inside: avoid; font-size: 8.5pt; }}
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

# ── 5. Guardar y abrir ───────────────────────────────────────────────────────
with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nOK Archivo generado: {OUT_FILE}")
print("Abrir en navegador -> Ctrl+P -> Guardar como PDF")
webbrowser.open(f"file:///{OUT_FILE.replace(chr(92), '/')}")
