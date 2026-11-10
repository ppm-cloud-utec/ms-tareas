# ms-tareas

Microservicio de tareas, hitos, dependencias y avance de proyectos del PPM de
CS2032. Responsable **@fabianbot1331**. Usa API Gateway REST, Lambda Python 3.12,
DynamoDB y SNS en `us-east-1`.

## API

Todas las rutas requieren `Authorization: Bearer <JWT>` de `ms-identidad`.
El authorizer compartido proporciona el tenant, el usuario y el rol. La API
usa ese tenant para construir todas las claves de DynamoDB.

| Método | Ruta | Uso |
|---|---|---|
| GET | `/proyectos/{proyecto_id}/tareas` | Lista por proyecto. Admite `estado`, `limite` y `cursor`. |
| POST | `/proyectos/{proyecto_id}/tareas` | Crea una tarea o hito. Genera `tarea.creada`. |
| GET | `/proyectos/{proyecto_id}/tareas/{tarea_id}` | Obtiene una tarea del tenant. |
| PUT | `/proyectos/{proyecto_id}/tareas/{tarea_id}` | Edita campos o cambia el estado antes de completar. |
| POST | `/proyectos/{proyecto_id}/tareas/{tarea_id}/completar` | Completa y devuelve el resumen. Genera `tarea.completada`. |
| GET | `/proyectos/{proyecto_id}/resumen` | Devuelve total, completadas y avance porcentual. |
| GET | `/mis-tareas` | Consulta el índice de tareas del usuario. Admite `limite`. |
| GET | `/health` | Devuelve el estado del servicio y el tenant autenticado. |

Los roles `pm`, `pmo` y `admin` pueden crear, editar y completar tareas y
consultar `/mis-tareas?responsable_id=...` dentro de su tenant. Un miembro
puede completar sus propias tareas. Una segunda llamada a `/completar`
devuelve `409` y conserva el contador. Para paginar el listado del proyecto,
envía el `meta.cursor` de la página anterior con el mismo filtro.

## Despliegue

Desde la raíz, con Node.js, Docker en ejecución, credenciales vigentes de AWS Academy
y el stack `infra-compartida-dev` desplegado en la misma cuenta:

```bash
npm ci
npx serverless deploy --stage dev
```

El stack crea la tabla, el índice, el stream y las Lambdas. Usa el
`LabRole` de la cuenta y lee los ARN del authorizer y SNS de
`infra-compartida`. El rol debe permitir la lectura de DynamoDB Streams
para que Lambda pueda publicar los eventos. Serverless 3.40 puede mostrar
un aviso de validación por `python3.12`. El paquete conserva ese runtime en
CloudFormation. Con Python 3.12 instalado en el equipo, se puede empaquetar
localmente sin Docker con `--param=dockerizePip=false`.

## Datos de demostración

Carga el seed antes de atender tráfico en el stage de demostración. Con
`ms-identidad` ya sembrado, se pueden asignar tareas a usuarios del tenant:

```bash
python3.12 -m pip install boto3
export TABLA=ms-tareas-tareas-dev
python3.12 seed/generar.py --tabla-identidad ms-identidad-identidad-dev
python3.12 seed/generar.py --contar
```

En una tabla nueva se cargan **30 000 tareas y 775 resúmenes**. Si se
interrumpe la carga, puedes repetirla antes de abrir el stage a tráfico. El
script conserva las tareas existentes y reconstruye los resúmenes.
Sin `--tabla-identidad`, el seed usa responsables ficticios.

## Pruebas

Pruebas locales de los handlers, DynamoDB, el stream y SNS/SQS emulados:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install 'moto[dynamodb,sns,sqs]==5.1.22'
python -m unittest discover -s tests -v
```

Para probar el stage desplegado y reunir evidencias, sigue la
[guía de verificación y demo](docs/demo.md).

## Publicación de eventos

DynamoDB confirma en una sola transacción la tarea y su contador en el
resumen. Después, DynamoDB Streams invoca a `publicarTareas`, que lee los
contadores actuales y publica el evento a SNS. El stream puede reintentar un
envío fallido y conserva el ID del evento. El consumidor de
`ms-portafolios` usa los contadores para recalcular el avance.

- [Modelo de datos](docs/modelo.md)
- [Arquitectura del equipo](https://github.com/ppm-cloud-utec/infra-compartida/blob/main/docs/arquitectura.md)
