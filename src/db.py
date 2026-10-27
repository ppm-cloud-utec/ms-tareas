"""Acceso a DynamoDB con aislamiento por particion de tenant."""
import os

import boto3

_dynamo = boto3.resource("dynamodb")


def tabla(nombre):
    return _dynamo.Table(os.environ[nombre])


def pk(tenant_id):
    """La partition key SIEMPRE empieza con el tenant.

    Asi una query no puede cruzar tenants ni por error.
    """
    return f"TENANT#{tenant_id}"
