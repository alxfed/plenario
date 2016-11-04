from collections import namedtuple
from datetime import datetime, timedelta

from marshmallow import fields, Schema
from marshmallow.validate import Range, ValidationError
from sqlalchemy.exc import DatabaseError, ProgrammingError, NoSuchTableError

from plenario.api.common import extract_first_geometry_fragment, make_fragment_str
from plenario.database import session
from plenario.models.SensorNetwork import NodeMeta, NetworkMeta, FeatureMeta, SensorMeta
from plenario.sensor_network.api.sensor_aggregate_functions import aggregate_fn_map

valid_sensor_aggs = ("minute", "hour", "day", "week", "month", "year")


def validate_network(network):
    if network.lower() not in NetworkMeta.index():
        raise ValidationError("Invalid network name: {}".format(network))


def validate_nodes(nodes):
    if isinstance(nodes, str):
        nodes = [nodes]
    valid_nodes = NodeMeta.index()
    for node in nodes:
        if node.lower() not in valid_nodes:
            raise ValidationError("Invalid node ID: {}".format(node))


def validate_features(features):
    if isinstance(features, str):
        features = [features]
    valid_features = FeatureMeta.index()
    for feature in features:
        feature = feature.split(".")[0].lower()
        if feature not in valid_features:
            raise ValidationError("Invalid feature of interest name: {}".format(feature))


def validate_sensors(sensors):
    if isinstance(sensors, str):
        sensors = [sensors]
    valid_sensors = SensorMeta.index()
    for sensor in sensors:
        sensor = sensor.lower()
        if sensor not in valid_sensors:
            raise ValidationError("Invalid sensor name: {}".format(sensor))


def validate_geom(geom):
    """Custom validator for geom parameter."""

    try:
        return extract_first_geometry_fragment(geom)
    except Exception as exc:
        raise ValidationError("Could not parse geojson: {}. {}".format(geom, exc))


class Validator(Schema):
    """Base validator object using Marshmallow. Don't be intimidated! As scary
    as the following block of code looks it's quite simple, and saves us from
    writing validators. Let's break it down...

    <FIELD_NAME> = fields.<TYPE>(default=<DEFAULT_VALUE>, validate=<VALIDATOR FN>)

    The validator, when instanciated, has a method called 'dump'.which expects a
    dictionary of arguments, where keys correspond to <FIELD_NAME>. The validator
    has a default <TYPE> checker, that along with extra <VALIDATOR FN>s will
    accept or reject the value associated with the key. If the value is missing
    or rejected, the validator will substitute it with the value specified by
    <DEFAULT_VALUE>."""

    network = fields.Str(allow_none=True, missing=None, default='array_of_things', validate=validate_network)
    nodes = fields.List(fields.Str(), default=None, missing=None, validate=validate_nodes)
    sensors = fields.List(fields.Str(), default=None, missing=None, validate=validate_sensors)
    feature = fields.Str(validate=validate_features)
    features = fields.List(fields.Str(), default=None, missing=None, validate=validate_features)

    geom = fields.Str(default=None, validate=validate_geom)
    start_datetime = fields.DateTime(default=lambda: datetime.utcnow() - timedelta(days=90))
    end_datetime = fields.DateTime(default=lambda: datetime.utcnow())
    filter = fields.Str(allow_none=True, missing=None, default=None)
    limit = fields.Integer(default=1000)
    offset = fields.Integer(default=0, validate=Range(0))


class NodeAggregateValidator(Validator):

    node = fields.Str(required=True, validate=validate_nodes)
    feature = fields.List(fields.Str(), validate=validate_features, required=True)
    function = fields.Str(missing="avg", default="avg", validate=lambda x: x.lower() in aggregate_fn_map)

    agg = fields.Str(default="hour", missing="hour", validate=lambda x: x in valid_sensor_aggs)
    start_datetime = fields.DateTime(default=lambda: datetime.utcnow() - timedelta(days=1))
    end_datetime = fields.DateTime(default=lambda: datetime.utcnow())


class RequiredFeatureValidator(Validator):

    feature = fields.Str(validate=validate_features, required=True)


class DatadumpValidator(Validator):

    start_datetime = fields.DateTime(default=lambda: datetime.utcnow() - timedelta(days=7))
    end_datetime = fields.DateTime(default=lambda: datetime.utcnow())
    limit = fields.Integer(default=None)


class NearMeValidator(Validator):

    lat = fields.Float(required=True)
    lng = fields.Float(required=True)
    feature = fields.Str(required=True, validate=validate_features)
    datetime = fields.DateTime(default=datetime.utcnow)


# ValidatorResult
# ===============
# Many methods in response.py rely on information that used to be provided
# by the old ParamValidator attributes. This namedtuple carries that same
# info around, and allows me to not have to rewrite any response code.

ValidatorResult = namedtuple('ValidatorResult', 'data errors warnings')


# converters
# ==========
# Callables which are used to convert request arguments to their correct types.

converters = {
    'geom': lambda x: make_fragment_str(extract_first_geometry_fragment(x)),
    'start_datetime': lambda x: x.isoformat().split('+')[0],
    'end_datetime': lambda x: x.isoformat().split('+')[0]
}


def convert(request_args):
    """Convert a dictionary of arguments from strings to their types. How the
    values are converted are specified by the converters dictionary defined
    above.

    :param request_args: dictionary of request arguments

    :returns: converted dictionary"""

    for key, value in list(request_args.items()):
        try:
            request_args[key] = converters[key](value)
        except (KeyError, TypeError, AttributeError, NoSuchTableError):
            pass
        except (DatabaseError, ProgrammingError):
            # Failed transactions, which we do expect, can cause
            # a DatabaseError with Postgres. Failing to rollback
            # prevents further queries from being carried out.
            session.rollback()

