# ms-tareas

Tareas, hitos y avance de cada proyecto.

Parte del proyecto final de Cloud Computing (CS2032), Grupo 4. Todas las rutas cuelgan de la API compartida `ppm-api-dev` bajo `/tareas` y, salvo el login, piden el token JWT.

Base de datos: DynamoDB (tabla ms-tareas-tareas-dev, clave TENANT#<id>).

## Endpoints

- GET /tareas/health
- GET /tareas/proyectos/{proyecto_id}/tareas
- POST /tareas/proyectos/{proyecto_id}/tareas
- GET /tareas/proyectos/{proyecto_id}/tareas/{tarea_id}
- PUT /tareas/proyectos/{proyecto_id}/tareas/{tarea_id}
- POST /tareas/proyectos/{proyecto_id}/tareas/{tarea_id}/completar
- GET /tareas/proyectos/{proyecto_id}/resumen
- GET /tareas/mis-tareas

## Eventos

Publica tarea.creada y tarea.completada desde DynamoDB Streams.

## Despliegue

Requiere que `infra-compartida` ya este desplegado.

```bash
npm install
npx serverless deploy
```

## Datos de prueba

```bash
python seed/generar.py
python seed/generar.py --contar
```
