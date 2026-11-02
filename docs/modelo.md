# Modelo de datos — ms-tareas

**Motor:** DynamoDB · **Tabla:** `ms-tareas-tareas-dev` (single-table design)
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

Lleva dos contadores que se actualizan con `ADD`, que en DynamoDB es **atómico**:
dos personas completando tareas al mismo tiempo no se pisan.

**Por qué importa:** el evento `tarea.completada` viaja con los dos contadores
completos (`total_tareas` y `tareas_completadas`), no con un "+1". El worker de
`ms-portafolios` **reemplaza** el porcentaje en vez de acumularlo. Así, si un
evento se pierde o llega repetido, el avance se corrige solo en el siguiente en
lugar de quedar desviado para siempre.

Es la diferencia entre un sistema de eventos que aguanta y uno que se
descuadra a la primera.

> `total` es **palabra reservada** en DynamoDB: en la `UpdateExpression` va
> aliasada como `#total`. Sin el alias la llamada falla en tiempo de ejecución.

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
  "gsi1pk": "TENANT#t-acme#RESPONSABLE#8f14e45f-...",
  "gsi1sk": "completada#2026-10-02"
}
```

- `tipo`: `tarea` o `hito`
- `estado`: `pendiente`, `en_progreso`, `completada`, `bloqueada`, `cancelada`
- `depende_de`: lista de `tarea_id`. Es una lista, no una FK — DynamoDB no
  valida referencias y no hace falta para el caso de uso.
- `responsable_id`: UUID lógico de un usuario de `ms-identidad`, sin validación
  HTTP (los microservicios son autónomos).

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
| completar una tarea | `UpdateItem` condicional + `ADD` al resumen |

## Idempotencia de la acción de completar

`POST /tareas/{id}/completar` lleva la condición
`attribute_exists(sk) AND estado <> "completada"`. Si el usuario hace doble clic,
la segunda llamada devuelve **409** y **no incrementa el contador**. Sin esa
condición el avance subiría dos veces por una sola tarea.

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
python seed/generar.py
python seed/generar.py --contar
```
