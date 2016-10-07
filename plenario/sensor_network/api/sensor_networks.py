import json
import math
import os
from collections import OrderedDict
from datetime import datetime

from dateutil.parser import parse
from flask import request, make_response
from shapely import wkb
from sqlalchemy import MetaData, Table, func as sqla_fn

from plenario.api.common import cache, crossdomain
from plenario.api.common import make_cache_key, unknown_object_json_handler
from plenario.api.jobs import get_status, set_status, set_flag
from plenario.api.response import make_error
from plenario.database import fast_count, windowed_query
from plenario.database import session, redshift_session, redshift_engine
from plenario.models import DataDump
from plenario.sensor_network.api.sensor_response import json_response_base, bad_request
from plenario.sensor_network.api.sensor_validator import Validator, validate, NodeAggregateValidator, RequiredFeatureValidator
from plenario.sensor_network.sensor_models import NetworkMeta, NodeMeta, FeatureOfInterest, Sensor
from sensor_aggregate_functions import aggregate_fn_map

# Cache timeout of 5 mintutes
CACHE_TIMEOUT = 60 * 10


@cache.cached(timeout=CACHE_TIMEOUT, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_network_metadata(network=None):
    """Return metadata for some network. If no network_name is specified, the
    default is to return metadata for all sensor networks.

    :endpoint: /sensor-networks/<network-name>
    :param network: (str) network name
    :returns: (json) response"""

    args = {"network": network.lower() if network else None}

    fields = ('network',)
    validated_args = validate(Validator(only=fields), args)
    if validated_args.errors:
        return bad_request(validated_args.errors)

    return get_metadata("network", validated_args)


@cache.cached(timeout=CACHE_TIMEOUT, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_node_metadata(network, node=None):
    """Return metadata about nodes for some network. If no node_id or
    location_geom__within is specified, the default is to return metadata
    for all nodes within the network.

    :endpoint: /sensor-networks/<network-name>/nodes/<node>
    :param network: (str) network that exists in sensor__network_metadata
    :param node: (str) node that exists in sensor__node_metadata
    :returns: (json) response"""

    args = dict(request.args.to_dict(), **{"network": network, "nodes": [node] if node else None})

    fields = ('network', 'nodes', 'geom')
    validated_args = validate(Validator(only=fields), args)
    if validated_args.errors:
        return bad_request(validated_args.errors)
    validated_args = sanitize_validated_args(validated_args)

    return get_metadata("nodes", validated_args)


@cache.cached(timeout=CACHE_TIMEOUT, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_sensor_metadata(network, sensor=None):
    """Return metadata for all sensors within a network. Sensors can also be
    be filtered by various other properties. If no single sensor is specified,
    the default is to return metadata for all sensors within the network.

    :endpoint: /sensor-networks/<network_name>/sensors/<sensor>
    :param network: (str) name from sensor__network_metadata
    :param sensor: (str) name from sensor__sensors
    :returns: (json) response"""

    args = dict(request.args.to_dict(), **{"network": network, "sensors": [sensor] if sensor else None})

    fields = ('network', 'sensors', 'geom')
    validated_args = validate(Validator(only=fields), args)
    if validated_args.errors:
        return bad_request(validated_args.errors)
    validated_args = sanitize_validated_args(validated_args)

    return get_metadata("sensors", validated_args)


@cache.cached(timeout=CACHE_TIMEOUT, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_feature_metadata(network, feature=None):
    """Return metadata about features for some network. If no feature is
    specified, return metadata about all features within the network.

    :endpoint: /sensor-networks/<network_name>/features_of_interest/<feature>
    :param network: (str) network name from sensor__network_metadata
    :param feature: (str) name from sensor__features_of_interest
    :returns: (json) response"""

    args = dict(request.args.to_dict(), **{"network": network, "features": [feature] if feature else None})

    fields = ('network', 'features', 'geom')
    validated_args = validate(Validator(only=fields), args)
    if validated_args.errors:
        return bad_request(validated_args.errors)

    return get_metadata("features", validated_args)


@crossdomain(origin="*")
def get_observations(network):
    """Return raw sensor network observations for a single feature within
    the specified network.

    :endpoint: /sensor-networks/<network-name>/query?feature=<feature>
    :param network: (str) network name
    :returns: (json) response"""

    args = dict(request.args.to_dict(), **{"network": network})

    fields = ('network', 'nodes', 'start_datetime', 'end_datetime', 'geom',
              'feature', 'sensors', 'limit', 'offset')
    validated_args = validate(RequiredFeatureValidator(only=fields), args)
    if validated_args.errors:
        return bad_request(validated_args.errors)
    validated_args = sanitize_args(validated_args)

    observation_queries = get_observation_queries(validated_args)
    if type(observation_queries) != list:
        return observation_queries
    return run_observation_queries(validated_args, observation_queries)


@cache.cached(timeout=CACHE_TIMEOUT * 10, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_observations_download(network_name):
    """Queue a datadump job for raw sensor network observations and return
    links to check on its status and eventual download. Has a longer cache
    timeout than the other endpoints -- datadumps are alot of work.

    :endpoint: /sensor-networks/<network-name>/download
    :param network_name: (str) network name
    :returns: (json) response"""

    fields = ('network_name', 'nodes', 'start_datetime', 'end_datetime',
              'limit', 'location_geom__within', 'features_of_interest',
              'sensors', 'offset')

    args = request.args.to_dict()
    args.update({"network_name": network_name})

    if 'nodes' in args:
        args['nodes'] = args['nodes'].split(',')
        args["nodes"] = [n.lower() for n in args["nodes"]]

    if 'sensors' in args:
        args['sensors'] = args['sensors'].split(',')
        args["sensors"] = [s.lower() for s in args["sensors"]]

    if 'features_of_interest' in args:
        args['features_of_interest'] = args['features_of_interest'].split(',')
        args["features_of_interest"] = [f.lower() for f in args["features_of_interest"]]

    validator = Validator(only=fields)
    validated_args = validate(validator, args)
    if validated_args.errors:
        return bad_request(validated_args.errors)

    from plenario.api.jobs import make_job_response

    validated_args.data["query_fn"] = "aot_point"
    validated_args.data["datadump_urlroot"] = request.url_root
    validated_args = sanitize_validated_args(validated_args)
    job = make_job_response("observation_datadump", validated_args)
    return job


@cache.cached(timeout=CACHE_TIMEOUT, key_prefix=make_cache_key)
@crossdomain(origin="*")
def get_aggregations(network):
    """Aggregate individual node observations up to larger units of time.
    Do so by applying aggregate functions on all observations found within
    a specified window of time.

    :endpoint: /sensor-networks/<network-name>/aggregate
    :param network: (str) from sensor__network_metadata
    :returns: (json) response"""

    fields = ("network", "node", "sensors", "features", "function",
              "start_datetime", "end_datetime", "agg")

    request_args = dict(request.args.to_dict(), **{"network": network})
    request_args = sanitize_args(request_args)
    validated_args = validate(NodeAggregateValidator(only=fields), request_args)
    if validated_args.errors:
        return bad_request(validated_args.errors)
    validated_args = sanitize_validated_args(validated_args)

    try:
        result = aggregate_fn_map[validated_args.data.get("function")](validated_args)
    except ValueError as err:
        # In the case of proper syntax, but params which lead to an
        # unprocesseable query.
        return make_error(err.message, 422)
    return jsonify(validated_args, result)


def observation_query(args, table):
    nodes = args.data.get("nodes")
    start_dt = args.data.get("start_datetime")
    end_dt = args.data.get("end_datetime")
    sensors = args.data.get("sensors")
    limit = args.data.get("limit")
    offset = args.data.get("offset")

    q = redshift_session.query(table)
    q = q.filter(table.c.datetime >= start_dt)
    q = q.filter(table.c.datetime < end_dt)

    q = q.filter(sqla_fn.lower(table.c.node_id).in_(nodes)) if nodes else q
    q = q.filter(sqla_fn.lower(table.c.sensor).in_(sensors)) if sensors else q
    q = q.limit(limit) if args.data["limit"] else q
    q = q.offset(offset) if args.data["offset"] else q

    return q


def get_raw_metadata(target, args):
    metadata_args = {
        "target": target,
        "network": args.data.get("network"),
        "nodes": args.data.get("nodes"),
        "sensors": args.data.get("sensors"),
        "features": args.data.get("features"),
        "geom": args.data.get("geom")
    }
    return metadata(**metadata_args)


def get_metadata(target, args):
    args = remove_null_keys(args)
    raw_metadata = get_raw_metadata(target, args)
    if type(raw_metadata) != list:
        return raw_metadata
    return jsonify(args, [format_metadata[target](record) for record in raw_metadata])


def format_network_metadata(network):
    network_response = {
        'name': network.name,
        'features_of_interest': FeatureOfInterest.index(network.name),
        'nodes': NodeMeta.index(network.name),
        'sensors': Sensor.index(network.name),
        'info': network.info
    }

    return network_response


def format_node_metadata(node):
    node_response = {
        "type": "Feature",
        'geometry': {
            "type": "Point",
            "coordinates": [
                wkb.loads(bytes(node.location.data)).x,
                wkb.loads(bytes(node.location.data)).y
            ],
        },
        "properties": {
            "id": node.id,
            "network_name": node.sensor_network,
            "sensors": [sensor.name for sensor in node.sensors],
            "info": node.info,
        },
    }

    return node_response


def format_sensor_metadata(sensor):
    sensor_response = {
        'name': sensor.name,
        'observed_properties': sensor.observed_properties.values(),
        'info': sensor.info
    }

    return sensor_response


def format_feature_metadata(feature):
    feature_response = {
        'name': feature.name,
        'observed_properties': feature.observed_properties,
    }

    return feature_response


format_metadata = {
    "network": format_network_metadata,
    "nodes": format_node_metadata,
    "sensors": format_sensor_metadata,
    "features": format_feature_metadata
}


def format_observation(obs, table):
    obs_response = {
        'node_id': obs.node_id,
        'meta_id': obs.meta_id,
        'datetime': obs.datetime.isoformat().split('+')[0],
        'sensor': obs.sensor,
        'feature_of_interest': table.name,
        'results': {}
    }

    for prop in (set([c.name for c in table.c]) - {'node_id', 'datetime', 'sensor', 'meta_id'}):
        obs_response['results'][prop] = getattr(obs, prop)

    return obs_response


def get_observation_queries(args):

    args = sanitize_validated_args(args)

    tables = []
    meta = MetaData()

    result = get_raw_metadata("features", args)
    if type(result) != list:
        return result

    for feature in result:
        tables.append(Table(
            feature.name, meta,
            autoload=True,
            autoload_with=redshift_engine
        ))

    return [(observation_query(args, table), table) for table in tables]


def run_observation_queries(args, queries):

    data = list()
    for query, table in queries:
        data += [format_observation(obs, table) for obs in query.all()]

    remove_null_keys(args)
    if 'geom' in args.data:
        args.data.pop('geom')
    data.sort(key=lambda x: parse(x["datetime"]))

    return jsonify(args, data)


def get_observation_datadump(args):

    request_id = args.data.get("jobsframework_ticket")
    observation_queries = get_observation_queries(args)

    if type(observation_queries) != list:
        return observation_queries

    row_count = 0
    for query, table in observation_queries:
        row_count += fast_count(query)

    chunk_size = 1000.0
    chunk_count = math.ceil(row_count / chunk_size)
    chunk_number = 1

    chunk = list()
    features = set()
    for query, table in observation_queries:

        features.add(table.name.lower())
        for row in windowed_query(query, table.c.datetime, chunk_size):
            chunk.append(format_observation(row, table))

            if len(chunk) > chunk_size:
                store_chunk(chunk, chunk_count, chunk_number, request_id)
                chunk = list()
                chunk_number += 1

    if len(chunk) > 0:
        store_chunk(chunk, chunk_count, chunk_number, request_id)

    meta_chunk = '{{"startTime": "{}", "endTime": "{}", "workers": {}, "features": {}}}'.format(
        get_status(request_id)["meta"]["startTime"],
        str(datetime.now()),
        json.dumps([args.data["jobsframework_workerid"]]),
        json.dumps(list(features))
    )

    dump = DataDump(request_id, request_id, 0, chunk_count, meta_chunk)

    session.add(dump)
    try:
        session.commit()
    except Exception as e:
        session.rollback()
        raise e

    return {"url": args.data["datadump_urlroot"] + "v1/api/datadump/" + request_id}


def store_chunk(chunk, chunk_count, chunk_number, request_id):

    datadump_part = DataDump(
        id=os.urandom(16).encode('hex'),
        request=request_id,
        part=chunk_number,
        total=chunk_count,
        data=json.dumps(chunk, default=str)
    )

    session.add(datadump_part)

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        raise e

    status = get_status(request_id)
    status["progress"] = {"done": chunk_number, "total": chunk_count}
    set_status(request_id, status)

    # Supress datadump cleanup
    set_flag(request_id + "_suppresscleanup", True, 10800)


def metadata(target, network=None, nodes=None, sensors=None, features=None, geom=None):

    meta_levels = OrderedDict([
        ("network", network),
        ("nodes", nodes),
        ("sensors", sensors),
        ("features", features),
    ])

    for i, key in enumerate(meta_levels):
        current_state = meta_levels.items()
        value = meta_levels[key]

        if key == "network":
            meta_levels[key] = filter_meta(key, [], value, geom)
        else:
            meta_levels[key] = filter_meta(key, current_state[i - 1], value, geom)

        if not meta_levels[key]:
            msg = "Given your selection, {}: {} are available and from these no " \
                  "valid {} could be found".format(
                      current_state[i - 1][0],
                      current_state[i - 1][1],
                      target
                  )
            try:
                return bad_request(msg)
            except RuntimeError:
                raise ValueError(msg)

        if key == target:
            return meta_levels[key]


def filter_meta(meta_level, upper_filter_values, filter_values, geojson):
    """TODO: Docs please."""
    meta_queries = {
        "network": (session.query(NetworkMeta), NetworkMeta),
        "nodes": (session.query(NodeMeta), NodeMeta),
        "sensors": (session.query(Sensor), Sensor),
        "features": (session.query(FeatureOfInterest), FeatureOfInterest)
    }

    query, table = meta_queries[meta_level]
    upper_filter_values = upper_filter_values[1] if upper_filter_values else None

    valid_values = []
    if meta_level == "nodes":
        for network in upper_filter_values:
            valid_values += [node.id for node in network.nodes]
        if geojson:
            geom = NodeMeta.location.ST_Within(sqla_fn.ST_GeomFromGeoJSON(geojson))
            query = query.filter(geom)
    elif meta_level == "sensors":
        for node in upper_filter_values:
            valid_values += [sensor.name for sensor in node.sensors]
    elif meta_level == "features":
        for sensor in upper_filter_values:
            valid_values += [p.split(".")[0] for p in sensor.observed_properties.values()]

    if type(filter_values) != list and filter_values is not None:
        filter_values = [filter_values]

    if meta_level == "network" and not filter_values:
        return query.all()
    elif not filter_values and valid_values:
        filter_values = valid_values

    try:
        return query.filter(table.name.in_(filter_values)).all()
    except AttributeError:
        return query.filter(table.id.in_(filter_values)).all()


def jsonify(args, data):
    resp = json_response_base(args, data, args.data)
    resp = make_response(json.dumps(resp, default=unknown_object_json_handler), 200)
    resp.headers['Content-Type'] = 'application/json'
    return resp


def remove_null_keys(args):
    null_keys = [k for k in args.data if args.data[k] is None]
    for key in null_keys:
        del args.data[key]
    return args


def sanitize_args(args):
    for k in args:
        try:
            args[k] = args[k].lower()
        except AttributeError:
            continue
        if k in {"nodes", "sensors", "features"}:
            args[k] = args[k].split(",")
        if "+" in args[k]:
            args[k] = args[k].split("+")[0]
    return args


def sanitize_validated_args(args):
    args = remove_null_keys(args)
    for k in args.data:
        try:
            args.data[k] = args.data[k].lower()
        except AttributeError:
            continue
        if k in {"nodes", "sensors", "features"}:
            args.data[k] = args.data[k].split(",")
        if "+" in args.data[k]:
            args.data[k] = args.data[k].split("+")[0]
    return args
