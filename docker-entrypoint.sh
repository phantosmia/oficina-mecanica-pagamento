#!/bin/bash
set -e

# Mesma imagem para os quatro papéis do serviço:
#   docker-entrypoint.sh            -> API (uvicorn)
#   docker-entrypoint.sh worker     -> consumidor da fila de comandos da saga + reconciliação com o Mercado Pago
#   docker-entrypoint.sh relay      -> relay da outbox (publica eventos no SNS)
#   docker-entrypoint.sh migrate    -> aplica as migrations e sai (Job no Kubernetes)
ROLE="${1:-api}"

if [ "$ROLE" = "migrate" ]; then
    echo "Aplicando migrations..."
    alembic upgrade head
    exit 0
fi

# Só no docker-compose (LocalStack): na AWS, fila e tópico vêm do Terraform.
if [ "${BOOTSTRAP_LOCAL_RESOURCES:-false}" = "true" ]; then
    echo "Criando fila e tópico locais..."
    python -m scripts.bootstrap_local
fi

# Agente APM do New Relic (ADR-0007) só quando a license key estiver definida.
RUNNER=()
if [ -n "${NEW_RELIC_LICENSE_KEY:-}" ]; then
    RUNNER=(newrelic-admin run-program)
fi

case "$ROLE" in
    worker)
        echo "Iniciando worker (fila de comandos + reconciliação)..."
        exec "${RUNNER[@]}" python -m app.worker
        ;;
    relay)
        echo "Iniciando relay da outbox..."
        exec "${RUNNER[@]}" python -m app.outbox_relay
        ;;
    *)
        echo "Iniciando API do Pagamento..."
        exec "${RUNNER[@]}" uvicorn app.main:app --host 0.0.0.0 --port 8000
        ;;
esac
