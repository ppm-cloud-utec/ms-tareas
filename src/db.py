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

import boto3

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
