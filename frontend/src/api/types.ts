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
export type RenderRecord = components["schemas"]["RenderRecord"];
export type Transcript = components["schemas"]["Transcript"];
export type PublishPack = components["schemas"]["PublishPack"];
export type TemplateSummary = components["schemas"]["TemplateSummary"];
export type TemplateKind = components["schemas"]["TemplateKind"];
export type TemplateStatus = components["schemas"]["TemplateStatus"];
export type Preset = components["schemas"]["Preset"];
export type UploadSession = components["schemas"]["UploadSession"];
export type DriveStatus = components["schemas"]["DriveStatus"];
export type Blocker = components["schemas"]["Blocker"];
export type LanguageCode = components["schemas"]["LanguageCode"];
export type ScriptPref = components["schemas"]["ScriptPref"];
export type AspectRatio = components["schemas"]["AspectRatio"];
export type PlatformTarget = components["schemas"]["PlatformTarget"];
export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];
export type ErrorCode = components["schemas"]["ErrorCode"];

export type ListJobsQuery = operations["listJobs"]["parameters"]["query"];
export type ListTemplatesQuery = operations["listTemplates"]["parameters"]["query"];
export type ListDriveFilesQuery = operations["listDriveFiles"]["parameters"]["query"];

export type TemplateListResponse =
  operations["listTemplates"]["responses"][200]["content"]["application/json"];
export type TemplateVersionsResponse =
  operations["listTemplateVersions"]["responses"][200]["content"]["application/json"];
export type PreviewTemplatePayload =
  NonNullable<operations["previewTemplate"]["requestBody"]>["content"]["application/json"];
export type PresetListResponse =
  operations["listPresets"]["responses"][200]["content"]["application/json"];
export type PatchClipPayload =
  operations["patchClip"]["requestBody"]["content"]["application/merge-patch+json"];
export type RetryJobPayload =
  NonNullable<operations["retryJob"]["requestBody"]>["content"]["application/json"];
export type InitUploadPayload =
  operations["initUpload"]["requestBody"]["content"]["application/json"];
export type DriveFilesResponse =
  operations["listDriveFiles"]["responses"][200]["content"]["application/json"];
export type DrivePickPayload =
  operations["pickDriveFile"]["requestBody"]["content"]["application/json"];
export type DrivePickResponse =
  operations["pickDriveFile"]["responses"][200]["content"]["application/json"];

export type AdminTemplatesResponse =
  operations["adminListTemplates"]["responses"][200]["content"]["application/json"];
export type AdminPresetsResponse =
  operations["adminListPresets"]["responses"][200]["content"]["application/json"];
export type AdminBlockersResponse =
  operations["adminListBlockers"]["responses"][200]["content"]["application/json"];
export type AdminAuditResponse =
  operations["adminAuditLog"]["responses"][200]["content"]["application/json"];
export type AdminSettingsResponse =
  operations["adminGetSettings"]["responses"][200]["content"]["application/json"];
export type AdminFontsResponse =
  operations["adminListFonts"]["responses"][200]["content"]["application/json"];
export type AdminFontRecord =
  operations["adminUploadFont"]["responses"][201]["content"]["application/json"];
export type AdminBrandKitsResponse =
  operations["adminListBrandKits"]["responses"][200]["content"]["application/json"];
export type AdminBrandKitPayload =
  operations["adminCreateBrandKit"]["requestBody"]["content"]["application/json"];
export type AdminBrandKitRecord =
  operations["adminCreateBrandKit"]["responses"][201]["content"]["application/json"];
export type AdminPromptsResponse =
  operations["adminListPrompts"]["responses"][200]["content"]["application/json"];
export type AdminPromptDetail =
  operations["adminGetPrompt"]["responses"][200]["content"]["application/json"];
export type AdminUpdatePromptPayload =
  operations["adminUpdatePrompt"]["requestBody"]["content"]["application/json"];
export type AdminActivatePromptPayload =
  operations["adminActivatePrompt"]["requestBody"]["content"]["application/json"];
export type AdminTestPromptResponse =
  operations["adminTestPrompt"]["responses"][200]["content"]["application/json"];
export type AdminScoringResponse =
  operations["adminGetScoring"]["responses"][200]["content"]["application/json"];
export type AdminUpdateScoringPayload =
  operations["adminUpdateScoring"]["requestBody"]["content"]["application/json"];
export type AdminDryRunScoringPayload =
  operations["adminDryRunScoring"]["requestBody"]["content"]["application/json"];
export type AdminDryRunScoringResponse =
  operations["adminDryRunScoring"]["responses"][200]["content"]["application/json"];
export type AdminUpdateSettingsPayload =
  operations["adminUpdateSettings"]["requestBody"]["content"]["application/json"];
export type DriveSyncPayload =
  NonNullable<operations["triggerDriveSync"]["requestBody"]>["content"]["application/json"];
export type DriveTreeResponse =
  operations["getDriveTree"]["responses"][200]["content"]["application/json"];
export type AdminResolveBlockerPayload =
  NonNullable<operations["adminResolveBlocker"]["requestBody"]>["content"]["application/json"];

export type ClipFeedbackPayload =
  operations["submitClipFeedback"]["requestBody"]["content"]["application/json"];
export type ReselectJobPayload =
  NonNullable<operations["reselectJobClips"]["requestBody"]>["content"]["application/json"];
export type RenderAllJobPayload =
  NonNullable<operations["renderAllJobClips"]["requestBody"]>["content"]["application/json"];
export type RenderClipPayload =
  NonNullable<operations["renderClip"]["requestBody"]>["content"]["application/json"];
export type ClipRendersResponse =
  operations["listClipRenders"]["responses"][200]["content"]["application/json"];

export type PatchTranscriptPayload =
  operations["patchJobTranscript"]["requestBody"]["content"]["application/json"];
export type UpdatePublishPackPayload =
  operations["updateClipPublishPack"]["requestBody"]["content"]["application/json"];
export type RegeneratePublishPackPayload =
  NonNullable<operations["regenerateClipPublishPack"]["requestBody"]>["content"]["application/json"];


