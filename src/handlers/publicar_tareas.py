"""Publica los cambios de tarea confirmados en DynamoDB."""
import logging

from boto3.dynamodb.types import TypeDeserializer

from src import db
from src.eventos import publicar

logger = logging.getLogger(__name__)
_deserializador = TypeDeserializer()


def manejar(event, context):
    tabla = db.tabla()
    for registro in event["Records"]:
        cambio = registro["dynamodb"]
        imagen = cambio.get("NewImage", {})
        marcador = imagen.get("evento_id")
        if not marcador or marcador == cambio.get("OldImage", {}).get("evento_id"):
            continue
        tarea = {k: _deserializador.deserialize(v) for k, v in imagen.items()}
        tenant_id = tarea["tenant_id"]
        proyecto_id = tarea["proyecto_id"]
        try:
            resumen = db.leer_resumen(tabla, tenant_id, proyecto_id)
            total = int(resumen["total"])
            completadas = int(resumen["completadas"])
            datos = {
                "proyecto_id": int(proyecto_id),
                "tarea_id": tarea["tarea_id"],
                "tipo": tarea.get("tipo", "tarea"),
                "total_tareas": total,
                "tareas_completadas": completadas,
            }
            if tarea["estado"] == "completada":
                datos["nombre"] = tarea["nombre"]
                datos["avance_pct"] = round(100 * completadas / total, 2) if total else 0.0
                publicar("tarea.completada", tenant_id, datos,
                         tarea["evento_id"], tarea["completado_en"])
            else:
                publicar("tarea.creada", tenant_id, datos,
                         tarea["evento_id"], tarea["creado_en"])
        except Exception:
            logger.exception("Error publicando el evento %s", tarea["evento_id"])
            # Lambda reintenta desde este registro y conserva el orden del lote.
            return {"batchItemFailures": [{"itemIdentifier": cambio["SequenceNumber"]}]}
    return {"batchItemFailures": []}
