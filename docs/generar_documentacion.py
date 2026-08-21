"""
Genera la version imprimible de Documentacion_KARD.md.

Produce un HTML autocontenido y lo abre en el navegador; desde ahi se guarda
como PDF con Ctrl+P. Mismo enfoque que md_to_pdf.py, que hace lo propio con
"Reporte - KARD.md".

    cd docs
    python generar_documentacion.py

IMPORTANTE: ejecutar desde docs/, no desde la raiz del repo. En la raiz hay un
directorio 'logging/' (resto de un modulo renombrado a audit_logging/) que tapa
el modulo estandar del mismo nombre y rompe el import de markdown.
"""
import base64
import io
import os
import re
import sys
import webbrowser
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
MD_FILE = os.path.join(DOCS_DIR, "Documentacion_KARD.md")
OUT_FILE = os.path.join(DOCS_DIR, "Documentacion_KARD - PRINT.html")

TITULO = "KARD — Documentación técnica"
INTEGRANTES = ["Sánchez Mendoza Octavio", "Díaz Santiago Brayan Adonai"]

# ── 1. Leer el markdown ──────────────────────────────────────────────
with open(MD_FILE, encoding="utf-8") as f:
    md_text = f.read()

# El encabezado del .md se reemplaza por la portada, para no duplicarlo.
md_text = re.sub(r"\A#\s+.*?(?=\n## )", "", md_text, flags=re.S)

# ── 2. Markdown -> HTML ──────────────────────────────────────────────
try:
    import markdown
except ImportError:
    sys.exit("Falta la libreria: pip install markdown")

body_html = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "toc", "sane_lists"],
)

# ── 3. Embeber imagenes locales en base64 ────────────────────────────
def embed_image(match):
    src = match.group(1)
    path = os.path.join(DOCS_DIR, src.lstrip("./"))
    if os.path.exists(path):
        with open(path, "rb") as img:
            b64 = base64.b64encode(img.read()).decode()
        print(f"  embebida: {os.path.basename(path)}")
        return f'src="data:image/png;base64,{b64}"'
    print(f"  NO encontrada: {src}")
    return match.group(0)


body_html = re.sub(r'src="([^"]+)"', embed_image, body_html)

# Los enlaces a otros archivos del repo no sirven dentro del PDF: se dejan
# como texto para que el documento no prometa navegacion que no existe.
body_html = re.sub(
    r'<a href="(?!https?://|#)[^"]*">(.*?)</a>',
    r"<span class=\"ref\">\1</span>",
    body_html,
    flags=re.S,
)

hoy = date.today().strftime("%d/%m/%Y")
integrantes_html = "".join(f"<li>{n}</li>" for n in INTEGRANTES)

# ── 4. Documento ─────────────────────────────────────────────────────
html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{TITULO}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}

  body {{
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    font-size: 10.5pt; line-height: 1.6; color: #1a1d24;
    margin: 0 auto; max-width: 190mm; padding: 0 6mm; background: #fff;
  }}

  /* Portada */
  .cover {{ page-break-after: always; padding-top: 55mm; text-align: center; }}
  .cover-kicker {{
    font-size: 9pt; letter-spacing: .25em; text-transform: uppercase;
    color: #6b7280; margin-bottom: 14mm;
  }}
  .cover h1 {{
    font-size: 30pt; font-weight: 700; letter-spacing: -.02em;
    margin: 0 0 6mm; border: none; padding: 0;
  }}
  .cover-sub {{ font-size: 12pt; color: #4b5563; margin-bottom: 22mm; }}
  .cover-team {{
    display: inline-block; text-align: left;
    border-top: 1px solid #d1d5db; border-bottom: 1px solid #d1d5db;
    padding: 6mm 12mm; margin-bottom: 20mm;
  }}
  .cover-team-label {{
    font-size: 8.5pt; letter-spacing: .18em; text-transform: uppercase;
    color: #6b7280; margin-bottom: 3mm;
  }}
  .cover-team ul {{ list-style: none; margin: 0; padding: 0; }}
  .cover-team li {{ font-size: 12.5pt; font-weight: 600; padding: 1.2mm 0; }}
  .cover-meta {{ font-size: 9.5pt; color: #6b7280; line-height: 1.9; }}

  /* Jerarquia */
  h2 {{
    font-size: 16pt; font-weight: 700; letter-spacing: -.015em;
    margin: 11mm 0 4mm; padding-bottom: 2.5mm;
    border-bottom: 2px solid #1a1d24; page-break-after: avoid;
  }}
  h3 {{
    font-size: 11.5pt; font-weight: 600; color: #374151;
    margin: 7mm 0 2.5mm; page-break-after: avoid;
  }}
  h4 {{ font-size: 10.5pt; font-weight: 600; margin: 5mm 0 2mm; }}
  p {{ margin: 0 0 3.5mm; }}
  ul, ol {{ margin: 0 0 3.5mm; padding-left: 6mm; }}
  li {{ margin-bottom: 1.2mm; }}
  strong {{ font-weight: 600; }}
  hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 8mm 0; }}

  /* Codigo */
  code {{
    font-family: Consolas, "Courier New", monospace; font-size: 9pt;
    background: #f3f4f6; padding: .1em .35em; border-radius: 2px;
  }}
  pre {{
    background: #f8f9fb; border: 1px solid #e5e7eb; border-left: 3px solid #2a78d6;
    border-radius: 3px; padding: 3mm 4mm; overflow-x: auto;
    font-size: 8.8pt; line-height: 1.5; page-break-inside: avoid;
    margin: 0 0 4mm;
  }}
  pre code {{ background: none; padding: 0; font-size: inherit; }}

  /* Tablas */
  table {{
    width: 100%; border-collapse: collapse; font-size: 9pt;
    margin: 0 0 5mm; page-break-inside: avoid;
  }}
  th {{
    text-align: left; background: #f3f4f6; font-weight: 600; font-size: 8.5pt;
    text-transform: uppercase; letter-spacing: .06em; color: #4b5563;
    padding: 2mm 2.5mm; border-bottom: 1.5px solid #9ca3af;
  }}
  td {{ padding: 2mm 2.5mm; border-bottom: 1px solid #eceef2; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}

  /* Citas y avisos */
  blockquote {{
    margin: 0 0 4mm; padding: 2.5mm 4mm;
    background: #fffbeb; border-left: 3px solid #d97706;
    font-size: 9.5pt; color: #422006;
  }}
  blockquote p {{ margin: 0 0 1.5mm; }}
  blockquote p:last-child {{ margin: 0; }}

  /* Referencias a archivos del repo (enlaces desactivados) */
  .ref {{
    font-family: Consolas, "Courier New", monospace;
    font-size: 9pt; color: #1f5fa8;
  }}

  img {{ max-width: 100%; height: auto; page-break-inside: avoid; }}

  @media print {{
    body {{ padding: 0; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head>
<body>

<div class="cover">
  <div class="cover-kicker">Documentación técnica</div>
  <h1>KARD</h1>
  <div class="cover-sub">Plataforma SaaS multi-tenant para gestión de negocios</div>

  <div class="cover-team">
    <div class="cover-team-label">Integrantes</div>
    <ul>{integrantes_html}</ul>
  </div>

  <div class="cover-meta">
    Backend · FastAPI · PostgreSQL · Redis<br>
    Generado el {hoy}
  </div>
</div>

{body_html}

</body>
</html>"""

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nGenerado: {OUT_FILE}")
print("Abriendo en el navegador. Ctrl+P -> Guardar como PDF.")
print("Tip: desactiva 'Encabezados y pies de pagina' en Mas opciones.")
webbrowser.open("file://" + OUT_FILE.replace("\\", "/"))
