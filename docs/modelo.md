# Modelo de datos — ms-tareas

**Motor:** DynamoDB · **Tabla:** `ms-tareas-tareas-dev` (diseño de tabla única)
**Facturación:** PAY_PER_REQUEST

## Claves

| Item | `pk` | `sk` |
|---|---|---|
| Tarea | `TENANT#<tenant_id>` | `PROYECTO#<pid>#TAREA#<tarea_id>` |
| Resumen del proyecto | `TENANT#<tenant_id>` | `PROYECTO#<pid>#RESUMEN` |

El `sk` compuesto permite traer todas las tareas de un proyecto con un
`begins_with(sk, "PROYECTO#7#TAREA#")`, sin índices ni scans. El `tenant_id`
está en la partición, así que una query **no puede** cruzar tenants.

## El item de RESUMEN: la pieza central del diseño

```json
{
  "pk": "TENANT#t-acme",
  "sk": "PROYECTO#7#RESUMEN",
  "proyecto_id": "7",
  "total": 20,
  "completadas": 13,
  "actualizado_en": "2026-11-01T12:00:00+00:00"
}
```

Al crear o completar una tarea, una transacción escribe la tarea y el
resumen juntos. El resumen usa `ADD` con `#total` como alias de `total`.
En la misma transacción, la tarea guarda un `evento_id` nuevo. DynamoDB
Streams entrega el cambio ya confirmado al publicador de SNS.

El publicador lee el resumen con consistencia fuerte y envía el total y
las completadas en `total_tareas` y `tareas_completadas`. Un reintento usa
el mismo ID de evento. Las actualizaciones de campos que conservan el
`evento_id` y las tareas del seed quedan fuera de esta publicación.

## Item de tarea

```json
{
  "pk": "TENANT#t-acme",
  "sk": "PROYECTO#7#TAREA#9c84...",
  "tarea_id": "9c84...",
  "proyecto_id": "7",
  "tenant_id": "t-acme",
  "nombre": "Vaciado de concreto sector A",
  "tipo": "tarea",
  "estado": "completada",
  "responsable_id": "8f14e45f-...",
  "fecha_inicio": "2026-09-14",
  "fecha_fin": "2026-10-02",
  "horas_estimadas": 40,
  "depende_de": [],
  "creado_en": "2026-09-14T12:00:00+00:00",
  "completado_en": "2026-10-01T09:12:00+00:00",
  "completado_por": "8f14e45f-...",
  "revision": 2,
  "evento_id": "3f23178e-...",
  "gsi1pk": "TENANT#t-acme#RESPONSABLE#8f14e45f-...",
  "gsi1sk": "completada#2026-10-02"
}
```

- `tipo`: `tarea` o `hito`
- `estado`: `pendiente`, `en_progreso`, `completada`, `bloqueada`, `cancelada`
- `depende_de` contiene una lista de IDs de tareas. Se almacena como dato de planificación.
- `responsable_id` es el ID lógico de un usuario de `ms-identidad`. En operación, el servicio se mantiene autónomo.
- `revision` permite detectar escrituras concurrentes. La primera edición de un registro previo inicializa la revisión.
- `evento_id` identifica la última creación o finalización de la tarea. Es un campo interno.

## Índice secundario

| Índice | `gsi1pk` | `gsi1sk` | Para qué |
|---|---|---|---|
| `ix-responsable` | `TENANT#<tid>#RESPONSABLE#<uuid>` | `<estado>#<fecha_fin>` | "mis tareas" |

**Por qué existe:** un miembro de equipo de Acme tiene 8 tareas repartidas entre
500 obras. Sin el índice habría que recorrer las 10,000 tareas del tenant para
encontrarlas. El `gsi1pk` también empieza con el tenant, así que el aislamiento
se mantiene dentro del índice.

Es el **único** GSI: el resto de accesos los cubre el diseño de claves.

| Consulta | Operación |
|---|---|
| tareas de un proyecto | `Query` con `begins_with` |
| avance de un proyecto | `GetItem` del item de resumen |
| mis tareas | `Query` sobre `ix-responsable` |
| completar una tarea | `TransactWriteItems` con revisión condicional y `ADD` al resumen |

## Idempotencia de la acción de completar

`POST /proyectos/{proyecto_id}/tareas/{tarea_id}/completar` comprueba el
estado y condiciona la escritura a la revisión leída. Si dos solicitudes
intentan completar la misma tarea, solo una puede confirmar su revisión e
incrementar el contador. Una nueva solicitud sobre una tarea completada
recibe `409`. Las ediciones de metadata se permiten después del completado.

Al reasignar, quitar un responsable, cambiar la fecha de fin o cambiar el
estado, la escritura actualiza también las claves del GSI.

## Volumen de datos

| Tenant | Proyectos | Tareas por proyecto | Tareas | Resúmenes | Total |
|---|---|---|---|---|---|
| `t-acme` | 500 | 20 | 10,000 | 500 | 10,500 |
| `t-globex` | 250 | 40 | 10,000 | 250 | 10,250 |
| `t-initech` | 25 | 400 | 10,000 | 25 | 10,025 |
| | | | **30,000** | **775** | **30,775** |

Los tres reparten las mismas 10,000 tareas de forma completamente distinta:
Acme entre 500 obras cortas, Initech concentradas en 25 desarrollos de 400
tareas. Es la misma lógica de perfiles que en `ms-portafolios`.

## Cargar los datos

```bash
export TABLA=ms-tareas-tareas-dev
python3.12 seed/generar.py --tabla-identidad ms-identidad-identidad-dev
python3.12 seed/generar.py --contar
```
