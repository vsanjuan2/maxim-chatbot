#!/usr/bin/env python3
"""
Proxy para el Asesor de Maxim Domenech - Versión Cloud Run
─────────────────────────────────────────────────────────────
Endpoints:
  GET  /                → Redirect a landing.html
  GET  /*.html          → Archivos estáticos
  POST /api/chat        → Proxy a Claude API (chatbot + CRM manual)
  POST /api/webhook     → Reenviar a Make.com → HubSpot
  POST /api/timelinesai → Webhook TimelinesAI → Claude → Firestore → Make.com

Variables de entorno:
  ANTHROPIC_API_KEY  → API key de Anthropic (secreto)
  WEBHOOK_URL        → URL del webhook Make.com
  TIMELINESAI_TOKEN  → Token API de TimelinesAI (para validar webhooks)
  PORT               → Puerto (Cloud Run lo asigna, default 8080)
"""

import os
import json
import http.server
import urllib.request
import urllib.error
import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
TIMELINESAI_TOKEN = os.environ.get("TIMELINESAI_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))
CARPETA = Path(__file__).parent

# ═══════════════════════════════════════════════════════════════
# FIRESTORE (capa de abstracción para migración futura a pgvector)
# ═══════════════════════════════════════════════════════════════
try:
    from google.cloud import firestore
    db = firestore.Client()
    print("[Firestore] Conectado OK")
except Exception as e:
    db = None
    print(f"[Firestore] No disponible: {e} — continuando sin persistencia")


def db_get_chat_state(chat_id):
    """Obtener estado del último procesamiento de un chat."""
    if not db:
        return None
    try:
        doc = db.collection("chat_state").document(str(chat_id)).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        print(f"[DB] Error leyendo chat_state/{chat_id}: {e}")
        return None


def db_save_conversation(data):
    """Guardar conversación procesada."""
    if not db:
        return
    try:
        doc_id = f"{data['chat_id']}_{data['max_message_id']}"
        db.collection("conversations").document(doc_id).set(data)
        print(f"[DB] Conversación guardada: {doc_id}")
    except Exception as e:
        print(f"[DB] Error guardando conversación: {e}")


def db_save_crm_record(data):
    """Guardar copia de registro CRM enviado a Make.com."""
    if not db:
        return
    try:
        db.collection("crm_records").add(data)
        print(f"[DB] CRM record guardado")
    except Exception as e:
        print(f"[DB] Error guardando CRM record: {e}")


def db_update_chat_state(chat_id, message_id):
    """Actualizar estado de último mensaje procesado."""
    if not db:
        return
    try:
        db.collection("chat_state").document(str(chat_id)).set({
            "last_message_id": message_id,
            "last_processed_at": firestore.SERVER_TIMESTAMP,
            "times_processed": firestore.Increment(1),
        }, merge=True)
    except Exception as e:
        print(f"[DB] Error actualizando chat_state/{chat_id}: {e}")


def db_update_stats(comercial_phone, comercial_name, claude_result):
    """Actualizar estadísticas mensuales y globales."""
    if not db:
        return
    try:
        month = datetime.date.today().strftime("%Y-%m")
        stat_id = f"{comercial_phone}_{month}"
        category = claude_result.get("categoria", "irrelevante")
        acciones = claude_result.get("acciones", {})

        # Stats mensuales por comercial
        updates = {
            "comercial_name": comercial_name,
            "comercial_phone": comercial_phone,
            "month": month,
            "last_updated": firestore.SERVER_TIMESTAMP,
            "total_conversations": firestore.Increment(1),
        }
        if category == "comercial":
            updates["relevant_conversations"] = firestore.Increment(1)
            if acciones.get("crear_deal"):
                updates["deals_created"] = firestore.Increment(1)
            if acciones.get("crear_contacto"):
                updates["contacts_created"] = firestore.Increment(1)
            if acciones.get("crear_empresa"):
                updates["empresas_created"] = firestore.Increment(1)
            if acciones.get("crear_tarea"):
                updates["tareas_created"] = firestore.Increment(1)
        elif category == "coordinacion":
            updates["coordination_conversations"] = firestore.Increment(1)

        db.collection("stats_monthly").document(stat_id).set(
            updates, merge=True
        )

        # Stats globales
        global_updates = {
            "total_conversations": firestore.Increment(1),
            "last_updated": firestore.SERVER_TIMESTAMP,
        }
        if category == "comercial":
            global_updates["total_relevant"] = firestore.Increment(1)
            if acciones.get("crear_deal"):
                global_updates["total_deals"] = firestore.Increment(1)
            if acciones.get("crear_contacto"):
                global_updates["total_contacts"] = firestore.Increment(1)

        # Registrar comercial activo
        db.collection("stats_global").document("current").set(
            global_updates, merge=True
        )

    except Exception as e:
        print(f"[DB] Error actualizando stats: {e}")


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT CRM AMPLIADO (categorías de coordinación)
# ═══════════════════════════════════════════════════════════════
def get_crm_system_prompt():
    """Genera el system prompt con la fecha actual inyectada."""
    fecha = datetime.date.today().isoformat()
    return f"""Eres un asistente de CRM para Maxim Domenech Peru, empresa de alquiler de maquinaria de elevacion (plataformas tijera y articuladas).

FECHA ACTUAL: {fecha}

Analiza la siguiente conversacion de WhatsApp entre un comercial y un contacto. Tu tarea es:

1. CLASIFICAR la conversacion en una de estas categorias:
   - "comercial": solicitudes de presupuesto, reservas de equipos, averias/incidencias de equipos alquilados, clientes pidiendo mas equipos, consultas comerciales con intencion
   - "coordinacion": logistica de entrega/recojo de equipos, envio de documentacion (facturas, contratos, guias), coordinacion de pagos, incidencias operativas (no averias del equipo sino problemas logisticos)
   - "interno": conversaciones entre empleados de Maxim, coordinacion con proveedores
   - "irrelevante": conversaciones personales, spam, mensajes sin contenido util

   Subcategorias para coordinacion: "entrega_equipo", "recojo_equipo", "documentacion", "facturacion", "incidencia_operativa", "pago"
   Subcategorias para comercial: "presupuesto", "reserva", "averia", "extension", "consulta"

2. Para TODAS las categorias (excepto irrelevante), EXTRAER la siguiente informacion si esta disponible:
   - nombre_contacto, apellido_contacto
   - empresa, razon_social, ruc
   - email, telefono_adicional
   - sector (construccion, mineria, industrial, energia, gobierno, salud, otro)
   - equipo_interes (tipo y altura si se menciona)
   - ubicacion_obra (ciudad, distrito, direccion)
   - resumen: 2-3 frases describiendo la conversacion

3. SOLO para categoria "comercial", DECIDIR que acciones CRM tomar:
   - crear_contacto: true/false (true si es un contacto nuevo. REQUIERE al menos nombre y telefono o email)
   - actualizar_contacto: true/false (true si parece un contacto existente con datos nuevos)
   - crear_empresa: true/false (SOLO true si se dispone de RUC. Si solo se menciona un nombre comercial sin RUC, poner false. Si tiene RUC pero no razon social completa con tipo societario (SAC, SA, SRL, EIRL, etc.), poner true pero incluir "razon_social" en datos_pendientes)
   - crear_deal: true/false (true para CUALQUIER oportunidad activa: presupuestos concretos, reservas, extensiones, Y TAMBIEN averias/incidencias ya que representan un servicio tecnico facturable. FALSE para consultas generales sin intencion concreta)
   - etapa_deal: "consulta_inicial" | "cotizacion" | "negociacion" | "servicio_tecnico" | null
   - crear_tarea: true/false (true siempre que haya un deal o una accion pendiente del comercial)
   - tarea_asunto: string (ej: "Cotizar Plataforma Tijera 12m - Constructora Vida")
   - tarea_descripcion: string con detalle. IMPORTANTE: si faltan datos clave (RUC, razon social, email), incluir como primer paso "Solicitar datos fiscales completos al contacto antes de proceder." Si tiene RUC pero falta razon social: "Consultar razon social en SUNAT (https://e-consultaruc.sunat.gob.pe) con el RUC proporcionado."
   - tarea_fecha_vencimiento: YYYY-MM-DD. Para averias/urgentes: fecha actual. Cotizaciones: dia siguiente. Reservas con fecha: 3 dias antes de entrega.
   - datos_pendientes: lista de datos faltantes (ej: ["RUC", "razon_social", "email"]). Si no falta nada: []
   - urgencia: "alta" (averia/emergencia/urgente/esta semana), "media" (fecha proxima 1-4 semanas), "baja" (consulta general/fecha lejana)
   - tipo_solicitud: "presupuesto" | "reserva" | "averia" | "extension" | "consulta"

4. Responde SOLO en formato JSON valido:

Para conversacion COMERCIAL:
{{
  "categoria": "comercial",
  "subcategoria": "presupuesto",
  "datos": {{
    "nombre_contacto": "Carlos", "apellido_contacto": "Ramirez",
    "empresa": "Constructora Vida", "razon_social": null, "ruc": null,
    "email": null, "telefono_adicional": null, "sector": "construccion",
    "equipo_interes": "Plataforma tijera 12m", "ubicacion_obra": "San Isidro, Lima",
    "urgencia": "media", "tipo_solicitud": "presupuesto",
    "resumen": "Carlos de Constructora Vida necesita tijera 12m para obra en San Isidro."
  }},
  "acciones": {{
    "crear_contacto": true, "actualizar_contacto": false,
    "crear_empresa": false, "crear_deal": true,
    "etapa_deal": "cotizacion", "crear_tarea": true,
    "tarea_asunto": "Cotizar Tijera 12m - Constructora Vida",
    "tarea_descripcion": "Enviar cotizacion. Solicitar datos fiscales completos.",
    "tarea_fecha_vencimiento": "{fecha}",
    "datos_pendientes": ["RUC", "email"]
  }}
}}

Para conversacion de COORDINACION:
{{
  "categoria": "coordinacion",
  "subcategoria": "entrega_equipo",
  "datos": {{
    "nombre_contacto": "Ana", "apellido_contacto": "Lopez",
    "empresa": "Constructora Vida", "equipo_interes": "Tijera 12m",
    "ubicacion_obra": "San Isidro, Lima",
    "resumen": "Coordinacion de entrega de tijera 12m para obra en San Isidro. Entrega programada para martes 8am."
  }}
}}

Para conversacion IRRELEVANTE:
{{"categoria": "irrelevante", "subcategoria": "personal", "datos": {{"resumen": "Conversacion personal sin contenido comercial."}}}}"""


# ═══════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES
# ═══════════════════════════════════════════════════════════════
def format_messages_for_claude(messages, contact_name, contact_phone):
    """Formatea mensajes de TimelinesAI para análisis de Claude."""
    lines = []
    for m in messages:
        ts = m.get("timestamp", 0)
        time_str = datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "??:??"
        direction = m.get("direction", "unknown")
        sender = m.get("sender", "desconocido")
        text = m.get("text", "")

        if not text or not text.strip():
            continue

        if direction == "incoming":
            label = f"{contact_name or 'Contacto'} (tel: {contact_phone or sender})"
        else:
            label = "Comercial Maxim"

        lines.append(f"[{time_str}] {label}: {text}")

    return "\n".join(lines)


def call_claude_crm(conversation_text):
    """Llama a Claude API con el prompt CRM y devuelve el JSON parseado."""
    prompt = get_crm_system_prompt()

    request_body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "system": prompt,
        "messages": [{"role": "user", "content": conversation_text}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=request_body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = data.get("content", [{}])[0].get("text", "")
            # Extraer JSON de la respuesta (Claude a veces envuelve en ```)
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
            return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[Claude CRM] JSON inválido: {e} — texto: {text[:200]}")
        return None
    except Exception as e:
        print(f"[Claude CRM] Error: {e}")
        return None


def build_make_payload(claude_result, contact_phone, chat_url):
    """Construye el payload compatible con Make.com → HubSpot."""
    datos = claude_result.get("datos", {})
    acciones = claude_result.get("acciones", {})

    payload = {
        "origen": "whatsapp-auto",
        "telefono": contact_phone,
        "chat_url": chat_url,
        # Datos del contacto
        "nombre_contacto": datos.get("nombre_contacto"),
        "apellido_contacto": datos.get("apellido_contacto"),
        "empresa": datos.get("empresa"),
        "razon_social": datos.get("razon_social"),
        "ruc": datos.get("ruc"),
        "email": datos.get("email"),
        "telefono_adicional": datos.get("telefono_adicional"),
        "sector": datos.get("sector"),
        "equipo_interes": datos.get("equipo_interes"),
        "ubicacion_obra": datos.get("ubicacion_obra"),
        "urgencia": datos.get("urgencia"),
        "tipo_solicitud": datos.get("tipo_solicitud"),
        # Acciones CRM
        "crear_contacto": acciones.get("crear_contacto", False),
        "actualizar_contacto": acciones.get("actualizar_contacto", False),
        "crear_empresa": acciones.get("crear_empresa", False),
        "crear_deal": acciones.get("crear_deal", False),
        "etapa_deal": acciones.get("etapa_deal"),
        "crear_tarea": acciones.get("crear_tarea", False),
        "tarea_asunto": acciones.get("tarea_asunto"),
        "tarea_descripcion": acciones.get("tarea_descripcion"),
        "tarea_fecha_vencimiento": acciones.get("tarea_fecha_vencimiento"),
        "datos_pendientes": acciones.get("datos_pendientes", []),
        "resumen": datos.get("resumen"),
    }

    return payload


def send_to_make(payload):
    """Envía payload a Make.com webhook."""
    if not WEBHOOK_URL:
        print("[Make.com] WEBHOOK_URL no configurado — no se envía")
        return False

    try:
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            print(f"[Make.com] Enviado OK — {payload.get('nombre_contacto', '?')} / {payload.get('empresa', '?')}")
            return True
    except Exception as e:
        print(f"[Make.com] Error enviando: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# HTTP HANDLER
# ═══════════════════════════════════════════════════════════════
class ProxyHandler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CARPETA), **kwargs)

    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/landing.html")
            self.end_headers()
            return
        super().do_GET()

    def do_OPTIONS(self):
        self.send_cors()

    def do_POST(self):
        if self.path == "/api/chat":
            self.handle_chat()
        elif self.path == "/api/webhook":
            self.handle_webhook()
        elif self.path == "/api/timelinesai":
            self.handle_timelinesai()
        else:
            self.send_error(404)

    # ── PROXY A CLAUDE API (chatbot + CRM manual) ──────────────
    def handle_chat(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                self.send_cors(200)
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_data = e.read()
            self.send_cors(e.code)
            self.wfile.write(err_data)

    # ── WEBHOOK DIRECTO (Make/Zapier → HubSpot) ───────────────
    def handle_webhook(self):
        if not WEBHOOK_URL:
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": "WEBHOOK_URL no configurado"}).encode())
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Log para testing
        try:
            payload = json.loads(body)
            print(f"[Webhook] Payload: {json.dumps(payload, ensure_ascii=False)}")
            log_file = CARPETA / "webhook_logs.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.datetime.utcnow().isoformat(), "payload": payload}, ensure_ascii=False) + "\n")
        except Exception as log_err:
            print(f"[Webhook] Error logging: {log_err}")

        try:
            req = urllib.request.Request(
                WEBHOOK_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                resp.read()
                print("[Webhook] Enviado OK")
                self.send_cors(200)
                self.wfile.write(json.dumps({"ok": True}).encode())
        except urllib.error.HTTPError as e:
            print(f"[Webhook] Error: {e.code}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": f"Webhook error {e.code}"}).encode())
        except Exception as e:
            print(f"[Webhook] Error: {e}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "msg": str(e)}).encode())

    # ── TIMELINESAI WEBHOOK (automático) ───────────────────────
    def handle_timelinesai(self):
        """
        Recibe webhook outbound de TimelinesAI con mensajes agregados.
        Flujo: Validar → Deduplicar → Claude → Firestore → Make.com (si comercial)
        """
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            print("[TimelinesAI] Payload JSON inválido")
            self.send_cors(400)
            self.wfile.write(json.dumps({"ok": False, "error": "invalid_json"}).encode())
            return

        print(f"[TimelinesAI] Webhook recibido — chat: {payload.get('chat', {}).get('chat_id', '?')}")

        # ── 1. Extraer datos del payload ──
        chat = payload.get("chat", {})
        messages = payload.get("messages", [])
        chat_id = chat.get("chat_id")
        chat_url = chat.get("chat_url", "")
        contact_name = chat.get("full_name", "")
        is_group = chat.get("is_group", False)
        whatsapp_account = payload.get("whatsapp_account", "")

        # ── 2. Validaciones ──
        if not chat_id or not messages:
            print("[TimelinesAI] Payload sin chat_id o sin mensajes")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": True, "skipped": "no_chat_id_or_messages"}).encode())
            return

        if is_group:
            print(f"[TimelinesAI] Ignorando chat grupal: {chat_id}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": True, "skipped": "group_chat"}).encode())
            return

        # ── 3. Obtener max message_id del webhook ──
        msg_ids = [m.get("message_id", "") for m in messages]
        max_msg_id = max(msg_ids) if msg_ids else ""

        # ── 4. Check idempotencia (Firestore) ──
        state = db_get_chat_state(chat_id)
        if state and state.get("last_message_id", "") >= max_msg_id:
            print(f"[TimelinesAI] Chat {chat_id} ya procesado (msg {max_msg_id})")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": True, "skipped": "already_processed"}).encode())
            return

        # ── 5. Extraer teléfono del contacto ──
        contact_phone = ""
        for m in messages:
            if m.get("direction") == "incoming":
                contact_phone = m.get("sender", "")
                break
        if not contact_phone:
            # Si no hay incoming, intentar del primer mensaje
            contact_phone = messages[0].get("recipient", "") or messages[0].get("sender", "")

        # Teléfono del comercial (la cuenta de WhatsApp)
        comercial_phone = whatsapp_account
        comercial_name = ""  # TimelinesAI no envía el nombre del comercial en el webhook
        # Determinar quién es el comercial por el sender de outgoing
        for m in messages:
            if m.get("direction") == "outgoing":
                comercial_phone = m.get("sender", whatsapp_account)
                break

        # ── 6. Formatear conversación y llamar a Claude ──
        conv_text = format_messages_for_claude(messages, contact_name, contact_phone)

        if not conv_text.strip():
            print(f"[TimelinesAI] Chat {chat_id} sin texto útil")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": True, "skipped": "no_text"}).encode())
            return

        print(f"[TimelinesAI] Analizando {len(messages)} mensajes de {contact_name} ({contact_phone})")
        claude_result = call_claude_crm(conv_text)

        if not claude_result:
            # Reintentar una vez
            print("[TimelinesAI] Reintentando Claude...")
            claude_result = call_claude_crm(conv_text)

        if not claude_result:
            print(f"[TimelinesAI] Claude falló 2 veces para chat {chat_id}")
            self.send_cors(200)
            self.wfile.write(json.dumps({"ok": False, "error": "claude_failed"}).encode())
            return

        category = claude_result.get("categoria", "irrelevante")
        subcategory = claude_result.get("subcategoria", "")
        print(f"[TimelinesAI] Resultado: {category}/{subcategory} — {contact_name}")

        # ── 7. SIEMPRE guardar en Firestore (conversations) ──
        conv_data = {
            "chat_id": str(chat_id),
            "max_message_id": max_msg_id,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "comercial_phone": comercial_phone,
            "comercial_name": comercial_name,
            "messages": messages,
            "messages_count": len(messages),
            "category": category,
            "subcategory": subcategory,
            "claude_response": claude_result,
            "relevante_crm": category == "comercial",
            "sent_to_make": False,
            "chat_url": chat_url,
            "processed_at": datetime.datetime.utcnow().isoformat(),
        }

        sent_to_make = False

        # ── 8. Si es COMERCIAL → enviar a Make.com + guardar CRM record ──
        if category == "comercial":
            make_payload = build_make_payload(claude_result, contact_phone, chat_url)
            sent_to_make = send_to_make(make_payload)
            conv_data["sent_to_make"] = sent_to_make

            if sent_to_make:
                crm_data = {
                    "conversation_id": f"{chat_id}_{max_msg_id}",
                    "chat_id": str(chat_id),
                    "comercial_phone": comercial_phone,
                    "comercial_name": comercial_name,
                    **make_payload,
                    "created_at": datetime.datetime.utcnow().isoformat(),
                }
                db_save_crm_record(crm_data)

        # ── 9. Guardar conversación y actualizar estado ──
        db_save_conversation(conv_data)
        db_update_chat_state(chat_id, max_msg_id)
        db_update_stats(comercial_phone, comercial_name, claude_result)

        # ── 10. Responder OK ──
        result = {
            "ok": True,
            "chat_id": str(chat_id),
            "category": category,
            "subcategory": subcategory,
            "sent_to_make": sent_to_make,
            "messages_processed": len(messages),
        }
        print(f"[TimelinesAI] Procesado OK: {json.dumps(result, ensure_ascii=False)}")
        self.send_cors(200)
        self.wfile.write(json.dumps(result).encode())

    # ── CORS ───────────────────────────────────────────────────
    def send_cors(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else ""
        path = args[0].split()[1] if args else ""
        print(f"[{status}] {path}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Variable de entorno ANTHROPIC_API_KEY no configurada")
        print("  En Cloud Run: se configura como secreto")
        print("  En local:     export ANTHROPIC_API_KEY=sk-ant-...")
        exit(1)

    print(f"[Config] WEBHOOK_URL: {'configurado' if WEBHOOK_URL else 'NO configurado'}")
    print(f"[Config] TIMELINESAI_TOKEN: {'configurado' if TIMELINESAI_TOKEN else 'NO configurado'}")
    print(f"[Config] Firestore: {'conectado' if db else 'NO disponible'}")

    server = http.server.HTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"Proxy Maxim Domenech arrancado en puerto {PORT}")
    print(f"Endpoints: /api/chat, /api/webhook, /api/timelinesai")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Proxy parado.")
