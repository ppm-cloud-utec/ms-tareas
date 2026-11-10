"""Acceso a DynamoDB. Single-table design con aislamiento por particion.

Claves:
  Tarea    pk = "TENANT#<tid>"  sk = "PROYECTO#<pid>#TAREA#<tarea_id>"
  Resumen  pk = "TENANT#<tid>"  sk = "PROYECTO#<pid>#RESUMEN"

El sk compuesto permite traer todas las tareas de un proyecto con un
begins_with, sin indices ni scans.

El item de RESUMEN lleva contadores atomicos (total y completadas). Es lo que
hace posible publicar el avance correcto en el evento: si el worker de
ms-portafolios tuviera que acumular +1 por su cuenta, un evento perdido o
repetido desviaria el porcentaje para siempre. Aqui el numero viaja completo.
"""
import os
import random
import time

import boto3
from botocore.exceptions import ClientError

_dynamo = boto3.resource("dynamodb")


def tabla():
    return _dynamo.Table(os.environ["TABLA"])


def pk(tenant_id):
    """La particion SIEMPRE empieza con el tenant: una query no puede cruzarlo."""
    return f"TENANT#{tenant_id}"


def sk_tarea(proyecto_id, tarea_id):
    return f"PROYECTO#{proyecto_id}#TAREA#{tarea_id}"


def sk_resumen(proyecto_id):
    return f"PROYECTO#{proyecto_id}#RESUMEN"


def prefijo_tareas(proyecto_id):
    return f"PROYECTO#{proyecto_id}#TAREA#"


def gsi_responsable(tenant_id, responsable_id):
    """Clave del GSI 'mis tareas'. Un miembro necesita ver lo suyo sin recorrer
    todos los proyectos del tenant.
    """
    return f"TENANT#{tenant_id}#RESPONSABLE#{responsable_id}"


def leer_resumen(tabla, tenant_id, proyecto_id):
    return tabla.get_item(
        Key={"pk": pk(tenant_id), "sk": sk_resumen(proyecto_id)},
        ConsistentRead=True,
    ).get("Item", {})


def escribir_con_resumen(tabla, operacion, tenant_id, proyecto_id, ahora,
                         delta_total=0, delta_completadas=0):
    """Confirma la tarea y los contadores en una sola transacción."""
    tipo, parametros = next(iter(operacion.items()))
    operaciones = [
        {tipo: {"TableName": tabla.name, **parametros}},
        {"Update": {
            "TableName": tabla.name,
            "Key": {"pk": pk(tenant_id), "sk": sk_resumen(proyecto_id)},
            "UpdateExpression": (
                "SET proyecto_id = :p, tenant_id = :tid, actualizado_en = :a "
                "ADD #total :t, completadas :c"
            ),
            "ExpressionAttributeNames": {"#total": "total"},
            "ExpressionAttributeValues": {
                ":p": proyecto_id, ":tid": tenant_id, ":a": ahora,
                ":t": delta_total, ":c": delta_completadas,
            },
        }},
    ]
    for intento in range(4):
        try:
            return tabla.meta.client.transact_write_items(TransactItems=operaciones)
        except ClientError as exc:
            razones = exc.response.get("CancellationReasons") or []
            codigos = {r["Code"] for r in razones}
            if (intento == 3 or "TransactionConflict" not in codigos
                    or codigos - {"None", "TransactionConflict"}):
                raise
            time.sleep(random.uniform(0.01, 0.03) * 2**intento)
