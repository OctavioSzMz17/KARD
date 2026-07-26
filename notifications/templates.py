"""
HTML email templates for KARD transactional emails.

Each function returns a dict with "subject" and "html" keys.
All styles are inlined — email clients strip external/class-based CSS.
"""

from core.config import settings


def _base(title: str, body: str) -> str:
    """Shared layout: branded header, content slot, footer."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">

          <!-- Header -->
          <tr>
            <td style="background:#111827;border-radius:8px 8px 0 0;padding:24px 32px;">
              <span style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">KARD</span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="background:#ffffff;padding:32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
              {body}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:20px 32px;">
              <p style="margin:0;color:#9ca3af;font-size:12px;line-height:1.5;">
                Este mensaje fue enviado por KARD. Si no esperabas este correo, puedes ignorarlo.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def tenant_welcome(business_name: str, slug: str) -> dict[str, str]:
    """Sent to the business_admin right after self-service tenant registration."""
    dashboard_url = f"{settings.APP_URL}/dashboard"
    storefront_url = f"{settings.APP_URL}/s/{slug}"

    body = f"""
      <h1 style="margin:0 0 8px;color:#111827;font-size:24px;font-weight:700;">
        ¡Bienvenido a KARD, {business_name}!
      </h1>
      <p style="margin:0 0 24px;color:#4b5563;font-size:15px;line-height:1.6;">
        Tu cuenta está lista. Ya puedes comenzar a agregar productos y
        configurar tu tienda.
      </p>

      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:20px;margin-bottom:24px;">
        <tr>
          <td>
            <p style="margin:0 0 4px;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">Tu URL pública</p>
            <a href="{storefront_url}" style="color:#2563eb;font-size:15px;text-decoration:none;">{storefront_url}</a>
          </td>
        </tr>
      </table>

      <p style="margin:0 0 8px;color:#111827;font-size:14px;font-weight:600;">Próximos pasos:</p>
      <ol style="margin:0 0 28px;padding-left:20px;color:#4b5563;font-size:14px;line-height:2;">
        <li>Agrega tus productos desde el dashboard</li>
        <li>Activa tu tienda pública cuando estés listo</li>
        <li>Comparte tu URL con tus clientes</li>
      </ol>

      <a href="{dashboard_url}"
         style="display:inline-block;background:#111827;color:#ffffff;font-size:14px;font-weight:600;
                text-decoration:none;padding:12px 24px;border-radius:6px;">
        Ir al dashboard →
      </a>
    """
    return {
        "subject": f"¡Bienvenido a KARD, {business_name}!",
        "html": _base(f"Bienvenido, {business_name}", body),
    }


def consumer_welcome(name: str | None) -> dict[str, str]:
    """Sent to a new consumer after registration."""
    greeting = f"¡Hola, {name}!" if name else "¡Bienvenido a KARD!"
    explore_url = settings.APP_URL

    body = f"""
      <h1 style="margin:0 0 8px;color:#111827;font-size:24px;font-weight:700;">
        {greeting}
      </h1>
      <p style="margin:0 0 24px;color:#4b5563;font-size:15px;line-height:1.6;">
        Tu cuenta está lista. Con ella puedes acceder a cualquier tienda en
        KARD con un solo login — sin crear una cuenta nueva por cada negocio.
      </p>
      <p style="margin:0 0 28px;color:#4b5563;font-size:14px;line-height:1.6;">
        Cada vez que visites una tienda nueva, tu historial de compras y
        preferencias te esperan automáticamente.
      </p>

      <a href="{explore_url}"
         style="display:inline-block;background:#111827;color:#ffffff;font-size:14px;font-weight:600;
                text-decoration:none;padding:12px 24px;border-radius:6px;">
        Explorar tiendas →
      </a>
    """
    return {
        "subject": "Bienvenido a KARD",
        "html": _base("Bienvenido a KARD", body),
    }


def low_stock_alert(product_name: str, current_stock: int, business_name: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a product's stock drops below threshold."""
    dashboard_url = f"{settings.APP_URL}/dashboard/products"
    unit_label = "unidad" if current_stock == 1 else "unidades"

    body = f"""
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#fef3c7;border:1px solid #fbbf24;border-radius:8px;padding:16px 20px;margin-bottom:24px;">
        <tr>
          <td>
            <p style="margin:0;color:#92400e;font-size:14px;font-weight:600;">
              ⚠ &nbsp;Stock bajo detectado
            </p>
          </td>
        </tr>
      </table>

      <h1 style="margin:0 0 8px;color:#111827;font-size:22px;font-weight:700;">
        {product_name}
      </h1>
      <p style="margin:0 0 24px;color:#4b5563;font-size:15px;line-height:1.6;">
        El producto <strong>{product_name}</strong> de <strong>{business_name}</strong>
        tiene solamente <strong>{current_stock} {unit_label}</strong> en existencia.
        Considera reabastecer antes de que se agote.
      </p>

      <a href="{dashboard_url}"
         style="display:inline-block;background:#111827;color:#ffffff;font-size:14px;font-weight:600;
                text-decoration:none;padding:12px 24px;border-radius:6px;">
        Ver inventario →
      </a>
    """
    return {
        "subject": f"Stock bajo: {product_name} ({current_stock} {unit_label})",
        "html": _base(f"Stock bajo: {product_name}", body),
    }


def payment_confirmed(business_name: str, plan: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a Stripe payment succeeds."""
    plan_display = plan.capitalize()
    dashboard_url = f"{settings.APP_URL}/dashboard"

    body = f"""
      <h1 style="margin:0 0 8px;color:#111827;font-size:24px;font-weight:700;">
        Pago confirmado
      </h1>
      <p style="margin:0 0 24px;color:#4b5563;font-size:15px;line-height:1.6;">
        El pago para el plan <strong>{plan_display}</strong> de
        <strong>{business_name}</strong> fue procesado exitosamente.
        Tu suscripción está activa.
      </p>

      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:16px 20px;margin-bottom:28px;">
        <tr>
          <td>
            <p style="margin:0 0 4px;color:#15803d;font-size:12px;font-weight:600;text-transform:uppercase;">Plan activo</p>
            <p style="margin:0;color:#166534;font-size:18px;font-weight:700;">{plan_display}</p>
          </td>
        </tr>
      </table>

      <a href="{dashboard_url}"
         style="display:inline-block;background:#111827;color:#ffffff;font-size:14px;font-weight:600;
                text-decoration:none;padding:12px 24px;border-radius:6px;">
        Ir al dashboard →
      </a>
    """
    return {
        "subject": f"Pago confirmado — Plan {plan_display}",
        "html": _base("Pago confirmado", body),
    }


def payment_failed(business_name: str, plan: str) -> dict[str, str]:
    """Sent to the tenant's business_admin when a Stripe payment fails."""
    plan_display = plan.capitalize()
    billing_url = f"{settings.APP_URL}/dashboard/billing"

    body = f"""
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:16px 20px;margin-bottom:24px;">
        <tr>
          <td>
            <p style="margin:0;color:#991b1b;font-size:14px;font-weight:600;">
              ✕ &nbsp;Pago fallido
            </p>
          </td>
        </tr>
      </table>

      <h1 style="margin:0 0 8px;color:#111827;font-size:22px;font-weight:700;">
        No pudimos procesar tu pago
      </h1>
      <p style="margin:0 0 24px;color:#4b5563;font-size:15px;line-height:1.6;">
        El cargo para el plan <strong>{plan_display}</strong> de
        <strong>{business_name}</strong> fue rechazado.
        Por favor actualiza tu método de pago para mantener tu cuenta activa.
      </p>

      <a href="{billing_url}"
         style="display:inline-block;background:#dc2626;color:#ffffff;font-size:14px;font-weight:600;
                text-decoration:none;padding:12px 24px;border-radius:6px;">
        Actualizar método de pago →
      </a>
    """
    return {
        "subject": f"Pago fallido — revisa tu método de pago",
        "html": _base("Pago fallido", body),
    }
