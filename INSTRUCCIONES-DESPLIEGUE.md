# Desplegar Chatbot Maxim Domenech en Google Cloud Run

## Coste estimado
Con menos de 50 visitas/dia, el coste sera **$0 - $2/mes** gracias a la capa gratuita de Cloud Run (2 millones de requests/mes gratis). Solo pagas el uso de la API de Anthropic por cada conversacion.

## Requisitos previos

1. Una cuenta de Google Cloud (puedes crearla gratis en https://cloud.google.com)
2. Tu API key de Anthropic (https://console.anthropic.com/settings/keys)
3. Google Cloud CLI instalado en tu ordenador

---

## Paso 1: Instalar Google Cloud CLI

### macOS
```bash
brew install google-cloud-sdk
```

### Windows
Descarga el instalador desde: https://cloud.google.com/sdk/docs/install

### Verificar instalacion
```bash
gcloud --version
```

---

## Paso 2: Configurar proyecto en Google Cloud

```bash
# Iniciar sesion
gcloud auth login

# Crear un proyecto nuevo (o usar uno existente)
gcloud projects create maxim-domenech-chatbot --name="Chatbot Maxim Domenech"

# Seleccionar el proyecto
gcloud config set project maxim-domenech-chatbot

# Habilitar los servicios necesarios
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

---

## Paso 3: Guardar la API key como secreto seguro

```bash
# Crear el secreto (te pedira que escribas la clave)
echo -n "sk-ant-TU-CLAVE-AQUI" | gcloud secrets create anthropic-api-key --data-file=-

# Dar acceso a Cloud Run para leer el secreto
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Paso 4: Desplegar

Desde la carpeta `cloud-run/` donde estan los archivos:

```bash
cd cloud-run

# Construir y desplegar en un solo comando
gcloud run deploy maxim-chatbot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets=ANTHROPIC_API_KEY=anthropic-api-key:latest \
  --memory 256Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --timeout 60
```

Cloud Run te dara una URL tipo:
```
https://maxim-chatbot-XXXXX-uc.a.run.app
```

**Esa es tu web, ya funcionando.**

---

## Paso 5: (Opcional) Conectar dominio propio

Si quieres usar algo como `chat.maximdomenech.pe`:

```bash
gcloud run domain-mappings create \
  --service maxim-chatbot \
  --domain chat.maximdomenech.pe \
  --region us-central1
```

Luego anade el registro CNAME que te indique en el DNS de tu dominio.

---

## Probar en local antes de desplegar

Si quieres probar en tu Mac antes de subir a la nube:

```bash
# Opcion A: Sin Docker (igual que antes)
export ANTHROPIC_API_KEY="sk-ant-tu-clave"
cd cloud-run
python3 proxy.py
# Abre http://localhost:8080

# Opcion B: Con Docker (simula Cloud Run)
docker build -t maxim-chatbot .
docker run -p 8080:8080 -e ANTHROPIC_API_KEY="sk-ant-tu-clave" maxim-chatbot
# Abre http://localhost:8080
```

---

## Actualizar la app

Cuando hagas cambios al HTML o al proxy, simplemente vuelve a ejecutar:

```bash
gcloud run deploy maxim-chatbot --source . --region us-central1
```

---

## Resumen de archivos

| Archivo | Funcion |
|---|---|
| `proxy.py` | Servidor Python que sirve el HTML y proxea las llamadas a la API de Anthropic |
| `index.html` | Frontend del chatbot (antes maxim-domenech-v4.html) |
| `prototipo-crm.html` | Prototipo CRM: analiza conversaciones de WhatsApp con IA y muestra que registros crear en HubSpot |
| `Dockerfile` | Define el contenedor para Cloud Run |
| `.dockerignore` | Excluye archivos innecesarios del contenedor |

## URLs de la aplicacion

Una vez desplegada (local o Cloud Run), tienes dos paginas:

| URL | Descripcion |
|---|---|
| `/` o `/index.html` | Chatbot asesor de maquinaria (cara al cliente) |
| `/prototipo-crm.html` | Prototipo CRM con IA (herramienta interna para validar el flujo TimelinesAI → IA → HubSpot) |
