import type { components, operations, paths } from "./schema";

export type { components, operations, paths };

export type HealthResponse = components["schemas"]["HealthResponse"];
export type JobDetail = components["schemas"]["JobDetail"];
export type PaginatedJobs = components["schemas"]["PaginatedJobs"];
export type CreateJobRequest = components["schemas"]["CreateJobRequest"];
export type JobStatus = components["schemas"]["JobStatus"];
export type StageCheckpoint = components["schemas"]["StageCheckpoint"];
export type StageName = components["schemas"]["StageName"];
export type ClipSummary = components["schemas"]["ClipSummary"];
export type ClipDetail = components["schemas"]["ClipDetail"];
export type TemplateSummary = components["schemas"]["TemplateSummary"];
export type TemplateKind = components["schemas"]["TemplateKind"];
export type TemplateStatus = components["schemas"]["TemplateStatus"];
export type Preset = components["schemas"]["Preset"];
export type DriveStatus = components["schemas"]["DriveStatus"];
export type LanguageCode = components["schemas"]["LanguageCode"];
export type ScriptPref = components["schemas"]["ScriptPref"];
export type AspectRatio = components["schemas"]["AspectRatio"];
export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];
export type ErrorCode = components["schemas"]["ErrorCode"];

export type TemplateListResponse =
  operations["listTemplates"]["responses"][200]["content"]["application/json"];
export type PresetListResponse =
  operations["listPresets"]["responses"][200]["content"]["application/json"];
export type PatchClipPayload =
  operations["patchClip"]["requestBody"]["content"]["application/merge-patch+json"];
