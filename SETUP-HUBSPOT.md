# Integración Chatbot → Make → HubSpot

## Resumen del flujo

```
Cliente completa formulario → Chatbot envía datos al proxy
→ Proxy reenvía a webhook de Make → Make crea en HubSpot:
  - Contacto (busca si existe por email, si no lo crea)
  - Empresa (busca si existe por nombre, si no la crea)
  - Deal/Negocio (siempre crea uno nuevo, asociado al contacto y empresa)
```

## 1. Crear cuenta en Make (gratis)

1. Ve a https://www.make.com/
2. Regístrate con tu email
3. Plan gratuito = 1000 operaciones/mes (suficiente para empezar)

## 2. Crear el escenario en Make

### Paso 1: Webhook (trigger)
1. Crea un nuevo escenario
2. Añade módulo: **Webhooks → Custom webhook**
3. Haz clic en "Add" para crear un nuevo webhook
4. Ponle nombre: "Chatbot Maxim Domenech"
5. **Copia la URL del webhook** (la necesitarás luego)
6. Haz clic en "Redetermine data structure" y envía un test desde el chatbot (o pega este JSON de ejemplo):

```json
{
  "nombre": "Juan",
  "apellido": "Pérez",
  "email": "juan@empresa.com",
  "telefono": "+51999999999",
  "empresa": "Empresa Test",
  "razon_social": "Empresa Test S.A.C.",
  "ruc": "20123456789",
  "sector": "Construcción",
  "equipo_recomendado": "Plataforma Tijera 12m Eléctrica",
  "direccion_obra": "Av. Javier Prado 123, San Isidro, Lima",
  "ciudad_obra": "Lima",
  "distrito_obra": "San Isidro",
  "resumen_conversacion": "Necesita equipo para mantenimiento interior a 12m...",
  "fecha": "2026-04-03T15:30:00.000Z"
}
```

### Paso 2: Buscar contacto en HubSpot
1. Añade módulo: **HubSpot CRM → Search Contacts**
2. Conecta tu cuenta de HubSpot
3. Busca por email: `{{1.email}}`

### Paso 3: Router (bifurcación)
1. Añade un **Router** para separar:
   - **Ruta A**: Si el contacto existe → actualizar
   - **Ruta B**: Si no existe → crear nuevo

### Paso 4: Crear o actualizar contacto
- **Crear**: HubSpot CRM → Create Contact
  - First name: `{{1.nombre}}`
  - Last name: `{{1.apellido}}`
  - Email: `{{1.email}}`
  - Phone: `{{1.telefono}}`
  - Company: `{{1.razon_social}}`

- **Actualizar**: HubSpot CRM → Update Contact
  - Mismos campos

### Paso 5: Buscar/crear empresa
1. **HubSpot CRM → Search Companies** por nombre: `{{1.razon_social}}`
2. Si no existe: **HubSpot CRM → Create Company**
   - Name: `{{1.razon_social}}`
   - City: `{{1.ciudad_obra}}`
   - Industry: `{{1.sector}}`

### Paso 6: Crear deal (negocio)
1. **HubSpot CRM → Create Deal**
   - Deal name: `Alquiler {{1.equipo_recomendado}} - {{1.razon_social}}`
   - Description: `Equipo: {{1.equipo_recomendado}} | Obra: {{1.direccion_obra}} | {{1.resumen_conversacion}}`
   - Pipeline: default
   - Stage: primera etapa

### Paso 7: Asociar
1. **HubSpot CRM → Associate** deal con contacto
2. **HubSpot CRM → Associate** deal con empresa

### Paso 8: Crear tarea para el comercial
1. **HubSpot CRM → Create Task**
   - Subject: `Cotizar {{1.equipo_recomendado}} - {{1.razon_social}}`
   - Body/Notes: `Enviar cotización al cliente. Equipo: {{1.equipo_recomendado}} | Obra: {{1.direccion_obra}} | Contacto: {{1.nombre}} {{1.apellido}} ({{1.email}})`
   - Due date: día siguiente a la fecha de la solicitud (`{{1.fecha}}` + 1 día)
   - Priority: HIGH
   - Status: NOT_STARTED
   - Type: TODO
2. **HubSpot CRM → Associate** tarea con contacto
3. **HubSpot CRM → Associate** tarea con empresa
4. **HubSpot CRM → Associate** tarea con deal

## 3. Configurar el webhook en el chatbot

### En local:
```bash
export ANTHROPIC_API_KEY=sk-ant-tu-clave
export WEBHOOK_URL=https://hook.make.com/tu-webhook-id-aquí
python3 proxy.py
```

### En Cloud Run:
```bash
gcloud run deploy maxim-chatbot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-key:latest" \
  --set-env-vars="WEBHOOK_URL=https://hook.make.com/tu-webhook-id-aquí"
```

## 4. Verificación de duplicados

La verificación de duplicados la hace **Make**, no el chatbot:
- Busca el contacto por email antes de crear uno nuevo
- Busca la empresa por nombre (razón social) antes de crear una nueva
- Solo los deals se crean siempre nuevos (cada solicitud es una oportunidad diferente)

## 5. Sin webhook configurado

Si `WEBHOOK_URL` no está configurado, el chatbot funciona normalmente:
- WhatsApp con resumen sigue funcionando
- El formulario de cotización sigue funcionando
- Solo no se envían datos a Make/HubSpot (aparece un aviso en la consola)
