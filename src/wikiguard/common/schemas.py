# Placeholder -- explicit Spark schemas for the Wikimedia recentchange SSE
# payload will live here.  Task 2.1 (Ingest) populates this module.
#
# Each schema will be a pyspark.sql.types.StructType constant, named after
# the stream it represents, e.g.:
#
#   RECENTCHANGE_SCHEMA = StructType([...])
#
# Keeping schemas here (rather than inline in notebooks or Auto Loader options)
# makes them importable across all layers and testable without a running cluster.
