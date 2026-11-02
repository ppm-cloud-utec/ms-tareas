"""Tareas e hitos. Al completarse, publican el avance del proyecto.

Este es el microservicio que hace visible el EDA en la demo: completar una
tarea aqui cambia el porcentaje de avance en ms-portafolios sin que el
frontend llame a ms-portafolios ni los dos microservicios se conozcan.
"""
import uuid
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src import db, roles
from src.eventos import publicar
from src.respuesta import cuerpo_de, error, ok, tenant_de, usuario_de

ESTADOS = ("pendiente", "en_progreso", "completada", "bloqueada", "cancelada")
TIPOS = ("tarea", "hito")


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def _contadores(tenant_id, proyecto_id, delta_total=0, delta_completadas=0):
    """Actualiza los contadores del proyecto de forma atomica.

    ADD sobre DynamoDB es atomico: dos personas completando tareas a la vez no
    se pisan. Devuelve los valores ya actualizados, que son los que viajan en
    el evento.
    """
    r = db.tabla().update_item(
        Key={"pk": db.pk(tenant_id), "sk": db.sk_resumen(proyecto_id)},
        # "total" es palabra reservada en DynamoDB: hay que aliasarla con #.
        UpdateExpression=(
            "ADD #total :t, completadas :c "
            "SET proyecto_id = :p, tenant_id = :tid, actualizado_en = :a"
        ),
        ExpressionAttributeNames={"#total": "total"},
        ExpressionAttributeValues={
            ":t": delta_total,
            ":c": delta_completadas,
            ":p": str(proyecto_id),
            ":tid": tenant_id,
            ":a": _ahora(),
        },
        ReturnValues="ALL_NEW",
    )
    item = r["Attributes"]
    return int(item.get("total", 0)), int(item.get("completadas", 0))


def listar(event, context):
    """GET /proyectos/{proyecto_id}/tareas?estado=&limite="""
    tenant_id = tenant_de(event)
    proyecto_id = event["pathParameters"]["proyecto_id"]
    q = event.get("queryStringParameters") or {}
    limite = min(int(q.get("limite", 100)), 300)

    args = {
        "KeyConditionExpression": Key("pk").eq(db.pk(tenant_id))
        & Key("sk").begins_with(db.prefijo_tareas(proyecto_id)),
        "Limit": limite,
    }
    if q.get("estado"):
        args["FilterExpression"] = "estado = :e"
        args["ExpressionAttributeValues"] = {":e": q["estado"]}

    r = db.tabla().query(**args)
    tareas = [{k: v for k, v in t.items() if k not in ("pk", "sk")}
              for t in r.get("Items", [])]

    meta = {"total": r["Count"], "proyecto_id": proyecto_id}
    if r.get("LastEvaluatedKey"):
        meta["cursor"] = r["LastEvaluatedKey"]["sk"]
    return ok(tareas, meta=meta)


def mias(event, context):
    """GET /mis-tareas — usa el GSI por responsable.

    Un miembro de equipo no deberia recorrer los 500 proyectos del tenant para
    encontrar sus 8 tareas.
    """
    tenant_id = tenant_de(event)
    q = event.get("queryStringParameters") or {}
    responsable = q.get("responsable_id") or usuario_de(event)

    r = db.tabla().query(
        IndexName="ix-responsable",
        KeyConditionExpression=Key("gsi1pk").eq(
            db.gsi_responsable(tenant_id, responsable)),
        Limit=min(int(q.get("limite", 100)), 300),
    )
    tareas = [{k: v for k, v in t.items()
               if k not in ("pk", "sk", "gsi1pk", "gsi1sk")}
              for t in r.get("Items", [])]
    return ok(tareas, meta={"total": r["Count"], "responsable_id": responsable})


def obtener(event, context):
    """GET /proyectos/{proyecto_id}/tareas/{tarea_id}"""
    p = event["pathParameters"]
    item = db.tabla().get_item(Key={
        "pk": db.pk(tenant_de(event)),
        "sk": db.sk_tarea(p["proyecto_id"], p["tarea_id"]),
    }).get("Item")

    if not item:
        return error("NO_ENCONTRADO", f"No existe la tarea {p['tarea_id']}", 404)
    for c in ("pk", "sk", "gsi1pk", "gsi1sk"):
        item.pop(c, None)
    return ok(item)


def crear(event, context):
    """POST /proyectos/{proyecto_id}/tareas — publica tarea.creada."""
    negado = roles.exige(event, roles.PM, roles.PMO, roles.ADMIN)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    proyecto_id = event["pathParameters"]["proyecto_id"]
    c = cuerpo_de(event)

    if not c.get("nombre"):
        return error("DATOS_INCOMPLETOS", "Falta el nombre de la tarea")
    tipo = c.get("tipo", "tarea")
    if tipo not in TIPOS:
        return error("TIPO_INVALIDO", f"Tipos validos: {', '.join(TIPOS)}")

    tarea_id = str(uuid.uuid4())
    responsable = c.get("responsable_id")
    item = {
        "pk": db.pk(tenant_id),
        "sk": db.sk_tarea(proyecto_id, tarea_id),
        "tarea_id": tarea_id,
        "proyecto_id": str(proyecto_id),
        "tenant_id": tenant_id,
        "nombre": c["nombre"].strip(),
        "descripcion": c.get("descripcion", ""),
        "tipo": tipo,
        "estado": "pendiente",
        "responsable_id": responsable,
        "fecha_inicio": c.get("fecha_inicio"),
        "fecha_fin": c.get("fecha_fin"),
        "horas_estimadas": c.get("horas_estimadas", 0),
        # Los ids de las tareas de las que depende. Es una lista, no FK:
        # DynamoDB no valida referencias y no hace falta para el caso de uso.
        "depende_de": c.get("depende_de", []),
        "creado_en": _ahora(),
        "creado_por": usuario_de(event),
    }
    if responsable:
        item["gsi1pk"] = db.gsi_responsable(tenant_id, responsable)
        item["gsi1sk"] = f"pendiente#{c.get('fecha_fin') or '9999-12-31'}"

    db.tabla().put_item(Item=item)
    total, completadas = _contadores(tenant_id, proyecto_id, delta_total=1)

    publicar("tarea.creada", tenant_id, {
        # El contrato dice entero: los consumidores lo usan contra columnas
        # BIGINT. Dentro de DynamoDB se guarda como cadena, pero eso es un
        # detalle de ESTE microservicio y no debe filtrarse al evento.
        "proyecto_id": int(proyecto_id),
        "tarea_id": tarea_id,
        "tipo": tipo,
        # Los dos contadores completos: el worker no acumula, reemplaza.
        "total_tareas": total,
        "tareas_completadas": completadas,
    })

    for c_ in ("pk", "sk", "gsi1pk", "gsi1sk"):
        item.pop(c_, None)
    return ok(item, estado=201)


def completar(event, context):
    """POST /proyectos/{proyecto_id}/tareas/{tarea_id}/completar

    EL MOMENTO CLAVE DE LA DEMO: esto recalcula el avance del proyecto en
    ms-portafolios por evento, sin que nadie llame a nadie.
    """
    tenant_id = tenant_de(event)
    p = event["pathParameters"]
    proyecto_id, tarea_id = p["proyecto_id"], p["tarea_id"]

    try:
        # La condicion evita contar dos veces la misma tarea si el usuario
        # hace doble clic: solo pasa si NO estaba ya completada.
        r = db.tabla().update_item(
            Key={"pk": db.pk(tenant_id), "sk": db.sk_tarea(proyecto_id, tarea_id)},
            UpdateExpression=(
                "SET estado = :completada, completado_en = :ahora, "
                "completado_por = :quien"
            ),
            ConditionExpression=(
                "attribute_exists(sk) AND estado <> :completada"
            ),
            ExpressionAttributeValues={
                ":completada": "completada",
                ":ahora": _ahora(),
                ":quien": usuario_de(event),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Puede ser que no exista o que ya estuviera completada: hay que mirar.
        existe = db.tabla().get_item(Key={
            "pk": db.pk(tenant_id),
            "sk": db.sk_tarea(proyecto_id, tarea_id),
        }).get("Item")
        if not existe:
            return error("NO_ENCONTRADO", f"No existe la tarea {tarea_id}", 404)
        return error("YA_COMPLETADA", "La tarea ya estaba completada", 409)

    tarea = r["Attributes"]
    total, completadas = _contadores(tenant_id, proyecto_id, delta_completadas=1)
    avance = round(100.0 * completadas / total, 2) if total else 0.0

    publicar("tarea.completada", tenant_id, {
        "proyecto_id": int(proyecto_id),
        "tarea_id": tarea_id,
        "nombre": tarea["nombre"],
        "tipo": tarea.get("tipo", "tarea"),
        "total_tareas": total,
        "tareas_completadas": completadas,
        "avance_pct": avance,
    })

    for c in ("pk", "sk", "gsi1pk", "gsi1sk"):
        tarea.pop(c, None)
    return ok({
        "tarea": tarea,
        "proyecto": {
            "proyecto_id": str(proyecto_id),
            "total_tareas": total,
            "tareas_completadas": completadas,
            "avance_pct": avance,
        },
    })


def actualizar(event, context):
    """PUT /proyectos/{proyecto_id}/tareas/{tarea_id}"""
    tenant_id = tenant_de(event)
    p = event["pathParameters"]
    c = cuerpo_de(event)

    permitidos = ("nombre", "descripcion", "estado", "responsable_id",
                  "fecha_inicio", "fecha_fin", "horas_estimadas", "depende_de")
    cambios = {k: c[k] for k in permitidos if k in c}
    if not cambios:
        return error("SIN_CAMBIOS", f"Envia alguno de: {', '.join(permitidos)}")
    if cambios.get("estado") == "completada":
        return error("USA_COMPLETAR",
                     "Para completar usa POST /tareas/{id}/completar, "
                     "que publica el evento y recalcula el avance")
    if "estado" in cambios and cambios["estado"] not in ESTADOS:
        return error("ESTADO_INVALIDO", f"Estados validos: {', '.join(ESTADOS)}")

    expr = ", ".join(f"{k} = :{k}" for k in cambios)
    valores = {f":{k}": v for k, v in cambios.items()}

    # Si cambia el responsable hay que mover la clave del GSI
    if "responsable_id" in cambios and cambios["responsable_id"]:
        expr += ", gsi1pk = :g"
        valores[":g"] = db.gsi_responsable(tenant_id, cambios["responsable_id"])

    try:
        r = db.tabla().update_item(
            Key={"pk": db.pk(tenant_id),
                 "sk": db.sk_tarea(p["proyecto_id"], p["tarea_id"])},
            UpdateExpression=f"SET {expr}",
            ExpressionAttributeValues=valores,
            ConditionExpression="attribute_exists(sk)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return error("NO_ENCONTRADO", f"No existe la tarea {p['tarea_id']}", 404)
        raise

    tarea = r["Attributes"]
    for k in ("pk", "sk", "gsi1pk", "gsi1sk"):
        tarea.pop(k, None)
    return ok(tarea)


def resumen(event, context):
    """GET /proyectos/{proyecto_id}/resumen — los contadores y el avance."""
    tenant_id = tenant_de(event)
    proyecto_id = event["pathParameters"]["proyecto_id"]

    item = db.tabla().get_item(Key={
        "pk": db.pk(tenant_id), "sk": db.sk_resumen(proyecto_id)
    }).get("Item")

    if not item:
        return ok({"proyecto_id": proyecto_id, "total_tareas": 0,
                   "tareas_completadas": 0, "avance_pct": 0.0})

    total = int(item.get("total", 0))
    completadas = int(item.get("completadas", 0))
    return ok({
        "proyecto_id": proyecto_id,
        "total_tareas": total,
        "tareas_completadas": completadas,
        "avance_pct": round(100.0 * completadas / total, 2) if total else 0.0,
        "actualizado_en": item.get("actualizado_en"),
    })
