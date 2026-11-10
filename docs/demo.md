# Verificación y demo de ms-tareas

Usa el stage desplegado y la URL de `ms-tareas` que mostró Serverless.
Obtén un JWT de Acme y otro de Globex iniciando sesión en `ms-identidad`.
Pégalos en una terminal interactiva. Los comandos de lectura ocultan los
tokens y los ejemplos imprimen solo los resultados necesarios.

```bash
export API='https://TU_API.execute-api.us-east-1.amazonaws.com/dev'
read -rsp 'JWT de Acme: ' TOKEN_ACME; echo
read -rsp 'JWT de Globex: ' TOKEN_GLOBEX; echo
```

## Evidencias para docs/capturas

| Captura | Resultado esperado |
|---|---|
| `01-deploy.png` | Salida de `npx serverless deploy --stage dev` con el stack y los endpoints. |
| `02-sin-token.png` | `GET /health` devuelve `401`. |
| `03-acme.png` | `GET /proyectos/1/tareas` con el token de Acme muestra tareas de `t-acme`. |
| `04-globex.png` | La misma ruta con el token de Globex muestra tareas de `t-globex`. |
| `05-conteo.png` | El seed reporta 30 775 registros en una tabla recién poblada. |

```bash
curl -sS -i "$API/health"

curl -fsS -H "Authorization: Bearer $TOKEN_ACME" \
  "$API/proyectos/1/tareas?limite=3" |
  jq '.datos[] | {tenant_id, proyecto_id, tarea_id, nombre, estado}'

curl -fsS -H "Authorization: Bearer $TOKEN_GLOBEX" \
  "$API/proyectos/1/tareas?limite=3" |
  jq '.datos[] | {tenant_id, proyecto_id, tarea_id, nombre, estado}'

TABLA=ms-tareas-tareas-dev python3.12 seed/generar.py --contar
```

## Probar el flujo de una tarea

Usa un token de Acme con rol `pm`, `pmo` o `admin`. El proyecto 1 debe
existir en `ms-portafolios` para observar el cambio en el dashboard.

```bash
RUTA="$API/proyectos/1/tareas"
TAREA_ID=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN_ACME" \
  -H 'Content-Type: application/json' --data '{"nombre":"Hito demo PPM","tipo":"hito"}' \
  "$RUTA" | jq -er '.datos.tarea_id')

curl -fsS -H "Authorization: Bearer $TOKEN_ACME" "$RUTA/$TAREA_ID" | jq .
curl -sS -o /dev/null -w 'Lectura desde Globex: HTTP %{http_code}\n' \
  -H "Authorization: Bearer $TOKEN_GLOBEX" "$RUTA/$TAREA_ID"

curl -fsS -X PUT -H "Authorization: Bearer $TOKEN_ACME" \
  -H 'Content-Type: application/json' --data '{"estado":"en_progreso"}' \
  "$RUTA/$TAREA_ID" | jq .datos.estado

curl -fsS -H "Authorization: Bearer $TOKEN_ACME" "$API/proyectos/1/resumen" | jq .datos

curl -fsS -X POST -H "Authorization: Bearer $TOKEN_ACME" \
  "$RUTA/$TAREA_ID/completar" | jq .datos.proyecto

curl -sS -o /dev/null -w 'Completar por segunda vez: HTTP %{http_code}\n' \
  -X POST -H "Authorization: Bearer $TOKEN_ACME" "$RUTA/$TAREA_ID/completar"

curl -fsS -H "Authorization: Bearer $TOKEN_ACME" "$API/proyectos/1/resumen" | jq .datos
curl -fsS -H "Authorization: Bearer $TOKEN_ACME" "$API/mis-tareas" | jq .meta
```

La lectura cruzada debe devolver `404`. Completar por segunda vez debe
devolver `409`. El primer completado suma exactamente uno al contador de
completadas. Recarga el proyecto en el dashboard después de que el worker
procese el evento y compara su avance con el de `/resumen`.

## Aporte al PPT del equipo

**Tareas separadas por tenant.** El authorizer toma el tenant del JWT y el
servicio consulta la partición correspondiente en DynamoDB. Acme y Globex
pueden tener un proyecto con el mismo ID y ver tareas distintas. La carga
inicial contiene 10 000 tareas por tenant.

**Completar una tarea actualiza el avance.** La tarea y su contador se
guardan juntos. El stream publica `tarea.completada` a SNS. La cola de
portafolios recibe el evento y su worker recalcula el porcentaje.

Para exponer, muestra primero el mismo listado con dos tenants. Luego
completa una tarea en Acme y compara el avance del proyecto antes y después.
