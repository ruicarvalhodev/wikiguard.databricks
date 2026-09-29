"""
Explicit Spark schemas for Wikimedia SSE payloads.

Keeping schemas here (rather than inline in notebooks or Auto Loader options)
makes them importable across all layers and avoids schema-inference surprises
when new event types arrive with different fields.
"""
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
)

# Schema covering only the fields used by the silver transform.
# Unknown fields in the raw payload are silently ignored by from_json.
RECENTCHANGE_SCHEMA = StructType([
    StructField("meta", StructType([
        StructField("id",     StringType()),
        StructField("dt",     StringType()),
        StructField("domain", StringType()),
    ])),
    StructField("type",        StringType()),
    StructField("namespace",   LongType()),
    StructField("title",       StringType()),
    StructField("comment",     StringType()),
    StructField("user",        StringType()),
    StructField("bot",         BooleanType()),
    StructField("minor",       BooleanType()),
    StructField("patrolled",   BooleanType()),
    StructField("wiki",        StringType()),
    StructField("server_name", StringType()),
    StructField("length", StructType([
        StructField("old", LongType()),
        StructField("new", LongType()),
    ])),
    StructField("revision", StructType([
        StructField("old", LongType()),
        StructField("new", LongType()),
    ])),
])
