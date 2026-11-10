"""
HTML email templates for transactional emails (the name comes from BRAND_NAME).

Each function returns a dict with "subject" and "html" keys.
All styles are inlined — email clients strip external/class-based CSS. The few
rules that cannot be inline (phone layout, dark mode) live in one <style> block
in the head; clients that ignore it still get a complete, readable email.

Colors follow the app: navy #14183a, indigo #4a5bcf.
"""

import html
from urllib.parse import urlparse

from core.config import settings

_NAVY = "#14183a"
_INDIGO = "#4a5bcf"
_RED = "#dc2626"

def _brand() -> str:
    """The product name, safe to drop into HTML. Comes from BRAND_NAME in the .env."""
    return html.escape(settings.BRAND_NAME, quote=True)


def _domain() -> str:
    """The public address people recognise (host of APP_URL), shown in the footer."""
    return urlparse(settings.APP_URL).netloc or settings.APP_URL


_FONT = "'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _esc(value: object) -> str:
    """Anything typed by a person goes through here before it lands in the HTML."""
    return html.escape(str(value), quote=True)


def _button(url: str, label: str, color: str = _INDIGO) -> str:
    """Call-to-action button built from a table so Outlook draws it too."""
    return f"""
      <table role="presentation" cellpadding="0" cellspacing="0" class="btn-wrap">
        <tr>
          <td align="center" bgcolor="{color}" style="background:{color};border-radius:10px;">
            <a href="{_esc(url)}" class="btn"
               style="display:inline-block;padding:14px 30px;font-family:{_FONT};font-size:15px;font-weight:600;
                      color:#ffffff;text-decoration:none;border-radius:10px;">{label}</a>
          </td>
        </tr>
      </table>"""


def _callout(text: str, *, bg: str, border: str, color: str) -> str:
    """A highlighted strip for warnings and results."""
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
        <tr>
          <td style="background:{bg};border:1px solid {border};border-radius:10px;padding:14px 18px;
                     color:{color};font-size:14px;font-weight:600;line-height:1.5;">{text}</td>
        </tr>
      </table>"""


def _base(title: str, body: str, preheader: str = "") -> str:
    """Shared layout: branded header, content slot, footer."""
    # The line the inbox shows next to the subject. Hidden in the email itself.
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:#f1f2f8;">{_esc(preheader)}'
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
      .page {{ background: #0d1020 !important; }}
      .card {{ background: #181c38 !important; border-color: #2a2f55 !important; }}
      .card h1 {{ color: #ffffff !important; }}
      .card p, .card li, .card td {{ color: #c9cde4 !important; }}
      .card strong {{ color: #ffffff !important; }}
      .soft {{ background: #1f2447 !important; border-color: #2a2f55 !important; }}
      .foot {{ background: #121530 !important; border-color: #2a2f55 !important; }}
    }}
  </style>
</head>
<body class="page" style="margin:0;padding:0;background:#f1f2f8;font-family:{_FONT};">
  {pre}
  <table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f2f8;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" class="container" width="560" cellpadding="0" cellspacing="0" style="width:560px;max-width:100%;">

          <!-- Header -->
          <tr>
            <td class="px" style="background:{_NAVY};border-radius:14px 14px 0 0;padding:26px 36px;">
              <span style="font-family:{_FONT};color:#ffffff;font-size:22px;font-weight:800;letter-spacing:4px;">{_brand()}</span>
            </td>
          </tr>
          <tr>
            <td style="background:{_INDIGO};height:4px;line-height:4px;font-size:0;">&nbsp;</td>
          </tr>

          <!-- Body -->
          <tr>
            <td class="card px" style="background:#ffffff;padding:36px;border-left:1px solid #e8eaf0;border-right:1px solid #e8eaf0;
                                       font-family:{_FONT};">
              {body}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td class="foot px" style="background:#f8f9fc;border:1px solid #e8eaf0;border-top:none;border-radius:0 0 14px 14px;padding:20px 36px;">
              <p style="margin:0 0 4px;font-family:{_FONT};color:#8a8fa8;font-size:12px;line-height:1.6;">
                Este mensaje fue enviado por {_brand()}. Si no esperabas este correo, puedes ignorarlo.
              </p>
              <p style="margin:0;font-family:{_FONT};color:#b0b4c8;font-size:12px;line-height:1.6;">
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
        f'<h1 style="margin:0 0 12px;font-family:{_FONT};color:{_NAVY};font-size:24px;'
        f'font-weight:700;line-height:1.3;letter-spacing:-0.3px;">{text}</h1>'
    )


def _p(text: str, *, top: int = 0, bottom: int = 24, size: int = 15, color: str = "#4b5563") -> str:
    return (
        f'<p style="margin:{top}px 0 {bottom}px;font-family:{_FONT};color:{color};'
        f'font-size:{size}px;line-height:1.65;">{text}</p>'
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
             style="background:#f8f9fc;border:1px solid #e8eaf0;border-radius:10px;margin-bottom:24px;">
        <tr>
          <td style="padding:18px 20px;">
            <p style="margin:0 0 4px;font-family:{_FONT};color:#8a8fa8;font-size:11px;font-weight:700;
                      text-transform:uppercase;letter-spacing:1px;">Tu URL pública</p>
            <a href="{_esc(storefront_url)}" style="font-family:{_FONT};color:{_INDIGO};font-size:15px;
               font-weight:600;text-decoration:none;word-break:break-all;">{_esc(storefront_url)}</a>
          </td>
        </tr>
      </table>

      <p style="margin:0 0 8px;font-family:{_FONT};color:{_NAVY};font-size:14px;font-weight:700;">Próximos pasos</p>
      <ol style="margin:0 0 28px;padding-left:20px;font-family:{_FONT};color:#4b5563;font-size:14px;line-height:2;">
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
    safe_link = _esc(link)

    body = f"""
      {_h1("Crea una contraseña nueva")}
      {_p(f'{greeting}. Recibimos una solicitud para restablecer tu contraseña de {_brand()}. '
          'Presiona el botón para elegir una nueva. El enlace sirve <strong>una sola vez</strong> '
          'y dura <strong>1 hora</strong>.', bottom=28)}

      {_button(link, "Crear contraseña nueva →")}

      <table role="presentation" class="soft" width="100%" cellpadding="0" cellspacing="0"
             style="background:#f8f9fc;border:1px solid #e8eaf0;border-radius:10px;margin-top:28px;">
        <tr>
          <td style="padding:14px 18px;">
            <p style="margin:0 0 6px;font-family:{_FONT};color:#8a8fa8;font-size:12px;line-height:1.5;">
              Si el botón no abre, copia este enlace en tu navegador:
            </p>
            <span style="font-family:{_FONT};word-break:break-all;color:{_INDIGO};font-size:12px;line-height:1.6;">{safe_link}</span>
          </td>
        </tr>
      </table>

      {_p("<strong>¿No lo pediste tú?</strong> Ignora este correo: tu contraseña no cambia y nadie puede entrar a tu cuenta con él.",
          top=24, bottom=0, size=13, color="#6b7280")}
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
      {_callout("✓&nbsp; Contraseña actualizada", bg="#f0fdf4", border="#86efac", color="#15803d")}
      {_h1("Tu contraseña cambió")}
      {_p(f"{greeting}. Tu contraseña de {_brand()} se acaba de cambiar y se cerraron las sesiones "
          "que tenías abiertas. Ya puedes iniciar sesión con la nueva.")}
      {_p("<strong>¿No fuiste tú?</strong> Pide otro enlace para recuperar tu cuenta y avisa a quien administra tu negocio.",
          bottom=0, size=14, color="#991b1b")}
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
      {_callout("⚠&nbsp; Stock bajo detectado", bg="#fffbeb", border="#fcd34d", color="#92400e")}
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

    body = f"""
      {_h1("Pago confirmado")}
      {_p(f"El pago para el plan <strong>{plan_display}</strong> de <strong>{business}</strong> "
          "fue procesado exitosamente. Tu suscripción está activa.")}

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;margin-bottom:28px;">
        <tr>
          <td style="padding:16px 20px;">
            <p style="margin:0 0 4px;font-family:{_FONT};color:#15803d;font-size:11px;font-weight:700;
                      text-transform:uppercase;letter-spacing:1px;">Plan activo</p>
            <p style="margin:0;font-family:{_FONT};color:#166534;font-size:20px;font-weight:700;">{plan_display}</p>
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
      {_callout("✕&nbsp; Pago fallido", bg="#fef2f2", border="#fca5a5", color="#991b1b")}
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
