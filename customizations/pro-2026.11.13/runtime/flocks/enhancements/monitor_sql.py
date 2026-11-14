"""Translate the monitor's bounded SQLite dialect to PostgreSQL.

Only monitoring calls use this adapter. Native task, licence and ownership
operations keep going through the Pro provider unchanged.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).with_name('_vendor')))
from sqlglot import exp, parse_one


def call(name, *args):
    return exp.Anonymous(this=name, expressions=list(args))


def _json_value(node):
    if isinstance(node, exp.JSONExtract):
        return call('zwmon_raw_value', node.this.copy(), exp.Literal.string(node.expression.sql(dialect='sqlite')[1:-1]))
    if isinstance(node, exp.JSONObject):
        args = []
        for pair in node.expressions:
            args.extend([pair.this.copy(), _json_value(pair.expression)])
        return call('json_build_object', *args)
    if isinstance(node, exp.Anonymous) and node.name.lower() == 'json':
        return call('zwmon_parse', *[x.copy() for x in node.expressions])
    return node.copy()


def _has_extract(node):
    if isinstance(node, (exp.JSONExtract, exp.JSONType)):
        return True
    if isinstance(node, exp.Coalesce):
        return any(_has_extract(x) for x in (node.this, *node.expressions))
    if isinstance(node, exp.Case):
        return any(_has_extract(x.args.get('true')) for x in node.args.get('ifs',())) or _has_extract(node.args.get('default'))
    if isinstance(node, exp.Paren):
        return _has_extract(node.this)
    return False


@lru_cache(maxsize=1024)
def _compile(sql: str, kinds: tuple) -> tuple[str, tuple]:
    from flocks.db.markers import rewrite_markers
    sql = rewrite_markers(sql, lambda n: f':zwp{n}')
    tree = parse_one(sql, read='sqlite')
    if isinstance(tree, exp.Insert) and tree.args.get('conflict'):
        target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        for assignment in tree.args['conflict'].expressions:
            for column in assignment.expression.find_all(exp.Column):
                if not column.table:
                    column.set('table', exp.to_identifier(target.name))
    for node in list(tree.find_all(exp.Placeholder)):
        index = int(node.this[3:])-1
        kind = kinds[index]
        if kind == 'NULL' and isinstance(node.parent, exp.Is):
            kind = 'TEXT'
        if kind != 'NULL':
            value = exp.Cast(this=node.copy(), to=exp.DataType.build(kind))
            if kind in {'BIGINT','DOUBLE'} and isinstance(node.parent, (exp.And, exp.Or)):
                value = exp.NEQ(this=value, expression=exp.Literal.number(0))
            node.replace(value)
    # SQLite compares extracted numbers as numbers. The scalar helper returns
    # text so both JSON strings and SQL text can still be used by substr/LIKE.
    for node in list(tree.walk()):
        if isinstance(node, exp.Binary) and _has_extract(node.this):
            node.set('expression', exp.Cast(this=node.expression.copy(), to=exp.DataType.build('TEXT')))
        if isinstance(node, exp.In) and _has_extract(node.this):
            node.set('expressions', [exp.Cast(this=x.copy(), to=exp.DataType.build('TEXT')) for x in node.expressions])
        if isinstance(node, exp.Coalesce) and _has_extract(node):
            node.set('this', exp.Cast(this=node.this.copy(), to=exp.DataType.build('TEXT')))
            node.set('expressions', [exp.Cast(this=x.copy(), to=exp.DataType.build('TEXT')) for x in node.expressions])

    def convert(node):
        if isinstance(node, exp.Is) and not isinstance(node.expression, (exp.Null, exp.Boolean)):
            return exp.NullSafeEQ(this=node.this.copy().transform(convert), expression=node.expression.copy().transform(convert))
        if isinstance(node, exp.Cast) and node.to.is_type('INT') and isinstance(node.this, exp.Column):
            return call('zwmon_integer', node.this.copy())
        if isinstance(node, exp.Alias) and isinstance(node.args.get('alias'), exp.Identifier):
            node.args['alias'].set('quoted', True)
            return node
        if isinstance(node, exp.Substring):
            for field in ('start', 'length'):
                value = node.args.get(field)
                if value is not None:
                    node.set(field, exp.Cast(this=value.copy(), to=exp.DataType.build('INT')))
            return node
        if isinstance(node, exp.JSONExtract):
            return call('zwmon_scalar', node.this.copy().transform(convert), exp.Literal.string(node.expression.sql(dialect='sqlite')[1:-1]))
        if isinstance(node, exp.JSONType):
            return call('zwmon_type', node.this.copy().transform(convert), node.expression.copy() if node.expression else exp.Literal.string('$'))
        if isinstance(node, exp.JSONObject):
            return exp.Cast(this=_json_value(node).transform(convert), to=exp.DataType.build('TEXT'))
        if isinstance(node, exp.JSONSet):
            path, value = [x.copy().transform(convert) for x in node.expressions]
            if isinstance(value, exp.Literal) and value.is_string:
                value = exp.Cast(this=value, to=exp.DataType.build('TEXT'))
            return call('zwmon_set', node.this.copy().transform(convert), path, call('to_jsonb', value))
        if isinstance(node, exp.TimeToStr):
            value = node.this
            if isinstance(value, exp.TsOrDsToTimestamp):
                value = value.this
            return call('zwmon_timestamp', value.copy().transform(convert))
        if isinstance(node, exp.Anonymous):
            name = node.name.lower()
            args = [x.copy().transform(convert) for x in node.expressions]
            if name == 'json_group_array':
                return exp.Cast(this=exp.Coalesce(this=call('json_agg', _json_value(node.expressions[0]).transform(convert)), expressions=[exp.Cast(this=exp.Literal.string('[]'), to=exp.DataType.build('JSON'))]), to=exp.DataType.build('TEXT'))
            if name in {'json_valid', 'json_array_length', 'json_each', 'json_tree', 'julianday'}:
                return call({'json_valid':'zwmon_valid', 'json_array_length':'zwmon_length', 'json_each':'zwmon_each', 'json_tree':'zwmon_tree', 'julianday':'zwmon_julianday'}[name], *args)
            if name == 'json':
                return call('zwmon_parse', *args)
            if name == 'monitor_event_version':
                return call('zwmon_event_version', *args)
        return node

    tree = tree.transform(convert)
    if isinstance(tree, exp.Insert) and str(tree.args.get('alternative', '')).upper() == 'IGNORE':
        tree.set('alternative', None)
        tree.set('conflict', exp.OnConflict(action=exp.Var(this='DO NOTHING')))
    if isinstance(tree, exp.Insert) and str(tree.args.get('alternative', '')).upper() == 'REPLACE':
        target = tree.this
        if not isinstance(target, exp.Schema):
            raise ValueError('Monitoring replacement requires explicit columns')
        keys = {'monitor_installations':('owner','scope'), 'monitor_investigations':('owner','project','event_key'),
                'monitor_observations':('attempt_id','event_key'), 'monitor_cursors':('owner','scope','device')}
        primary = keys[target.this.name]
        tree.set('alternative', None)
        tree.set('conflict', exp.OnConflict(conflict_keys=[exp.to_identifier(x) for x in primary],
            action=exp.Var(this='DO UPDATE'), expressions=[exp.EQ(this=exp.column(c.name),
                expression=exp.column(c.name, table='excluded')) for c in target.expressions if c.name not in primary]))
    result = tree.sql(dialect='postgres')
    order = []
    def placeholder(match):
        order.append(int(match[1])-1)
        return '?'
    result = re.sub(r'%\(zwp(\d+)\)s', placeholder, result)
    return result, tuple(order)


def prepare(sql, args):
    kinds = tuple('NULL' if v is None else 'BOOLEAN' if type(v) is bool else
                  'BIGINT' if type(v) is int else 'DOUBLE' if type(v) is float else 'TEXT' for v in args)
    statement, order = _compile(sql, kinds)
    return statement, tuple(args[i] for i in order)


def translate(sql):
    return _compile(sql, ())[0]


FUNCTIONS = r'''
CREATE OR REPLACE FUNCTION zwmon_integer(v text) RETURNS integer LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RETURN v::integer; EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN 0; END $$;
CREATE OR REPLACE FUNCTION zwmon_parse(v text) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RETURN v::jsonb; EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION zwmon_raw_value(v text,p text DEFAULT '$') RETURNS json LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN RETURN CASE WHEN p='$' THEN v::json ELSE v::json #> string_to_array(regexp_replace(substr(p,3),'\[([0-9]+)\]','.\1','g'),'.') END; EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION zwmon_valid(v text) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
 SELECT zwmon_parse(v) IS NOT NULL $$;
CREATE OR REPLACE FUNCTION zwmon_value(v text,p text DEFAULT '$') RETURNS jsonb LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN p='$' THEN zwmon_parse(v) ELSE zwmon_parse(v) #> string_to_array(regexp_replace(substr(p,3),'\[([0-9]+)\]','.\1','g'),'.') END $$;
CREATE OR REPLACE FUNCTION zwmon_scalar(v text,p text DEFAULT '$') RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE jsonb_typeof(j) WHEN 'null' THEN NULL WHEN 'boolean' THEN CASE WHEN j='true'::jsonb THEN '1' ELSE '0' END ELSE j #>> '{}' END FROM (SELECT zwmon_value(v,p) j) s $$;
CREATE OR REPLACE FUNCTION zwmon_type(v text,p text DEFAULT '$') RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE jsonb_typeof(j) WHEN 'number' THEN CASE WHEN j::text ~ '^-?[0-9]+$' THEN 'integer' ELSE 'real' END WHEN 'boolean' THEN j::text WHEN 'string' THEN 'text' ELSE jsonb_typeof(j) END FROM (SELECT zwmon_value(v,p) j) s $$;
CREATE OR REPLACE FUNCTION zwmon_length(v text,p text DEFAULT '$') RETURNS integer LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN jsonb_typeof(j)='array' THEN jsonb_array_length(j) ELSE 0 END FROM (SELECT zwmon_value(v,p) j) s $$;
CREATE OR REPLACE FUNCTION zwmon_each(v text,p text DEFAULT '$') RETURNS TABLE(key text,value text,type text) LANGUAGE sql IMMUTABLE AS $$
 SELECT e.key,zwmon_scalar(e.value::text),zwmon_type(e.value::text) FROM (SELECT zwmon_value(v,p) j) s CROSS JOIN LATERAL (
 SELECT o.key,o.value FROM jsonb_each(CASE WHEN jsonb_typeof(j)='object' THEN j ELSE '{}'::jsonb END) o
 UNION ALL SELECT (a.ordinality-1)::text,a.value FROM jsonb_array_elements(CASE WHEN jsonb_typeof(j)='array' THEN j ELSE '[]'::jsonb END) WITH ORDINALITY a(value,ordinality)) e $$;
CREATE OR REPLACE FUNCTION zwmon_tree(v text) RETURNS TABLE(key text,value text,type text) LANGUAGE sql IMMUTABLE AS $$
 WITH RECURSIVE walk(key,value,type) AS (SELECT NULL::text,zwmon_scalar(v),zwmon_type(v) UNION ALL SELECT e.key,e.value,e.type FROM walk w CROSS JOIN LATERAL zwmon_each(w.value) e WHERE w.type IN ('object','array')) SELECT * FROM walk $$;
CREATE OR REPLACE FUNCTION zwmon_julianday(v text) RETURNS double precision LANGUAGE plpgsql STABLE AS $$
BEGIN RETURN extract(epoch FROM v::timestamptz)/86400.0+2440587.5; EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION zwmon_timestamp(v double precision) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT to_char(to_timestamp((v-2440587.5)*86400) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;
CREATE OR REPLACE FUNCTION zwmon_set(v text,p text,new_value jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT jsonb_set(zwmon_parse(v),string_to_array(substr(p,3),'.'),new_value,true)::text $$;
CREATE OR REPLACE FUNCTION zwmon_python_json(j jsonb) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE result text;
BEGIN
 IF j IS NULL THEN RETURN 'null'; END IF;
 IF jsonb_typeof(j)='object' THEN
  SELECT '{'||COALESCE(string_agg(to_json(key)::text||': '||zwmon_python_json(value),', ' ORDER BY key COLLATE "C"),'')||'}' INTO result FROM jsonb_each(j);
 ELSIF jsonb_typeof(j)='array' THEN
  SELECT '['||COALESCE(string_agg(zwmon_python_json(value),', ' ORDER BY ordinality),'')||']' INTO result FROM jsonb_array_elements(j) WITH ORDINALITY;
 ELSE result := j::text;
 END IF;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION zwmon_event_version(v text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT encode(sha256(convert_to('{'||string_agg(to_json(k)::text||': '||zwmon_python_json(
  CASE WHEN k='dealStatus' THEN 'null'::jsonb WHEN k='basics' AND jsonb_typeof(j->k)='object' THEN (j->k)-'deal_status'-'dealStatus' ELSE j->k END
 ),', ' ORDER BY n)||'}','UTF8')),'hex') FROM (SELECT zwmon_parse(v) j) s CROSS JOIN unnest(ARRAY['key','id','device','host','name','endTime','dealStatus','incidentSeverity','riskLevel','incidentThreatClass','incidentThreatType','alertIds','development_sample','basics','startTime']) WITH ORDINALITY keys(k,n) $$;
'''
