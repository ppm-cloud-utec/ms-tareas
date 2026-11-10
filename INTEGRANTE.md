# Integrante responsable

**fabianbot1331**

Microservicio `ms-tareas`. Tareas, hitos, dependencias y completado.

## Checklist de entrega

- [x] Paquete de Serverless validado localmente con los ARN de prueba.
- [ ] Ejecutar `npx serverless deploy --stage dev` en AWS Academy con la infraestructura compartida desplegada.
- [x] Ocho endpoints REST con el authorizer compartido configurado.
- [x] Modelo JSON y claves de DynamoDB en [docs/modelo.md](docs/modelo.md).
- [x] Seed para 30 000 tareas y 775 resúmenes en tres tenants.
- [x] Publicación a SNS tras confirmar la transacción, con reintento desde el stream.
- [x] README con endpoints y [guía de verificación](docs/demo.md).

El servicio publica eventos a SNS. El consumidor SQS de los eventos de
tareas se ejecuta en `ms-portafolios`.

- [ ] Guardar en `docs/capturas/` las evidencias del despliegue, 401 sin token, dos tenants en la misma ruta y conteo de registros.
- [ ] Verificar en el dashboard desplegado que el worker de portafolios actualiza el avance.
