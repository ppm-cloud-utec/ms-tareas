"""Publicacion de eventos de dominio al topic SNS compartido."""
import os
import json

import boto3

_sns = boto3.client("sns")
TOPIC = os.environ["SNS_TOPIC_ARN"]
ORIGEN = os.environ["SERVICIO"]


def publicar(tipo, tenant_id, datos, evento_id, ocurrido_en):
    """Publica un evento."""
    evento = {
        "evento_id": evento_id,
        "tipo": tipo,
        "version": 1,
        "tenant_id": tenant_id,
        "ocurrido_en": ocurrido_en,
        "origen": ORIGEN,
        "datos": datos,
    }
    _sns.publish(
        TopicArn=TOPIC,
        Message=json.dumps(evento, ensure_ascii=False, default=str),
        MessageAttributes={
            "tipo": {"DataType": "String", "StringValue": tipo},
            "tenant_id": {"DataType": "String", "StringValue": tenant_id},
            "origen": {"DataType": "String", "StringValue": ORIGEN},
        },
    )
    return evento["evento_id"]
