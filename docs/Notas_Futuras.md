# KARD — Notas para implementar más adelante

Ideas acordadas que **todavía no se construyen**. No son decisiones cerradas:
cada una necesita su análisis antes de empezar. Se agregan al final, con fecha.

---

## 1. Ubicación del dispositivo (anti mala praxis)

**Fecha:** 2 de noviembre de 2026 · **Estado:** pendiente de analizar

**Objetivo.** Evitar que se modifiquen datos del negocio o se hagan
transacciones desde fuera del lugar del negocio (por ejemplo, editar precios o
cancelar ventas desde la casa de un empleado).

**Idea.** Validar la ubicación del dispositivo antes de permitir ciertas
acciones sensibles.

**Puntos a analizar antes de implementarlo** (son preguntas, no decisiones):

- **Qué acciones se restringen.** Candidatas: editar productos y precios,
  ajustar inventario, cancelar o reembolsar ventas, abrir y cerrar caja,
  cambiar permisos. Cobrar probablemente no.
- **Qué pasa fuera del negocio.** ¿Solo lectura? ¿Se bloquea del todo? ¿Se
  permite con aprobación de un gerente?
- **Dónde se valida.** La ubicación que manda el navegador la puede falsear
  quien sepa. La regla debe comprobarse en el **servidor** (coordenadas
  recibidas contra el lugar del negocio), no solo en la pantalla.
- **Precisión.** El GPS de un celular es bueno; una computadora de escritorio
  solo tiene ubicación aproximada (por wifi o dirección IP) y puede quedar
  fuera del radio estando dentro. Hay que definir un radio tolerante.
- **Cómo se registra el lugar del negocio.** Coordenadas y radio, uno por
  sucursal si más adelante se modelan varias.
- **Excepciones.** Si el dueño puede operar desde cualquier lugar.
- **Privacidad.** Pedir permiso de ubicación al usuario, avisar para qué se usa
  y guardar lo mínimo necesario. Conviene un aviso de privacidad para el
  personal.
- **Registro.** Anotar en la bitácora los intentos rechazados por ubicación.
- **Relación con otras piezas.** Encaja con la autorización de equipos por QR y
  con las cajas registradoras: un equipo autorizado podría quedar atado a un
  lugar.
