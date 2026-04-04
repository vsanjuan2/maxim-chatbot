FROM python:3.12-slim

WORKDIR /app

# Copiar archivos de la app
COPY proxy.py index.html prototipo-crm.html ./

# Puerto dinámico de Cloud Run
ENV PORT=8080
EXPOSE 8080

# Ejecutar el proxy
CMD ["python3", "proxy.py"]
