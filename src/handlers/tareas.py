"""API de tareas e hitos, con aislamiento por tenant y avance por proyecto."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import DYNAMODB_CONTEXT
from botocore.exceptions import ClientError

from src import db, roles
from src.respuesta import cuerpo_de, error, ok, rol_de, tenant_de, usuario_de

ESTADOS = ("pendiente", "en_progreso", "completada", "bloqueada", "cancelada")
TIPOS = ("tarea", "hito")
GESTORES = (roles.PM, roles.PMO, roles.ADMIN)
CAMPOS = ("nombre", "descripcion", "estado", "responsable_id",
          "fecha_inicio", "fecha_fin", "horas_estimadas", "depende_de")
INTERNOS = {"pk", "sk", "gsi1pk", "gsi1sk", "revision", "evento_id"}


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def _publico(item):
    return {k: v for k, v in item.items() if k not in INTERNOS}


def _id_proyecto_valido(proyecto_id):
    return (len(proyecto_id) <= 19 and proyecto_id.isascii() and proyecto_id.isdecimal()
            and 0 < int(proyecto_id) <= 2**63 - 1)


def _limite(q):
    try:
        limite = int(q.get("limite", 100))
    except (TypeError, ValueError):
        return None
    return min(limite, 300) if limite > 0 else None


def _validar(cambios):
    """Valida los campos antes de construir expresiones de DynamoDB."""
    nombre = cambios.get("nombre")
    if "nombre" in cambios and (not isinstance(nombre, str) or not nombre.strip()):
        return error("NOMBRE_INVALIDO", "El nombre debe ser un texto no vacío")
    if "descripcion" in cambios and not isinstance(cambios["descripcion"], str):
        return error("DESCRIPCION_INVALIDA", "La descripción debe ser texto")

    responsable = cambios.get("responsable_id")
    if responsable is not None and (
        not isinstance(responsable, str) or not responsable.strip()
        or len(responsable.encode("utf-8")) > 512
    ):
        return error("RESPONSABLE_INVALIDO", "Envía el identificador del responsable o null")

    for campo in ("fecha_inicio", "fecha_fin"):
        valor = cambios.get(campo)
        if valor in (None, ""):
            continue
        try:
            if date.fromisoformat(valor).isoformat() != valor:
                raise ValueError
        except (TypeError, ValueError):
            return error("FECHA_INVALIDA", f"{campo} debe tener el formato AAAA-MM-DD")

    if "horas_estimadas" in cambios:
        horas = cambios["horas_estimadas"]
        try:
            if (type(horas) not in (int, Decimal)
                    or not DYNAMODB_CONTEXT.create_decimal(horas).is_finite() or horas < 0):
                raise ValueError
        except (DecimalException, ValueError):
            return error("HORAS_INVALIDAS", "Las horas deben ser un número no negativo")

    if "depende_de" in cambios:
        deps = cambios["depende_de"]
        if not isinstance(deps, list) or any(not isinstance(x, str) or not x for x in deps):
            return error("DEPENDENCIAS_INVALIDAS", "depende_de debe ser una lista de IDs")


def _leer_tarea(tabla, tenant_id, proyecto_id, tarea_id):
    return tabla.get_item(
        Key={"pk": db.pk(tenant_id), "sk": db.sk_tarea(proyecto_id, tarea_id)},
        ConsistentRead=True,
    ).get("Item")


def _no_encontrada(tarea_id):
    return error("NO_ENCONTRADO", f"No existe la tarea {tarea_id}", 404)


def _cambio_tarea(tarea, cambios):
    """Escribe una revisión y actualiza el índice en la misma operación."""
    actualizado = tarea | cambios
    revision = tarea.get("revision")
    campos = cambios | {"revision": (revision or 0) + 1}
    responsable = actualizado.get("responsable_id")
    if responsable:
        campos["gsi1pk"] = db.gsi_responsable(tarea["tenant_id"], responsable)
        campos["gsi1sk"] = f"{actualizado['estado']}#{actualizado.get('fecha_fin') or '9999-12-31'}"

    nombres = {f"#{k}": k for k in campos}
    valores = {f":{k}": v for k, v in campos.items()}
    expresion = "SET " + ", ".join(f"#{k} = :{k}" for k in campos)
    if not responsable:
        expresion += " REMOVE gsi1pk, gsi1sk"
    condicion = "attribute_exists(sk) AND "
    if revision is None:
        condicion += "attribute_not_exists(#revision)"
    else:
        condicion += "#revision = :revision_anterior"
        valores[":revision_anterior"] = revision
    return {
        "Key": {"pk": tarea["pk"], "sk": tarea["sk"]},
        "UpdateExpression": expresion,
        "ConditionExpression": condicion,
        "ExpressionAttributeNames": nombres,
        "ExpressionAttributeValues": valores,
    }


def _progreso(proyecto_id, item):
    total = int(item.get("total", 0))
    completadas = int(item.get("completadas", 0))
    return {
        "proyecto_id": proyecto_id,
        "total_tareas": total,
        "tareas_completadas": completadas,
        "avance_pct": round(100 * completadas / total, 2) if total else 0.0,
    }


def listar(event, context):
    """GET /proyectos/{proyecto_id}/tareas?estado=&limite=&cursor="""
    proyecto_id = event["pathParameters"]["proyecto_id"]
    q = event.get("queryStringParameters") or {}
    limite = _limite(q)
    if limite is None:
        return error("LIMITE_INVALIDO", "limite debe ser un entero positivo")
    estado = q.get("estado")
    if estado and estado not in ESTADOS:
        return error("ESTADO_INVALIDO", f"Estados válidos: {', '.join(ESTADOS)}")

    pk = db.pk(tenant_de(event))
    prefijo = db.prefijo_tareas(proyecto_id)
    args = {
        "KeyConditionExpression": Key("pk").eq(pk) & Key("sk").begins_with(prefijo),
        "Limit": max(limite, 100),
    }
    if estado:
        args["FilterExpression"] = "estado = :e"
        args["ExpressionAttributeValues"] = {":e": estado}
    cursor = q.get("cursor")
    if cursor:
        if not cursor.startswith(prefijo) or len(cursor.encode("utf-8")) > 1024:
            return error("CURSOR_INVALIDO", "El cursor debe pertenecer al proyecto")
        args["ExclusiveStartKey"] = {"pk": pk, "sk": cursor}

    tabla = db.tabla()
    tareas = []
    while True:
        pagina = tabla.query(**args)
        espacio = limite - len(tareas)
        items = pagina["Items"]
        tareas.extend(_publico(item) for item in items[:espacio])
        siguiente = pagina.get("LastEvaluatedKey")
        if len(items) > espacio:
            siguiente = {"pk": pk, "sk": items[espacio - 1]["sk"]}
        if len(tareas) == limite or not siguiente:
            break
        args["ExclusiveStartKey"] = siguiente

    meta = {"total": len(tareas), "proyecto_id": proyecto_id}
    if siguiente:
        meta["cursor"] = siguiente["sk"]
    return ok(tareas, meta=meta)


def mias(event, context):
    """GET /mis-tareas — usa el GSI por responsable."""
    q = event.get("queryStringParameters") or {}
    limite = _limite(q)
    if limite is None:
        return error("LIMITE_INVALIDO", "limite debe ser un entero positivo")

    usuario = usuario_de(event)
    responsable = q.get("responsable_id") or usuario
    if responsable != usuario and rol_de(event) not in GESTORES:
        return error("ROL_INSUFICIENTE", "Solo puedes consultar tus propias tareas", 403)

    r = db.tabla().query(
        IndexName="ix-responsable",
        KeyConditionExpression=Key("gsi1pk").eq(db.gsi_responsable(tenant_de(event), responsable)),
        Limit=limite,
    )
    # Excluye datos antiguos que aún conservan el índice tras quitar al responsable.
    tareas = [_publico(t) for t in r["Items"] if t.get("responsable_id") == responsable]
    return ok(tareas, meta={"total": len(tareas), "responsable_id": responsable})


def obtener(event, context):
    p = event["pathParameters"]
    item = _leer_tarea(db.tabla(), tenant_de(event), p["proyecto_id"], p["tarea_id"])
    return ok(_publico(item)) if item else _no_encontrada(p["tarea_id"])


def crear(event, context):
    """POST /proyectos/{proyecto_id}/tareas — genera tarea.creada."""
    negado = roles.exige(event, *GESTORES)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    proyecto_id = event["pathParameters"]["proyecto_id"]
    if not _id_proyecto_valido(proyecto_id):
        return error("PROYECTO_INVALIDO", "proyecto_id debe ser un entero positivo de 64 bits")
    c = cuerpo_de(event)
    if c is None:
        return error("JSON_INVALIDO", "Envía un objeto JSON")
    if "nombre" not in c:
        return error("DATOS_INCOMPLETOS", "Falta el nombre de la tarea")
    tipo = c.get("tipo", "tarea")
    if tipo not in TIPOS:
        return error("TIPO_INVALIDO", f"Tipos válidos: {', '.join(TIPOS)}")
    invalido = _validar(c)
    if invalido:
        return invalido

    tarea_id = str(uuid.uuid4())
    responsable = c.get("responsable_id")
    ahora = _ahora()
    item = {
        "pk": db.pk(tenant_id),
        "sk": db.sk_tarea(proyecto_id, tarea_id),
        "tarea_id": tarea_id,
        "proyecto_id": proyecto_id,
        "tenant_id": tenant_id,
        "nombre": c["nombre"].strip(),
        "descripcion": c.get("descripcion", ""),
        "tipo": tipo,
        "estado": "pendiente",
        "responsable_id": responsable,
        "fecha_inicio": c.get("fecha_inicio"),
        "fecha_fin": c.get("fecha_fin"),
        "horas_estimadas": c.get("horas_estimadas", 0),
        "depende_de": c.get("depende_de", []),
        "creado_en": ahora,
        "creado_por": usuario_de(event),
        "revision": 1,
        "evento_id": str(uuid.uuid4()),
    }
    if responsable:
        item["gsi1pk"] = db.gsi_responsable(tenant_id, responsable)
        item["gsi1sk"] = f"pendiente#{c.get('fecha_fin') or '9999-12-31'}"

    tabla = db.tabla()
    db.escribir_con_resumen(
        tabla, {"Put": {"Item": item, "ConditionExpression": "attribute_not_exists(sk)"}},
        tenant_id, proyecto_id, ahora, delta_total=1,
    )
    return ok(_publico(item), estado=201)


def completar(event, context):
    """POST /proyectos/{proyecto_id}/tareas/{tarea_id}/completar."""
    tenant_id = tenant_de(event)
    p = event["pathParameters"]
    proyecto_id, tarea_id = p["proyecto_id"], p["tarea_id"]
    if not _id_proyecto_valido(proyecto_id):
        return error("PROYECTO_INVALIDO", "proyecto_id debe ser un entero positivo de 64 bits")
    tabla = db.tabla()
    tarea = _leer_tarea(tabla, tenant_id, proyecto_id, tarea_id)
    if not tarea:
        return _no_encontrada(tarea_id)
    usuario = usuario_de(event)
    if tarea.get("responsable_id") != usuario and rol_de(event) not in GESTORES:
        return error("ROL_INSUFICIENTE", "Solo puedes completar tus propias tareas", 403)
    if tarea["estado"] == "completada":
        return error("YA_COMPLETADA", "La tarea ya estaba completada", 409)

    ahora = _ahora()
    cambios = {
        "estado": "completada", "completado_en": ahora, "completado_por": usuario,
        "evento_id": str(uuid.uuid4()),
    }
    try:
        db.escribir_con_resumen(
            tabla, {"Update": _cambio_tarea(tarea, cambios)},
            tenant_id, proyecto_id, ahora, delta_completadas=1,
        )
    except ClientError as e:
        razones = e.response.get("CancellationReasons") or []
        if (e.response["Error"]["Code"] != "TransactionCanceledException"
                or not razones or razones[0]["Code"] != "ConditionalCheckFailed"):
            raise
        actual = _leer_tarea(tabla, tenant_id, proyecto_id, tarea_id)
        if actual and actual["estado"] == "completada":
            return error("YA_COMPLETADA", "La tarea ya estaba completada", 409)
        return error("TAREA_MODIFICADA", "La tarea cambió. Vuelve a intentar", 409)

    return ok({
        "tarea": _publico(tarea | cambios),
        "proyecto": _progreso(proyecto_id, db.leer_resumen(tabla, tenant_id, proyecto_id)),
    })


def actualizar(event, context):
    negado = roles.exige(event, *GESTORES)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    p = event["pathParameters"]
    c = cuerpo_de(event)
    if c is None:
        return error("JSON_INVALIDO", "Envía un objeto JSON")
    cambios = {k: c[k] for k in CAMPOS if k in c}
    if not cambios:
        return error("SIN_CAMBIOS", f"Envía alguno de: {', '.join(CAMPOS)}")
    if cambios.get("estado") == "completada":
        return error("USA_COMPLETAR", "Para completar usa POST en la ruta /completar")
    if "estado" in cambios and cambios["estado"] not in ESTADOS:
        return error("ESTADO_INVALIDO", f"Estados válidos: {', '.join(ESTADOS)}")
    invalido = _validar(cambios)
    if invalido:
        return invalido
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()

    tabla = db.tabla()
    tarea = _leer_tarea(tabla, tenant_id, p["proyecto_id"], p["tarea_id"])
    if not tarea:
        return _no_encontrada(p["tarea_id"])
    if "estado" in cambios and tarea["estado"] == "completada":
        return error("YA_COMPLETADA", "El estado de una tarea completada es final", 409)

    try:
        r = tabla.update_item(**_cambio_tarea(tarea, cambios), ReturnValues="ALL_NEW")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return error("TAREA_MODIFICADA", "La tarea cambió. Vuelve a intentar", 409)
        raise
    return ok(_publico(r["Attributes"]))


def resumen(event, context):
    proyecto_id = event["pathParameters"]["proyecto_id"]
    item = db.leer_resumen(db.tabla(), tenant_de(event), proyecto_id)
    datos = _progreso(proyecto_id, item)
    if item:
        datos["actualizado_en"] = item.get("actualizado_en")
    return ok(datos)
