# Setup: Integración TimelinesAI → Claude → HubSpot

## Requisitos previos
- Cloud Run ya desplegado (maxim-chatbot)
- Make.com webhook funcionando (WEBHOOK_URL configurado)
- Cuenta TimelinesAI con WhatsApp conectado

---

## Paso 1: Habilitar Firestore en GCP

```bash
# Habilitar la API de Firestore
gcloud services enable firestore.googleapis.com \
  --project=delivery95558

# Crear la base de datos Firestore (modo Native, misma región que Cloud Run)
gcloud firestore databases create \
  --project=delivery95558 \
  --location=us-central1
```

> **Nota:** El service account de Cloud Run ya tiene permisos de Editor,
> que incluyen acceso completo a Firestore. No necesitas configurar
> credenciales adicionales.

---

## Paso 2: Añadir variable de entorno TIMELINESAI_TOKEN

```bash
gcloud run services update maxim-chatbot \
  --region us-central1 \
  --project delivery95558 \
  --update-env-vars="TIMELINESAI_TOKEN=a66d262c-6604-4484-8faa-d3be18cef733"
```

> **Recuerda:** También verifica que WEBHOOK_URL sigue configurado:
> ```bash
> gcloud run services describe maxim-chatbot \
>   --region us-central1 \
>   --project delivery95558 \
>   --format="value(spec.template.spec.containers[0].env)"
> ```

---

## Paso 3: Deploy del código actualizado

Desde tu terminal local, en el directorio `cloud-run/`:

```bash
# Verificar que no hay lock files de git
find .git -name "*.lock" -delete 2>/dev/null

# Commit y push
git add proxy.py requirements.txt Dockerfile
git commit -m "feat: integración automática TimelinesAI → Claude → Firestore → HubSpot"
git push origin main
```

Cloud Build detecta el push y despliega automáticamente.

**Después del deploy**, re-configura WEBHOOK_URL si se perdió:
```bash
gcloud run services update maxim-chatbot \
  --region us-central1 \
  --project delivery95558 \
  --update-env-vars="WEBHOOK_URL=https://hook.eu1.make.com/xu29dl5zjc4qce97dwcmgt3rp3tys61d,TIMELINESAI_TOKEN=a66d262c-6604-4484-8faa-d3be18cef733"
```

---

## Paso 4: Obtener la URL del endpoint

```bash
gcloud run services describe maxim-chatbot \
  --region us-central1 \
  --project delivery95558 \
  --format="value(status.url)"
```

La URL será algo como: `https://maxim-chatbot-XXXXX-uc.a.run.app`

Tu endpoint de webhook es: `https://maxim-chatbot-XXXXX-uc.a.run.app/api/timelinesai`

---

## Paso 5: Configurar webhook en TimelinesAI

1. Ir a https://app.timelines.ai/integrations/api/
2. Sección **Outbound Webhooks**
3. Crear nuevo webhook:
   - **URL:** `https://maxim-chatbot-XXXXX-uc.a.run.app/api/timelinesai`
   - **Eventos:** Messages (outbound webhook)
   - **Agregación:** 15 minutos de inactividad
4. Guardar y activar

---

## Paso 6: Test

### Test rápido con curl
```bash
# Simular un webhook de TimelinesAI
curl -X POST https://maxim-chatbot-XXXXX-uc.a.run.app/api/timelinesai \
  -H "Content-Type: application/json" \
  -d '{
    "whatsapp_account": "51999888777",
    "chat": {
      "chat_id": 99999,
      "chat_url": "https://app.timelines.ai/chat/99999",
      "full_name": "Test Cliente",
      "is_new_chat": true,
      "is_group": false
    },
    "messages": [
      {
        "direction": "incoming",
        "timestamp": 1712500000,
        "message_id": "test_msg_001",
        "sender": "51999111222",
        "recipient": "51999888777",
        "text": "Hola, necesito cotizar una plataforma tijera de 12 metros para una obra en San Isidro, Lima. Es para la proxima semana."
      },
      {
        "direction": "outgoing",
        "timestamp": 1712500060,
        "message_id": "test_msg_002",
        "sender": "51999888777",
        "recipient": "51999111222",
        "text": "Hola! Claro, te preparo la cotización. Es tijera eléctrica o diésel?"
      },
      {
        "direction": "incoming",
        "timestamp": 1712500120,
        "message_id": "test_msg_003",
        "sender": "51999111222",
        "recipient": "51999888777",
        "text": "Eléctrica por favor. Mi nombre es Carlos Ramírez, de Constructora Vida."
      }
    ]
  }'
```

### Verificar en Firestore
1. GCP Console → Firestore → Data
2. Verificar colecciones: `conversations`, `crm_records`, `chat_state`, `stats_monthly`, `stats_global`

### Verificar en Make.com
- El escenario debe haber recibido un webhook con `origen: "whatsapp-auto"`

### Verificar en Cloud Run logs
```bash
gcloud run services logs read maxim-chatbot \
  --region us-central1 \
  --project delivery95558 \
  --limit 50
```

---

## Colecciones Firestore creadas automáticamente

| Colección | Propósito |
|-----------|-----------|
| `conversations` | Todas las conversaciones procesadas (comercial + coordinación + interno) |
| `crm_records` | Copia de registros enviados a Make.com → HubSpot |
| `chat_state` | Último message_id procesado por chat (idempotencia) |
| `stats_monthly` | Contadores por comercial y mes |
| `stats_global` | KPIs generales |

---

## Troubleshooting

### "Firestore no disponible"
- Verificar que `firestore.googleapis.com` está habilitado
- Verificar que la database está creada en `us-central1`

### Webhooks duplicados
- `chat_state` previene reprocesamiento. Si ves duplicados en logs, verificar que Firestore está conectado.

### Make.com no recibe datos
- Verificar WEBHOOK_URL en env vars de Cloud Run
- Solo conversaciones clasificadas como "comercial" se envían a Make.com
- Revisar logs: `[Make.com] Enviado OK` o `[Make.com] Error`

### Claude devuelve JSON inválido
- Se reintenta 1 vez automáticamente
- Si falla 2 veces, se loguea el error y se devuelve `claude_failed`
- Logs: `[Claude CRM] JSON inválido`
