#!/usr/bin/env python3
"""Carga 31,500 registros en ms-tareas, repartidos en 3 tenants.

    python seed/generar.py            # los 3 tenants
    python seed/generar.py --contar

Reparto por tenant: 10,000 tareas + 500 items de resumen = 10,500.
Total: 30,000 tareas + 1,500 resumenes = 31,500 registros.

Los perfiles son muy distintos a proposito, igual que en ms-portafolios:
Acme reparte sus 10,000 tareas entre 500 obras (20 por obra); Initech las
concentra en 25 desarrollos de producto (400 por desarrollo). Esa diferencia
es la que hace que el multi-tenancy se vea real en la demo.
"""
import os
import sys
import uuid
import random
import argparse
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


def generar(tenant_id, perfil, tabla):
    pk = f"TENANT#{tenant_id}"
    hoy = date.today()
    # Los responsables son los mismos UUID logicos que usa ms-identidad.
    # Se generan con semilla fija para que coincidan entre corridas.
    responsables = [str(uuid.UUID(int=random.getrandbits(128), version=4))
                    for _ in range(200)]
    escritos = 0

    with tabla.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as lote:
        for p in range(1, perfil["proyectos"] + 1):
            total = perfil["tareas_por_proyecto"]
            completadas = 0

            for t in range(total):
                estado = random.choices(ESTADOS, weights=PESOS)[0]
                if estado == "completada":
                    completadas += 1
                tarea_id = str(uuid.uuid4())
                inicio = hoy - timedelta(days=random.randint(0, 400))
                fin = inicio + timedelta(days=random.randint(2, 60))
                responsable = random.choice(responsables)
                # Cada 15 tareas, una es hito
                tipo = "hito" if t % 15 == 14 else "tarea"

                lote.put_item(Item={
                    "pk": pk,
                    "sk": f"PROYECTO#{p}#TAREA#{tarea_id}",
                    "tarea_id": tarea_id,
                    "proyecto_id": str(p),
                    "tenant_id": tenant_id,
                    "nombre": (f"{random.choice(perfil['acciones'])} "
                               f"{random.choice(perfil['objetos'])}"),
                    "tipo": tipo,
                    "estado": estado,
                    "responsable_id": responsable,
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "horas_estimadas": random.choice([4, 8, 16, 24, 40, 80]),
                    "depende_de": [],
                    "creado_en": ahora_iso(inicio),
                    "gsi1pk": f"TENANT#{tenant_id}#RESPONSABLE#{responsable}",
                    "gsi1sk": f"{estado}#{fin.isoformat()}",
                })
                escritos += 1
                if escritos % 2500 == 0:
                    print(f"    {escritos:>6} registros...", flush=True)

            # El item de resumen con los contadores que alimentan el evento
            lote.put_item(Item={
                "pk": pk,
                "sk": f"PROYECTO#{p}#RESUMEN",
                "proyecto_id": str(p),
                "tenant_id": tenant_id,
                "total": total,
                "completadas": completadas,
                "actualizado_en": ahora_iso(hoy),
            })
            escritos += 1

    return escritos


def contar(tabla):
    print(f"Tabla: {TABLA}\n")
    gran_total = 0
    for tenant_id, perfil in PERFILES.items():
        tareas = resumenes = 0
        clave = None
        while True:
            args = {"KeyConditionExpression": Key("pk").eq(f"TENANT#{tenant_id}"),
                    "ProjectionExpression": "sk"}
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
    args = ap.parse_args()

    tabla = boto3.resource("dynamodb", region_name=REGION).Table(TABLA)
    if args.contar:
        return contar(tabla)

    random.seed(2026)
    objetivo = {args.tenant: PERFILES[args.tenant]} if args.tenant else PERFILES
    total = 0
    print(f"Tabla: {TABLA}\n")
    for tenant_id, perfil in objetivo.items():
        print(f"== {perfil['nombre']} ({tenant_id}) ==")
        print(f"   {perfil['proyectos']} proyectos x "
              f"{perfil['tareas_por_proyecto']} tareas")
        n = generar(tenant_id, perfil, tabla)
        print(f"   {n} registros\n")
        total += n
    print(f"Listo: {total} registros. Verifica con --contar")


if __name__ == "__main__":
    main()
