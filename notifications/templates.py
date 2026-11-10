"""
HTML email templates for transactional emails (the name comes from BRAND_NAME).

Each function returns a dict with "subject" and "html" keys.
All styles are inlined — email clients strip external/class-based CSS. The few
rules that cannot be inline (phone layout, dark mode) live in one <style> block
in the head; clients that ignore it still get a complete, readable email.

Look: black and white, no accent color. Hierarchy comes from contrast and weight.
Color is reserved for states (green = done, amber = warning, red = problem).
The web app still uses navy/indigo until it is moved to the same palette.
"""

import html
from urllib.parse import urlparse

from core.config import settings

# ── Palette ────────────────────────────────────────────────────────────────
_INK = "#0a0a0b"      # black: header, headings, main button
_RED = "#dc2626"      # only for the "payment failed" button
_TEXT = "#3f3f46"     # body text
_MUTED = "#71717a"    # labels and small print
_FAINT = "#a1a1aa"    # footer address
_LINE = "#e4e4e7"     # borders
_SOFT = "#fafafa"     # quiet boxes
_PAGE = "#f4f4f5"     # page background

# Status strips: (background, border, text) in light mode.
_STATUS = {
    "ok":   ("#f0fdf4", "#86efac", "#15803d"),
    "warn": ("#fffbeb", "#fcd34d", "#92400e"),
    "bad":  ("#fef2f2", "#fca5a5", "#991b1b"),
}

_FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _brand() -> str:
    """The product name, safe to drop into HTML. Comes from BRAND_NAME in the .env."""
    return html.escape(settings.BRAND_NAME, quote=True)


def _domain() -> str:
    """The public address people recognise (host of APP_URL), shown in the footer."""
    return urlparse(settings.APP_URL).netloc or settings.APP_URL


def _esc(value: object) -> str:
    """Anything typed by a person goes through here before it lands in the HTML."""
    return html.escape(str(value), quote=True)


def _button(url: str, label: str, color: str | None = None) -> str:
    """Call-to-action button built from a table so Outlook draws it too.

    Black by default. A different `color` (the red of a failed payment) is kept
    as is in dark mode; the black one flips to white so it does not vanish.
    """
    kind = "main" if color is None else "other"
    color = color or _INK
    return f"""
      <table role="presentation" cellpadding="0" cellspacing="0" class="btn-wrap">
        <tr>
          <td align="center" bgcolor="{color}" class="cell-{kind}" style="background:{color};border-radius:8px;">
            <a href="{_esc(url)}" class="btn a-{kind}"
               style="display:inline-block;padding:14px 32px;font-family:{_FONT};font-size:14px;font-weight:600;
                      letter-spacing:0.3px;color:#ffffff;text-decoration:none;border-radius:8px;">{label}</a>
          </td>
        </tr>
      </table>"""


def _callout(text: str, *, kind: str) -> str:
    """A highlighted strip for warnings and results. `kind`: ok, warn or bad."""
    bg, border, color = _STATUS[kind]
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
        <tr>
          <td class="{kind}" style="background:{bg};border:1px solid {border};border-radius:8px;padding:14px 18px;
                     color:{color};font-size:14px;font-weight:600;line-height:1.5;">{text}</td>
        </tr>
      </table>"""


def _link(url: str, *, size: int = 15) -> str:
    """A web address shown as text: black and underlined, white in dark mode."""
    return (
        f'<a href="{_esc(url)}" class="lnk" style="font-family:{_FONT};color:{_INK};font-size:{size}px;'
        f'font-weight:600;text-decoration:underline;word-break:break-all;">{_esc(url)}</a>'
    )


def _base(title: str, body: str, preheader: str = "") -> str:
    """Shared layout: black header, content slot, footer."""
    # The line the inbox shows next to the subject. Hidden in the email itself.
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:{_PAGE};">{_esc(preheader)}'
        f'{"&#847;&zwnj;&nbsp;" * 30}</div>'
        if preheader
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  <title>{_esc(title)}</title>
  <style>
    @media only screen and (max-width: 620px) {{
      .container {{ width: 100% !important; }}
      .px {{ padding-left: 22px !important; padding-right: 22px !important; }}
      .btn-wrap {{ width: 100% !important; }}
      .btn {{ display: block !important; text-align: center !important; }}
    }}
    @media (prefers-color-scheme: dark) {{
      .page {{ background: #000000 !important; }}
      .card {{ background: #111113 !important; border-color: #26262b !important; }}
      .card h1 {{ color: #ffffff !important; }}
      .card p, .card li, .card td {{ color: #d4d4d8 !important; }}
      .card strong {{ color: #ffffff !important; }}
      .soft {{ background: #18181b !important; border-color: #26262b !important; }}
      .foot {{ background: #0c0c0e !important; border-color: #26262b !important; }}
      .lnk {{ color: #ffffff !important; }}
      .cell-main {{ background: #ffffff !important; }}
      .a-main {{ color: #0a0a0b !important; }}
      .card td.ok {{ background: #052e16 !important; border-color: #166534 !important; color: #86efac !important; }}
      .card td.warn {{ background: #422006 !important; border-color: #92400e !important; color: #fcd34d !important; }}
      .card td.bad {{ background: #450a0a !important; border-color: #991b1b !important; color: #fca5a5 !important; }}
      .card .okbox {{ background: #052e16 !important; border-color: #166534 !important; }}
    }}
  </style>
</head>
<body class="page" style="margin:0;padding:0;background:{_PAGE};font-family:{_FONT};">
  {pre}
  <table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAGE};padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" class="container" width="560" cellpadding="0" cellspacing="0" style="width:560px;max-width:100%;">

          <!-- Header -->
          <tr>
            <td class="px" style="background:{_INK};border-radius:12px 12px 0 0;padding:28px 36px;">
              <span style="font-family:{_FONT};color:#ffffff;font-size:17px;font-weight:600;letter-spacing:6px;">{_brand()}</span>
            </td>
          </tr>
          <!-- Thin highlight under the header: a line of light that fades at both ends -->
          <tr>
            <td style="background:#52525b;background-image:linear-gradient(90deg,#0a0a0b 0%,#f4f4f5 50%,#0a0a0b 100%);height:2px;line-height:2px;font-size:0;">&nbsp;</td>
          </tr>

          <!-- Body -->
          <tr>
            <td class="card px" style="background:#ffffff;padding:40px 36px;border-left:1px solid {_LINE};border-right:1px solid {_LINE};
                                       font-family:{_FONT};">
              {body}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td class="foot px" style="background:{_SOFT};border:1px solid {_LINE};border-top:none;border-radius:0 0 12px 12px;padding:20px 36px;">
              <p style="margin:0 0 4px;font-family:{_FONT};color:{_MUTED};font-size:12px;line-height:1.6;">
                Este mensaje fue enviado por {_brand()}. Si no esperabas este correo, puedes ignorarlo.
              </p>
              <p style="margin:0;font-family:{_FONT};color:{_FAINT};font-size:12px;line-height:1.6;">
                {_esc(_domain())}
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _h1(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 14px;font-family:{_FONT};color:{_INK};font-size:24px;'
        f'font-weight:600;line-height:1.3;letter-spacing:-0.4px;">{text}</h1>'
    )


def _p(text: str, *, top: int = 0, bottom: int = 24, size: int = 15, color: str = _TEXT) -> str:
    return (
        f'<p style="margin:{top}px 0 {bottom}px;font-family:{_FONT};color:{color};'
        f'font-size:{size}px;line-height:1.7;">{text}</p>'
    )


def tenant_welcome(business_name: str, slug: str) -> dict[str, str]:
    """Sent to the business_admin right after self-service tenant registration."""
    business = _esc(business_name)
    dashboard_url = f"{settings.APP_URL}/dashboard"
    storefront_url = f"{settings.APP_URL}/s/{slug}"

    body = f"""
      {_h1(f"¡Bienvenido a {_brand()}, {business}!")}
      {_p("Tu cuenta está lista. Ya puedes comenzar a agregar productos y configurar tu tienda.")}

      <table role="presentation" class="soft" width="100%" cellpadding="0" cellspacing="0"
             style="background:{_SOFT};border:1px solid {_LINE};border-radius:8px;margin-bottom:24px;">
        <tr>
          <td style="padding:18px 20px;">
            <p style="margin:0 0 4px;font-family:{_FONT};color:{_MUTED};font-size:11px;font-weight:600;
                      text-transform:uppercase;letter-spacing:1.5px;">Tu URL pública</p>
            {_link(storefront_url)}
          </td>
        </tr>
      </table>

      <p style="margin:0 0 8px;font-family:{_FONT};color:{_INK};font-size:14px;font-weight:600;">Próximos pasos</p>
      <ol style="margin:0 0 28px;padding-left:20px;font-family:{_FONT};color:{_TEXT};font-size:14px;line-height:2;">
        <li>Agrega tus productos desde el dashboard</li>
        <li>Activa tu tienda pública cuando estés listo</li>
        <li>Comparte tu URL con tus clientes</li>
      </ol>

      {_button(dashboard_url, "Ir al dashboard →")}
    """
    return {
        "subject": f"¡Bienvenido a {settings.BRAND_NAME}, {business_name}!",
        "html": _base(
            f"Bienvenido, {business_name}", body,
            preheader="Tu cuenta está lista. Agrega tus productos y comparte tu tienda.",
        ),
    }


def consumer_welcome(name: str | None) -> dict[str, str]:
    """Sent to a new consumer after registration."""
    greeting = f"¡Hola, {_esc(name)}!" if name else f"¡Bienvenido a {_brand()}!"
    explore_url = settings.APP_URL

    body = f"""
      {_h1(greeting)}
      {_p(f"Tu cuenta está lista. Con ella puedes acceder a cualquier tienda en {_brand()} con un solo login, sin crear una cuenta nueva por cada negocio.")}
      {_p("Cada vez que visites una tienda nueva, tu historial de compras y preferencias te esperan automáticamente.", bottom=28, size=14)}

      {_button(explore_url, "Explorar tiendas →")}
    """
    return {
        "subject": f"Bienvenido a {settings.BRAND_NAME}",
        "html": _base(
            f"Bienvenido a {settings.BRAND_NAME}", body,
            preheader=f"Una sola cuenta para todas las tiendas de {settings.BRAND_NAME}.",
        ),
    }


def password_reset(name: str | None, link: str) -> dict[str, str]:
    """Sent when someone asks to reset their password. `link` carries the one-time token."""
    greeting = f"Hola, {_esc(name)}" if name else "Hola"

    body = f"""
      {_h1("Crea una contraseña nueva")}
      {_p(f'{greeting}. Recibimos una solicitud para restablecer tu contraseña de {_brand()}. '
          'Presiona el botón para elegir una nueva. El enlace sirve <strong>una sola vez</strong> '
          'y dura <strong>1 hora</strong>.', bottom=28)}

      {_button(link, "Crear contraseña nueva →")}

      <table role="presentation" class="soft" width="100%" cellpadding="0" cellspacing="0"
             style="background:{_SOFT};border:1px solid {_LINE};border-radius:8px;margin-top:28px;">
        <tr>
          <td style="padding:14px 18px;">
            <p style="margin:0 0 6px;font-family:{_FONT};color:{_MUTED};font-size:12px;line-height:1.5;">
              Si el botón no abre, copia este enlace en tu navegador:
            </p>
            {_link(link, size=12)}
          </td>
        </tr>
      </table>

      {_p("<strong>¿No lo pediste tú?</strong> Ignora este correo: tu contraseña no cambia y nadie puede entrar a tu cuenta con él.",
          top=24, bottom=0, size=13, color=_MUTED)}
    """
    return {
        "subject": f"Restablece tu contraseña de {settings.BRAND_NAME}",
        "html": _base(
            "Restablece tu contraseña", body,
            preheader="Usa este enlace para elegir una contraseña nueva. Dura 1 hora.",
        ),
    }


def password_changed(name: str | None) -> dict[str, str]:
    """Confirmation after a reset — lets the owner of the account notice an unwanted change."""
    greeting = f"Hola, {_esc(name)}" if name else "Hola"

    body = f"""
      {_callout("✓&nbsp; Contraseña actualizada", kind="ok")}
      {_h1("Tu contraseña cambió")}
      {_p(f"{greeting}. Tu contraseña de {_brand()} se acaba de cambiar y se cerraron las sesiones "
          "que tenías abiertas. Ya puedes iniciar sesión con la nueva.")}
      {_p("<strong>¿No fuiste tú?</strong> Pide otro enlace para recuperar tu cuenta y avisa a quien administra tu negocio.",
          bottom=0, size=14, color=_STATUS["bad"][2])}
    """
    return {
        "subject": f"Tu contraseña de {settings.BRAND_NAME} cambió",
        "html": _base(
            "Tu contraseña cambió", body,
            preheader="Se cerraron tus sesiones abiertas. Si no fuiste tú, recupera tu cuenta.",
        ),
    }


def low_stock_alert(product_name: str, current_stock: int, business_name: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a product's stock drops below threshold."""
    product = _esc(product_name)
    business = _esc(business_name)
    dashboard_url = f"{settings.APP_URL}/dashboard/products"
    unit_label = "unidad" if current_stock == 1 else "unidades"

    body = f"""
      {_callout("⚠&nbsp; Stock bajo detectado", kind="warn")}
      {_h1(product)}
      {_p(f"El producto <strong>{product}</strong> de <strong>{business}</strong> "
          f"tiene solamente <strong>{current_stock} {unit_label}</strong> en existencia. "
          "Considera reabastecer antes de que se agote.", bottom=28)}

      {_button(dashboard_url, "Ver inventario →")}
    """
    return {
        "subject": f"Stock bajo: {product_name} ({current_stock} {unit_label})",
        "html": _base(
            f"Stock bajo: {product_name}", body,
            preheader=f"Quedan {current_stock} {unit_label} de {product_name}.",
        ),
    }


def payment_confirmed(business_name: str, plan: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a Stripe payment succeeds."""
    plan_display = _esc(plan.capitalize())
    business = _esc(business_name)
    dashboard_url = f"{settings.APP_URL}/dashboard"
    ok_bg, ok_border, ok_text = _STATUS["ok"]

    body = f"""
      {_h1("Pago confirmado")}
      {_p(f"El pago para el plan <strong>{plan_display}</strong> de <strong>{business}</strong> "
          "fue procesado exitosamente. Tu suscripción está activa.")}

      <table role="presentation" class="okbox" width="100%" cellpadding="0" cellspacing="0"
             style="background:{ok_bg};border:1px solid {ok_border};border-radius:8px;margin-bottom:28px;">
        <tr>
          <td style="padding:16px 20px;">
            <p style="margin:0 0 4px;font-family:{_FONT};color:{ok_text};font-size:11px;font-weight:600;
                      text-transform:uppercase;letter-spacing:1.5px;">Plan activo</p>
            <p style="margin:0;font-family:{_FONT};color:#166534;font-size:20px;font-weight:600;">{plan_display}</p>
          </td>
        </tr>
      </table>

      {_button(dashboard_url, "Ir al dashboard →")}
    """
    return {
        "subject": f"Pago confirmado — Plan {plan.capitalize()}",
        "html": _base(
            "Pago confirmado", body,
            preheader=f"Tu plan {plan.capitalize()} está activo.",
        ),
    }


def payment_failed(business_name: str, plan: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a Stripe payment fails."""
    plan_display = _esc(plan.capitalize())
    business = _esc(business_name)
    billing_url = f"{settings.APP_URL}/dashboard/billing"

    body = f"""
      {_callout("✕&nbsp; Pago fallido", kind="bad")}
      {_h1("No pudimos procesar tu pago")}
      {_p(f"El cargo para el plan <strong>{plan_display}</strong> de <strong>{business}</strong> "
          "fue rechazado. Por favor actualiza tu método de pago para mantener tu cuenta activa.", bottom=28)}

      {_button(billing_url, "Actualizar método de pago →", color=_RED)}
    """
    return {
        "subject": "Pago fallido — revisa tu método de pago",
        "html": _base(
            "Pago fallido", body,
            preheader="Actualiza tu método de pago para mantener tu cuenta activa.",
        ),
    }
