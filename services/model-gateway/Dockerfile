# Canvas Marketing OS — model-gateway container image.
#
# Production interpreter pin lives here (python:3.12-slim), not in
# pyproject.toml's requires-python: this image is what actually runs in
# Azure Container Apps, so 3.12 is enforced at the layer that matters.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Matches infra/modules/gateway.bicep's ingress.targetPort.
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
