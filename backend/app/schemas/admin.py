"""Schemas for the admin endpoints."""

import marshmallow as ma


class QueueStatsSchema(ma.Schema):
    name = ma.fields.String()
    queued = ma.fields.Integer()
    started = ma.fields.Integer()
    scheduled = ma.fields.Integer()
    failed = ma.fields.Integer()


class FailedJobSchema(ma.Schema):
    id = ma.fields.String()
    queue = ma.fields.String()
    function = ma.fields.String()
    enqueued_at = ma.fields.DateTime(allow_none=True)
    failed_at = ma.fields.DateTime(allow_none=True)
    retries_left = ma.fields.Integer()
    request_id = ma.fields.String(allow_none=True)
    error = ma.fields.String(
        metadata={"description": "Last line of the error, personal data removed"}
    )


class SchedulerStatusSchema(ma.Schema):
    last_heartbeat = ma.fields.DateTime(allow_none=True)
    age_seconds = ma.fields.Integer(allow_none=True)
    healthy = ma.fields.Boolean()


class JobsOverviewSchema(ma.Schema):
    queues = ma.fields.List(ma.fields.Nested(QueueStatsSchema))
    failed_jobs = ma.fields.List(ma.fields.Nested(FailedJobSchema))
    scheduler = ma.fields.Nested(SchedulerStatusSchema)


class RetriedJobSchema(ma.Schema):
    id = ma.fields.String()
    queue = ma.fields.String()
    status = ma.fields.String()
