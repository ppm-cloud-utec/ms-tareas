import json
import os
import unittest
from unittest.mock import patch

import boto3
from moto import mock_aws


class TareasTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock_aws())
        self.enterContext(patch.dict(os.environ, {
            "AWS_DEFAULT_REGION": "us-east-1",
            "TABLA": "ms-tareas-test",
            "SERVICIO": "ms-tareas",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:ppm-eventos",
        }))
        self.tabla = boto3.resource("dynamodb").create_table(
            TableName=os.environ["TABLA"],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"}
                                  for k in ("pk", "sk", "gsi1pk", "gsi1sk")],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "ix-responsable",
                "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"},
                              {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
            StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"},
        )
        self.sns = boto3.client("sns")
        self.topic = self.sns.create_topic(Name="ppm-eventos")["TopicArn"]
        self.sqs = boto3.client("sqs")
        self.cola = self.sqs.create_queue(QueueName="ppm-cola-portafolios")["QueueUrl"]
        cola_arn = self.sqs.get_queue_attributes(
            QueueUrl=self.cola, AttributeNames=["QueueArn"],
        )["Attributes"]["QueueArn"]
        self.sns.subscribe(TopicArn=self.topic, Protocol="sqs", Endpoint=cola_arn)

        from src.handlers import health, publicar_tareas, tareas
        self.health = health
        self.publicador = publicar_tareas
        self.tareas = tareas

    def evento(self, *, tenant="t-acme", proyecto="1", usuario="u-acme", rol="admin",
               tarea_id=None, cuerpo=None, consulta=None):
        return {
            "pathParameters": {"proyecto_id": proyecto, "tarea_id": tarea_id},
            "requestContext": {"authorizer": {
                "tenant_id": tenant, "usuario_id": usuario, "rol": rol,
            }},
            "body": cuerpo,
            "queryStringParameters": consulta,
        }

    def llamar(self, handler, estado=200, **kw):
        r = handler(self.evento(**kw), None)
        self.assertEqual(r["statusCode"], estado, r["body"])
        return json.loads(r["body"])

    def crear(self, nombre="Hito", **kw):
        return self.llamar(self.tareas.crear, 201, cuerpo=json.dumps({
            "nombre": nombre, "tipo": "hito", "horas_estimadas": 1.5,
            "responsable_id": "u-acme", "fecha_fin": "2026-12-01",
            "tenant_id": "t-ignorado",
        }), **kw)["datos"]

    def registros(self):
        client = boto3.client("dynamodbstreams")
        arn = self.tabla.latest_stream_arn
        shard = client.describe_stream(StreamArn=arn)["StreamDescription"]["Shards"][0]["ShardId"]
        it = client.get_shard_iterator(
            StreamArn=arn, ShardId=shard, ShardIteratorType="TRIM_HORIZON",
        )["ShardIterator"]
        return client.get_records(ShardIterator=it)["Records"]

    def test_dos_tenants_y_paginacion_con_filtro(self):
        self.crear("Acme A")
        self.crear("Acme B")
        self.crear("Globex", tenant="t-globex")
        self.assertEqual(self.llamar(self.health.manejar, tenant="t-globex")["datos"]["tenant"],
                         "t-globex")
        primera = self.llamar(self.tareas.listar, consulta={"estado": "pendiente", "limite": "1"})
        segunda = self.llamar(self.tareas.listar, consulta={
            "estado": "pendiente", "limite": "1", "cursor": primera["meta"]["cursor"],
        })
        self.assertEqual({primera["datos"][0]["nombre"], segunda["datos"][0]["nombre"]},
                         {"Acme A", "Acme B"})
        globex = self.llamar(self.tareas.listar, tenant="t-globex",
                             consulta={"tenant_id": "t-acme"})["datos"]
        self.assertEqual([t["nombre"] for t in globex], ["Globex"])
        self.llamar(self.tareas.obtener, 404, tenant="t-globex",
                    tarea_id=primera["datos"][0]["tarea_id"])

    def test_roles_indice_y_datos_previos(self):
        tarea_id = self.crear()["tarea_id"]
        clave = {"pk": "TENANT#t-acme", "sk": f"PROYECTO#1#TAREA#{tarea_id}"}
        self.tabla.update_item(Key=clave, UpdateExpression="REMOVE revision, evento_id, gsi1sk")
        self.llamar(self.tareas.actualizar, 403, tarea_id=tarea_id, rol="miembro", cuerpo="{}")
        self.llamar(self.tareas.completar, 403, tarea_id=tarea_id, rol="miembro", usuario="otro")
        self.llamar(self.tareas.mias, 403, rol="miembro", consulta={"responsable_id": "otro"})
        self.llamar(self.tareas.actualizar, tarea_id=tarea_id,
                    cuerpo='{"responsable_id":"otro","fecha_fin":"2026-12-20","estado":"en_progreso"}')
        actual = self.tabla.get_item(Key=clave)["Item"]
        self.assertEqual(actual["gsi1sk"], "en_progreso#2026-12-20")
        self.assertEqual(self.llamar(self.tareas.mias, usuario="otro")["meta"]["total"], 1)
        self.llamar(self.tareas.actualizar, tarea_id=tarea_id, cuerpo='{"responsable_id":null}')
        actual = self.tabla.get_item(Key=clave)["Item"]
        self.assertFalse({"gsi1pk", "gsi1sk"} & actual.keys())
        self.assertEqual(self.llamar(self.tareas.mias, usuario="otro")["datos"], [])

    def test_completar_y_publicar_despues_de_confirmar(self):
        tarea = self.crear()
        self.assertEqual(tarea["horas_estimadas"], 1.5)
        self.assertNotIn("Messages", self.sqs.receive_message(QueueUrl=self.cola))
        registros_crear = self.registros()
        self.assertEqual(self.publicador.manejar({"Records": registros_crear}, None),
                         {"batchItemFailures": []})
        tarea_id = tarea["tarea_id"]
        completo = self.llamar(self.tareas.completar, tarea_id=tarea_id, rol="miembro")
        self.assertEqual(completo["datos"]["proyecto"]["avance_pct"], 100)
        self.llamar(self.tareas.completar, 409, tarea_id=tarea_id, rol="miembro")
        self.llamar(self.tareas.actualizar, 409, tarea_id=tarea_id, cuerpo='{"estado":"pendiente"}')
        self.assertEqual(self.llamar(self.tareas.resumen)["datos"]["tareas_completadas"], 1)
        self.llamar(self.tareas.actualizar, tarea_id=tarea_id, cuerpo='{"nombre":"Hito listo"}')
        nuevos = self.registros()[len(registros_crear):]
        self.assertEqual(self.publicador.manejar({"Records": nuevos}, None),
                         {"batchItemFailures": []})
        mensajes = self.sqs.receive_message(QueueUrl=self.cola, MaxNumberOfMessages=10)["Messages"]
        eventos = [json.loads(json.loads(m["Body"])["Message"]) for m in mensajes]
        self.assertEqual([e["tipo"] for e in eventos], ["tarea.creada", "tarea.completada"])
        self.assertEqual([e["datos"]["tareas_completadas"] for e in eventos], [0, 1])
        self.assertEqual(eventos[1]["datos"]["proyecto_id"], 1)

    def test_reintento_sns_reutiliza_el_id_del_evento(self):
        self.crear()
        registros = self.registros()
        registro = next(r for r in registros if "evento_id" in r["dynamodb"].get("NewImage", {}))
        with patch.object(self.publicador, "publicar", side_effect=OSError("SNS temporal")):
            with self.assertLogs(self.publicador.logger, level="ERROR"):
                resultado = self.publicador.manejar({"Records": registros}, None)
        self.assertEqual(resultado["batchItemFailures"],
                         [{"itemIdentifier": registro["dynamodb"]["SequenceNumber"]}])
        for _ in range(2):
            self.publicador.manejar({"Records": [registro]}, None)
        mensajes = self.sqs.receive_message(QueueUrl=self.cola, MaxNumberOfMessages=10)["Messages"]
        ids = [json.loads(json.loads(m["Body"])["Message"])["evento_id"] for m in mensajes]
        self.assertEqual(ids, [registro["dynamodb"]["NewImage"]["evento_id"]["S"]] * 2)

    def test_transaccion_cancelada_no_completa_la_tarea(self):
        tarea_id = self.crear()["tarea_id"]
        clave_resumen = {"pk": "TENANT#t-acme", "sk": "PROYECTO#1#RESUMEN"}
        self.tabla.update_item(
            Key=clave_resumen, UpdateExpression="SET completadas = :texto",
            ExpressionAttributeValues={":texto": "invalido"},
        )
        from botocore.exceptions import ClientError
        with self.assertRaises(ClientError):
            self.tareas.completar(self.evento(tarea_id=tarea_id), None)
        item = self.llamar(self.tareas.obtener, tarea_id=tarea_id)["datos"]
        self.assertEqual(item["estado"], "pendiente")

    def test_entradas_invalidas_no_escriben(self):
        for cuerpo in ('{"nombre":', "[]", '{"nombre":" "}', '{"nombre":"A","horas_estimadas":-1}'):
            self.llamar(self.tareas.crear, 400, cuerpo=cuerpo)
        self.llamar(self.tareas.crear, 400, proyecto="abc", cuerpo='{"nombre":"A"}')
        self.llamar(self.tareas.listar, 400, consulta={"limite": "0"})
        self.llamar(self.tareas.listar, 400, consulta={"cursor": "PROYECTO#2#TAREA#x"})
        self.assertEqual(self.tabla.scan()["Count"], 0)
