# Logging + monitoring: log-based metrics over the app's structured JSON logs, an uptime
# check on /health, alert policies, and a dashboard. See OBSERVABILITY.md for the rationale.

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "OrbitMesh on-call email"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

locals {
  channels   = var.alert_email == "" ? [] : [google_monitoring_notification_channel.email[0].id]
  run_filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.service_name}\""
}

# --- log-based metrics (the app logs one JSON object per turn to stderr) ---------------
resource "google_logging_metric" "turns" {
  name   = "orbitmesh/turns"
  filter = "${local.run_filter} AND jsonPayload.event=\"turn\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key         = "action"
      value_type  = "STRING"
      description = "ask / instruct / resolved / escalate"
    }
  }
  label_extractors = { action = "EXTRACT(jsonPayload.action)" }
}

resource "google_logging_metric" "guardrail_blocks" {
  name   = "orbitmesh/guardrail_output_blocks"
  filter = "${local.run_filter} AND jsonPayload.event=\"guardrail.output\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "turn_errors" {
  name   = "orbitmesh/turn_errors"
  filter = "${local.run_filter} AND (jsonPayload.event=\"turn.error\" OR jsonPayload.event=\"llm.error\" OR severity>=ERROR)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "turn_latency" {
  name   = "orbitmesh/turn_latency_ms"
  filter = "${local.run_filter} AND jsonPayload.event=\"turn\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  value_extractor = "EXTRACT(jsonPayload.latency_ms)"
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 20
      growth_factor      = 1.6
      scale              = 100
    }
  }
}

# --- uptime + alerts ----------------------------------------------------------------------
resource "google_monitoring_uptime_check_config" "healthz" {
  display_name = "${var.service_name} /health"
  timeout      = "10s"
  period       = "300s"
  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = replace(google_cloud_run_v2_service.app.uri, "https://", "") }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  display_name          = "${var.service_name}: /health failing"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "uptime check failed from 2+ regions for 5 min"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.labels.check_id=\"${google_monitoring_uptime_check_config.healthz.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
    }
  }
}

resource "google_monitoring_alert_policy" "error_rate" {
  display_name          = "${var.service_name}: 5xx / turn errors"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "Cloud Run 5xx > 5% of requests over 10 min"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.service_name}\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "600s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }
  conditions {
    display_name = "turn / LLM errors logged"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turn_errors.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "latency" {
  display_name          = "${var.service_name}: p95 turn latency > 15s"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "p95 turn latency"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turn_latency.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 15000
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_PERCENTILE_95"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "guardrail_spike" {
  display_name          = "${var.service_name}: output guardrail blocks spiking"
  combiner              = "OR"
  notification_channels = local.channels
  conditions {
    display_name = "> 10 blocked drafts in 15 min (prompt regression or attack)"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.guardrail_blocks.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "900s"
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

# --- dashboard ---------------------------------------------------------------------------
resource "google_monitoring_dashboard" "main" {
  dashboard_json = jsonencode({
    displayName = "OrbitMesh Support Assistant"
    mosaicLayout = {
      columns = 12
      tiles = [
        {
          xPos = 0, yPos = 0, width = 6, height = 4
          widget = {
            title = "Turns per minute by action"
            xyChart = { dataSets = [{ plotType = "STACKED_BAR", timeSeriesQuery = { timeSeriesFilter = {
              filter      = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turns.name}\" resource.type=\"cloud_run_revision\""
              aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_SUM", crossSeriesReducer = "REDUCE_SUM", groupByFields = ["metric.label.action"] }
            } } }] }
          }
        },
        {
          xPos = 6, yPos = 0, width = 6, height = 4
          widget = {
            title = "Turn latency (p50 / p95, ms)"
            xyChart = { dataSets = [
              { plotType = "LINE", legendTemplate = "p50", timeSeriesQuery = { timeSeriesFilter = {
                filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turn_latency.name}\" resource.type=\"cloud_run_revision\""
              aggregation = { alignmentPeriod = "300s", perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_PERCENTILE_50" } } } },
              { plotType = "LINE", legendTemplate = "p95", timeSeriesQuery = { timeSeriesFilter = {
                filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turn_latency.name}\" resource.type=\"cloud_run_revision\""
              aggregation = { alignmentPeriod = "300s", perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_PERCENTILE_95" } } } }
            ] }
          }
        },
        {
          xPos = 0, yPos = 4, width = 4, height = 4
          widget = {
            title = "Guardrail output blocks"
            xyChart = { dataSets = [{ plotType = "LINE", timeSeriesQuery = { timeSeriesFilter = {
              filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.guardrail_blocks.name}\" resource.type=\"cloud_run_revision\""
            aggregation = { alignmentPeriod = "300s", perSeriesAligner = "ALIGN_SUM", crossSeriesReducer = "REDUCE_SUM" } } } }] }
          }
        },
        {
          xPos = 4, yPos = 4, width = 4, height = 4
          widget = {
            title = "Errors (turn + LLM)"
            xyChart = { dataSets = [{ plotType = "LINE", timeSeriesQuery = { timeSeriesFilter = {
              filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.turn_errors.name}\" resource.type=\"cloud_run_revision\""
            aggregation = { alignmentPeriod = "300s", perSeriesAligner = "ALIGN_SUM", crossSeriesReducer = "REDUCE_SUM" } } } }] }
          }
        },
        {
          xPos = 8, yPos = 4, width = 4, height = 4
          widget = {
            title = "Cloud Run requests by status class"
            xyChart = { dataSets = [{ plotType = "STACKED_BAR", timeSeriesQuery = { timeSeriesFilter = {
              filter = "metric.type=\"run.googleapis.com/request_count\" resource.type=\"cloud_run_revision\" resource.label.service_name=\"${var.service_name}\""
            aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_RATE", crossSeriesReducer = "REDUCE_SUM", groupByFields = ["metric.label.response_code_class"] } } } }] }
          }
        },
        {
          xPos = 0, yPos = 8, width = 6, height = 4
          widget = {
            title = "Container instances"
            xyChart = { dataSets = [{ plotType = "LINE", timeSeriesQuery = { timeSeriesFilter = {
              filter = "metric.type=\"run.googleapis.com/container/instance_count\" resource.type=\"cloud_run_revision\" resource.label.service_name=\"${var.service_name}\""
            aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_MAX", crossSeriesReducer = "REDUCE_SUM" } } } }] }
          }
        },
        {
          xPos = 6, yPos = 8, width = 6, height = 4
          widget = {
            title = "Uptime check (/health)"
            xyChart = { dataSets = [{ plotType = "LINE", timeSeriesQuery = { timeSeriesFilter = {
              filter = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" resource.type=\"uptime_url\""
            aggregation = { alignmentPeriod = "300s", perSeriesAligner = "ALIGN_FRACTION_TRUE", crossSeriesReducer = "REDUCE_MEAN" } } } }] }
          }
        }
      ]
    }
  })
}

output "dashboard_id" { value = google_monitoring_dashboard.main.id }
