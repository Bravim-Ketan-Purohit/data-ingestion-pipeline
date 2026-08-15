{{/*
Expand the name of the chart.
*/}}
{{- define "data-ingestion-pipeline.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "data-ingestion-pipeline.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "data-ingestion-pipeline.labels" -}}
helm.sh/chart: {{ include "data-ingestion-pipeline.name" . }}
{{ include "data-ingestion-pipeline.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "data-ingestion-pipeline.selectorLabels" -}}
app.kubernetes.io/name: {{ include "data-ingestion-pipeline.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
