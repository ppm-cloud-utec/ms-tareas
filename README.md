# ms-tareas

Tareas, hitos y dependencias. **Es el microservicio que hace visible el EDA en
la demo:** completar una tarea aquí cambia el avance del proyecto en
`ms-portafolios` sin que los dos se conozcan.

- **Responsable:** @fabianbot1331
- **Base de datos:** DynamoDB · partition key prefijada por tenant
- **Runtime:** Python 3.12 · Serverless Framework · us-east-1

## Endpoints (8, todos protegidos)

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/proyectos/{proyecto_id}/tareas` | tareas del proyecto, filtrables por `?estado=` |
| POST | `/proyectos/{proyecto_id}/tareas` | crea · **publica `tarea.creada`** |
| GET | `/proyectos/{proyecto_id}/tareas/{tarea_id}` | una tarea |
| PUT | `/proyectos/{proyecto_id}/tareas/{tarea_id}` | actualiza (no sirve para completar) |
| POST | `/proyectos/{proyecto_id}/tareas/{tarea_id}/completar` | **publica `tarea.completada`** |
| GET | `/proyectos/{proyecto_id}/resumen` | total, completadas y avance |
| GET | `/mis-tareas` | las del usuario del token, por el GSI |
| GET | `/health` | verifica el servicio |

## El evento clave de la demo

```
                POST /tareas/{id}/completar
                            |
            UpdateItem condicional (no cuenta dos veces)
                            |
              ADD atomico al item de RESUMEN
                            |
        publica tarea.completada  { total_tareas, tareas_completadas }
                            |
                      SNS ppm-eventos
                            |
                 SQS ppm-cola-portafolios
                            |
         worker de ms-portafolios recalcula avance_pct y salud
```

El frontend **nunca** llama a `ms-portafolios` para actualizar el avance. Vuelve
al dashboard y el número ya cambió solo. Eso es lo que hay que mostrar.

## Dos decisiones que vale la pena entender

**1. El evento lleva los contadores completos, no un "+1".** El item de
`RESUMEN` mantiene `total` y `completadas` con `ADD`, que es atómico. El worker
de `ms-portafolios` reemplaza el porcentaje en lugar de acumularlo, así que un
evento perdido o repetido se corrige en el siguiente en vez de desviar el
avance para siempre.

**2. Completar es idempotente.** La condición
`estado <> "completada"` hace que un doble clic devuelva 409 sin incrementar el
contador.

## Desplegar

```bash
pip install -r requirements.txt
npm install
sls deploy
```

## Cargar datos

```bash
export TABLA=ms-tareas-tareas-dev
python seed/generar.py            # 30,775 registros en 3 tenants
python seed/generar.py --contar
```

## Documentación

- [Modelo de datos](docs/modelo.md) — claves, el item de resumen, el GSI, volumen
- [Arquitectura general](https://github.com/ppm-cloud-utec/infra-compartida/blob/main/docs/arquitectura.md)
