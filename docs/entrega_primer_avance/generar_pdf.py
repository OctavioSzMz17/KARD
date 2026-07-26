"""
Genera un HTML listo para PDF con todos los documentos del Primer Avance.

Combina los 5 documentos en un solo archivo con portada, saltos de página
y la gráfica de burndown renderizada con mermaid.js.

Uso:
    python docs/entrega_primer_avance/generar_pdf.py

Luego: se abre en el navegador -> Ctrl+P -> "Guardar como PDF".
(Marca "Gráficos de fondo" en las opciones de impresión para conservar colores.)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import os, re, html as html_lib, webbrowser

DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DIR, "KARD - Primer Avance.html")

# Orden de los documentos en el PDF final
DOCS = [
    ("Requisitos, Casos de Uso e Historias de Usuario", "01_Requisitos_HU_Valoradas.md"),
    ("Product Backlog y Sprint Backlog",                "02_Backlog_Sprint.md"),
    ("Gráfica de Quemado (Burndown)",                   "03_Burndown.md"),
    ("Tablero Trello",                                  "04_Tablero_Trello.md"),
    ("Diseño de Base de Datos",                         "05_BD_NoSQL_y_Relacional.md"),
]

try:
    import markdown
except ImportError:
    print("Falta la librería markdown. Ejecuta: pip install markdown")
    sys.exit(1)


def md_to_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    # Convertir bloques ```mermaid en <div class="mermaid"> para que mermaid.js los dibuje
    def to_mermaid(m):
        code = m.group(1)
        code = html_lib.unescape(code)  # deshacer &lt; &amp; &quot; etc.
        return f'<div class="mermaid">{code}</div>'
    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        to_mermaid, body, flags=re.DOTALL,
    )
    return body


sections = []
for title, filename in DOCS:
    path = os.path.join(DIR, filename)
    with open(path, encoding="utf-8") as f:
        sections.append(f'<section class="doc">\n{md_to_html(f.read())}\n</section>')
    print(f"  OK  {filename}")

body_html = '\n<div class="page-break"></div>\n'.join(sections)

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"/>
<title>KARD — Primer Avance</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'default' }});</script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 10.5pt; line-height: 1.6; color: #1a1a2e;
    background: #fff; padding: 40px 55px; max-width: 900px; margin: 0 auto;
  }}
  .cover {{
    text-align: center; padding: 120px 0 80px; page-break-after: always;
  }}
  .cover h1 {{ font-size: 34pt; border: none; color: #1e1b4b; }}
  .cover .sub {{ font-size: 14pt; color: #6366f1; margin-top: 8px; font-weight: 600; }}
  .cover .meta {{ margin-top: 60px; font-size: 12pt; color: #444; line-height: 2; }}

  h1 {{ font-size: 20pt; font-weight: 800; margin: 0 0 12px; color: #1e1b4b; border-bottom: 3px solid #6366f1; padding-bottom: 8px; }}
  h2 {{ font-size: 14pt; font-weight: 700; margin: 26px 0 8px; color: #312e81; border-left: 4px solid #6366f1; padding-left: 10px; }}
  h3 {{ font-size: 11.5pt; font-weight: 600; margin: 18px 0 6px; color: #4338ca; }}
  p  {{ margin: 7px 0; }}
  ul, ol {{ margin: 7px 0 7px 22px; }}
  li {{ margin: 3px 0; }}

  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 9pt; }}
  th {{ background: #eef2ff; color: #3730a3; font-weight: 700; font-size: 8pt;
       text-transform: uppercase; letter-spacing: 0.3px; padding: 6px 8px;
       border: 1px solid #c7d2fe; text-align: left; }}
  td {{ padding: 5px 8px; border: 1px solid #e0e7ff; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #f5f3ff; }}

  pre {{ font-family: 'Consolas', monospace; font-size: 8pt; background: #1e1b4b;
        color: #e2e8f0; border-radius: 6px; padding: 12px 14px; margin: 10px 0;
        overflow-x: auto; page-break-inside: avoid; }}
  code {{ font-family: 'Consolas', monospace; font-size: 8.5pt; background: #eef2ff;
         color: #4338ca; padding: 1px 5px; border-radius: 3px; }}
  pre code {{ background: none; padding: 0; color: inherit; }}

  .mermaid {{ text-align: center; margin: 18px 0; page-break-inside: avoid; }}
  hr {{ border: none; border-top: 1.5px solid #e0e7ff; margin: 22px 0; }}
  strong {{ color: #312e81; }}
  blockquote {{ border-left: 3px solid #6366f1; padding-left: 12px; color: #4338ca; margin: 10px 0; }}
  .page-break {{ page-break-after: always; }}
  .doc {{ padding-top: 8px; }}

  @media print {{
    body {{ padding: 15px 25px; }}
    h2, h3 {{ page-break-after: avoid; }}
    table, pre, .mermaid {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
  <div class="cover">
    <h1>KARD</h1>
    <div class="sub">Plataforma SaaS Marketplace B2B2C</div>
    <div class="sub" style="font-size:16pt;margin-top:40px;color:#1e1b4b;">Primer Avance</div>
    <div class="meta">
      <strong>Equipo:</strong><br/>
      Octavio Sánchez Mendoza<br/>
      Brayan Adonai Díaz Santiago<br/><br/>
      <strong>Sprint 1:</strong> 02-jul-2026 → 09-jul-2026
    </div>
  </div>
  {body_html}
</body>
</html>"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nOK  Archivo generado:\n  {OUT}\n")
print("Abriendo en el navegador -> Ctrl+P -> Guardar como PDF")
print("(Activa 'Gráficos de fondo' para conservar los colores.)")
webbrowser.open(f"file:///{OUT.replace(chr(92), '/')}")
