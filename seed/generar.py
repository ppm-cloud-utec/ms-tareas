#!/usr/bin/env python3
"""Carga 30,000 tareas y 775 resúmenes, repartidos en 3 tenants.

    python seed/generar.py            # los 3 tenants
    python seed/generar.py --contar

Los perfiles son muy distintos a proposito, igual que en ms-portafolios:
Acme reparte sus 10,000 tareas entre 500 obras (20 por obra); Initech las
concentra en 25 desarrollos de producto (400 por desarrollo). Esa diferencia
es la que hace que el multi-tenancy se vea real en la demo.
"""
import os
import uuid
import random
import argparse
from collections import defaultdict
from datetime import date, timedelta

import boto3
from boto3.dynamodb.conditions import Key

TABLA = os.environ.get("TABLA", "ms-tareas-tareas-dev")
REGION = os.environ.get("AWS_REGION", "us-east-1")

PERFILES = {
    "t-acme": dict(
        nombre="Acme Ingenieria", proyectos=500, tareas_por_proyecto=20,
        acciones=["Excavacion", "Encofrado", "Vaciado de concreto", "Acabados",
                  "Instalaciones electricas", "Instalaciones sanitarias",
                  "Supervision", "Entrega de obra", "Replanteo", "Habilitacion"],
        objetos=["sector A", "sector B", "nivel 1", "nivel 2", "cimentacion",
                 "fachada", "techo", "accesos"],
    ),
    "t-globex": dict(
        nombre="Globex Consulting", proyectos=250, tareas_por_proyecto=40,
        acciones=["Levantamiento de informacion", "Entrevistas", "Diagnostico",
                  "Diseño de solucion", "Taller con el cliente", "Prototipo",
                  "Implementacion", "Capacitacion", "Informe final", "Cierre"],
        objetos=["area comercial", "area de operaciones", "finanzas", "TI",
                 "logistica", "RRHH", "atencion al cliente"],
    ),
    "t-initech": dict(
        nombre="Initech Software", proyectos=25, tareas_por_proyecto=400,
        acciones=["Diseñar", "Implementar", "Probar", "Revisar", "Documentar",
                  "Desplegar", "Refactorizar", "Corregir", "Integrar", "Migrar"],
        objetos=["endpoint de pagos", "modelo de usuario", "panel de reportes",
                 "cola de notificaciones", "cache", "autenticacion",
                 "exportador CSV", "buscador", "webhooks"],
    ),
}

ESTADOS = ["pendiente", "en_progreso", "completada", "bloqueada", "cancelada"]
PESOS = [25, 15, 52, 5, 3]


def ahora_iso(d):
    return d.isoformat() + "T12:00:00+00:00"


def responsables_de(tenant_id, tabla_identidad):
    if tabla_identidad is None:
        return [str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppm:{tenant_id}:usuario:{i}"))
                for i in range(200)]

    args = {
        "KeyConditionExpression": Key("pk").eq(f"TENANT#{tenant_id}")
        & Key("sk").begins_with("USUARIO#"),
        "ProjectionExpression": "usuario_id, activo",
        "Limit": 200,
    }
    responsables = []
    while len(responsables) < 200:
        r = tabla_identidad.query(**args)
        responsables.extend(x["usuario_id"] for x in r["Items"] if x.get("activo"))
        if "LastEvaluatedKey" not in r:
            break
        args["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    if not responsables:
        raise ValueError(f"No hay usuarios activos en ms-identidad para {tenant_id}")
    return responsables[:200]


def tareas_existentes(tabla, pk):
    args = {
        "KeyConditionExpression": Key("pk").eq(pk),
        "ProjectionExpression": "sk, estado, proyecto_id",
        "ConsistentRead": True,
    }
    while True:
        r = tabla.query(**args)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        args["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def generar(tenant_id, perfil, tabla, responsables):
    pk = f"TENANT#{tenant_id}"
    hoy = date.today()
    azar = random.Random(f"2026:{tenant_id}")
    nuevos = 0
    por_proyecto = defaultdict(dict)
    for item in tareas_existentes(tabla, pk):
        if "#TAREA#" in item["sk"]:
            por_proyecto[item["proyecto_id"]][item["sk"]] = item["estado"]

    for p in range(1, perfil["proyectos"] + 1):
        existentes = por_proyecto[str(p)]
        total = len(existentes)
        completadas = sum(estado == "completada" for estado in existentes.values())
        objetivo = perfil["tareas_por_proyecto"]

        # Confirma el lote de tareas antes de guardar su resumen.
        with tabla.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as lote:
            for t in range(objetivo):
                if total >= objetivo:
                    break
                tarea_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ppm:{tenant_id}:tarea:{p}:{t}"))
                sk = f"PROYECTO#{p}#TAREA#{tarea_id}"
                if sk in existentes:
                    continue
                estado = azar.choices(ESTADOS, weights=PESOS)[0]
                completadas += estado == "completada"
                inicio = hoy - timedelta(days=azar.randint(0, 400))
                fin = inicio + timedelta(days=azar.randint(2, 60))
                responsable = azar.choice(responsables)
                tipo = "hito" if t % 15 == 14 else "tarea"

                lote.put_item(Item={
                    "pk": pk,
                    "sk": sk,
                    "tarea_id": tarea_id,
                    "proyecto_id": str(p),
                    "tenant_id": tenant_id,
                    "nombre": (f"{azar.choice(perfil['acciones'])} "
                               f"{azar.choice(perfil['objetos'])}"),
                    "tipo": tipo,
                    "estado": estado,
                    "responsable_id": responsable,
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "horas_estimadas": azar.choice([4, 8, 16, 24, 40, 80]),
                    "depende_de": [],
                    "creado_en": ahora_iso(inicio),
                    "gsi1pk": f"TENANT#{tenant_id}#RESPONSABLE#{responsable}",
                    "gsi1sk": f"{estado}#{fin.isoformat()}",
                })
                total += 1
                nuevos += 1
                if nuevos % 2500 == 0:
                    print(f"    {nuevos:>6} tareas nuevas...", flush=True)

        tabla.put_item(Item={
            "pk": pk,
            "sk": f"PROYECTO#{p}#RESUMEN",
            "proyecto_id": str(p),
            "tenant_id": tenant_id,
            "total": total,
            "completadas": completadas,
            "actualizado_en": ahora_iso(hoy),
        })

    return nuevos


def contar(tabla, objetivo):
    print(f"Tabla: {TABLA}\n")
    gran_total = 0
    for tenant_id, perfil in objetivo.items():
        tareas = resumenes = 0
        clave = None
        while True:
            args = {"KeyConditionExpression": Key("pk").eq(f"TENANT#{tenant_id}"),
                    "ProjectionExpression": "sk", "ConsistentRead": True}
            if clave:
                args["ExclusiveStartKey"] = clave
            r = tabla.query(**args)
            for it in r["Items"]:
                if it["sk"].endswith("#RESUMEN"):
                    resumenes += 1
                else:
                    tareas += 1
            clave = r.get("LastEvaluatedKey")
            if not clave:
                break
        print(f"  {perfil['nombre']:<22} {tareas:>6} tareas  +{resumenes:>4} resumenes")
        gran_total += tareas + resumenes
    print(f"\n  TOTAL {gran_total:>6} registros")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contar", action="store_true")
    ap.add_argument("--tenant", choices=list(PERFILES))
    ap.add_argument("--tabla-identidad", help="Tabla DynamoDB de ms-identidad para asignar usuarios activos")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb", region_name=REGION)
    tabla = dynamo.Table(TABLA)
    objetivo = {args.tenant: PERFILES[args.tenant]} if args.tenant else PERFILES
    if args.contar:
        return contar(tabla, objetivo)

    identidad = dynamo.Table(args.tabla_identidad) if args.tabla_identidad else None
    print(f"Tabla: {TABLA}\n")
    for tenant_id, perfil in objetivo.items():
        print(f"== {perfil['nombre']} ({tenant_id}) ==")
        print(f"   {perfil['proyectos']} proyectos x "
              f"{perfil['tareas_por_proyecto']} tareas")
        n = generar(tenant_id, perfil, tabla, responsables_de(tenant_id, identidad))
        print(f"   {n} tareas nuevas y {perfil['proyectos']} resúmenes actualizados\n")
    print("Carga terminada. Verifica con --contar")


if __name__ == "__main__":
    main()
