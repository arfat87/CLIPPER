export const en = {
  appName: "CLIPPER",
  tagline: "Local-first AI Clipping & Indic Caption Studio",
  nav: {
    home: "Home",
    library: "Library",
    jobs: "Jobs",
    templates: "Templates",
    settings: "Settings",
    admin: "Admin Panel",
    backToStudio: "Back to Studio",
    primaryNavLabel: "Primary navigation",
    mobileNavLabel: "Mobile navigation",
    adminNavLabel: "Admin sections",
  },
  header: {
    ffmpegReady: "FFmpeg + libass ready",
    ffmpegDegraded: "FFmpeg check degraded",
    driveConnected: "Drive connected",
    driveOffline: "Drive offline",
    mockStateLabel: "Preview state",
    adminBadge: "Owner Admin",
    openAdmin: "Open Admin",
    states: {
      normal: "Mock Data",
      loading: "Loading",
      empty: "Empty",
      error: "Error",
      offline: "Offline",
    },
  },
  home: {
    title: "Create Viral Clips",
    subtitle:
      "Paste a YouTube, Instagram, or Facebook link, upload a file, or pick from Drive.",
    sourceKinds: {
      url: "Link",
      upload: "Upload",
      drive: "Drive",
    },
    urlLabel: "Video URL",
    urlPlaceholder: "https://youtube.com/watch?v=... or sample://",
    platforms: {
      youtube: "YouTube",
      instagram: "Instagram",
      facebook: "Facebook",
      direct: "Direct",
    },
    urlInvalid: "Enter a valid URL starting with https://, http://, or sample://.",
    urlUnsupported:
      "Unsupported platform. Paste a YouTube, Instagram, Facebook, or direct media (.mp4/.mov/.mkv) link.",
    igLoginHint:
      "Instagram Reels may require owner cookies in Settings if the reel is private or age-restricted.",
    fbLoginHint:
      "Facebook videos must be public or authorized via owner cookies in Settings.",
    uploadHint: "Chunked local upload (MP4, MOV, MKV)",
    uploadPickFile: "Choose video file",
    uploadProgressLabel: "Uploading chunks...",
    uploadReadyLabel: "Upload ready",
    uploadFailedLabel: "Upload failed — choose a file and try again.",
    driveHint: "Mirrored from CLIPPER/sources in Google Drive",
    drivePickLabel: "Select a mirrored Drive video",
    driveLoading: "Loading synced Drive sources...",
    driveEmpty: "No mirrored source videos found in Drive.",
    drivePickedLabel: "Selected Drive source",
    languageLabel: "Language",
    scriptLabel: "Script",
    aspectLabel: "Aspect",
    presetLabel: "Preset",
    maxClipsLabel: "Max Clips",
    autoModeLabel: "Auto-render top clips",
    autoModeHint: "Off = pause after selection to review & trim clips first",
    briefLabel: "Clip Brief (optional)",
    briefPlaceholder:
      "e.g. Focus on founder mistakes and unit economics hooks",
    briefExamplesLabel: "Examples:",
    briefExamples: [
      "Founder mistakes & unit economics",
      "60s product demo highlights",
      "Contrarian career advice hooks",
      "High-energy Q&A punchlines",
    ],
    rightsLabel: "I confirm I own or have rights to clip this content.",
    startButton: "Find Viral Clips",
    startingButton: "Starting Pipeline...",
    jobCreatedToast: "Clipping job created",
    jobCreatedToastDesc: "Pipeline queued — live stage updates connected.",
    clipUpdatedToast: "Clip template updated",
    clipsTitle: "Ranked Clips",
    previewTitle: "9:16 Live Caption Preview",
    scoreWhy: "Why this clip",
    scoreBreakdown: "Score breakdown",
    previewDisclaimer:
      "CSS approximation — server libass render is authoritative.",
    urlValidationError:
      "Paste a YouTube, Instagram, or Facebook URL to continue.",
    uploadValidationError:
      "Choose and finish uploading a local video file to continue.",
    driveValidationError:
      "Pick a mirrored video from Google Drive to continue.",
    rightsValidationError:
      "Confirm content rights before starting a clipping job.",
  },
  timeline: {
    title: "Pipeline Progress",
    liveBadge: "LIVE SSE",
    reconnectingBadge: "Reconnecting (Last-Event-ID)...",
    etaPrefix: "ETA",
    stages: {
      fetch: "Fetch source",
      prepare: "Prepare & probe",
      transcribe: "Transcribe (ASR)",
      analyze: "Analyze highlights",
      select: "Select clips",
      render: "Render captions",
      package: "Package outputs",
    },
    warningsTitle: "Pipeline warnings",
    cancelCta: "Cancel Job",
    cancellingCta: "Cancelling...",
    cancelledToast: "Job cancellation requested",
    retryStageLabel: "Retry from stage",
    retryCta: "Retry",
    retryingCta: "Retrying...",
    retriedToast: "Job re-queued",
    downloadLogsCta: "Download Logs",
    downloadZipCta: "Download ZIP",
    deleteCta: "Delete",
    errorCardTitle: "Pipeline stopped",
    recoveryActionSettings: "Open Settings",
    recoveryActionRetry: "Retry Failed Stage",
    errorHints: {
      LOGIN_REQUIRED:
        "This source requires you to log in. Add cookies in Settings, then Retry.",
      PRIVATE_VIDEO:
        "This video appears private or unavailable. Verify access permissions or use a local upload.",
      GEO_BLOCKED:
        "This video is geo-restricted in your region. Try a mirrored Drive file or local upload.",
      UNSUPPORTED_URL:
        "This URL could not be extracted. Check the link format or upload the file directly.",
      ASR_FAILED:
        "Speech transcription failed on this audio track. Retry from Transcribe or check Settings.",
      LLM_UNAVAILABLE:
        "Cloud LLM provider is unreachable. Switch to local Ollama in Settings or Retry.",
      RENDER_FAILED:
        "FFmpeg/libass render failed. Check System Health and font coverage, then Retry from Render.",
      DEFAULT:
        "The pipeline hit an unexpected error. Review the message below and retry from the failed stage.",
    },
  },
  jobs: {
    title: "Pipeline Jobs",
    subtitle:
      "Stage-by-stage checkpoints across fetch, ASR, highlight analysis, and libass render.",
    emptyTitle: "No clipping jobs yet",
    emptyBody:
      "Start a job from the Home tab with a video URL, local upload, or Google Drive file.",
    emptyCta: "Create First Job",
    errorTitle: "Couldn't load pipeline jobs",
    filterAll: "All",
    filterCompleted: "Completed",
    filterRunning: "Active",
    filterFailed: "Failed",
    deleteJob: "Delete Job",
    deleteConfirmTitle: "Delete clipping job?",
    deleteConfirmBody:
      "This permanently removes the job, extracted clips, rendered MP4s, and publish packs from local disk.",
    deleteConfirmCta: "Delete Permanently",
    deletedToast: "Job deleted",
    inspectClips: "Inspect Clips",
    inspectTimeline: "Inspect Timeline",
  },
  library: {
    title: "Library & Google Drive",
    subtitle: "Synced Drive workspace routes and recent source videos.",
    driveCardTitle: "Google Drive Sync",
    routesLabel: "Mirrored assets by route",
    driveLastSync: "Last sync",
    driveRootLabel: "Root folder",
    recentJobsTitle: "Recent Sources",
    emptyTitle: "No sources in library yet",
    emptyBody:
      "Mirror files from Google Drive or start a job from a URL or local upload.",
    emptyCta: "Go to Home",
    errorTitle: "Google Drive sync error",
  },
  templates: {
    title: "Caption Templates & Presets",
    subtitle:
      "Multi-script templates supporting Latin, Devanagari (हिन्दी), and Bengali (বাংলা).",
    templatesHeading: "Templates",
    presetsHeading: "Preset Bundles",
    emptyTitle: "No caption templates loaded",
    emptyBody:
      "Add template folders under assets/templates/ or sync from Google Drive.",
    emptyCta: "Open Admin Templates",
    errorTitle: "Couldn't load templates",
    filterAll: "All",
    filterCaption: "Caption",
    filterLayout: "Layout",
    filterPublish: "Publish",
    usePreset: "Set Default",
    activePreset: "Default",
    presetUpdatedToast: "Default preset updated",
  },
  settings: {
    title: "Studio Settings",
    subtitle:
      "Local toolchain, model status, and default studio preferences.",
    toolchainHeading: "Local Toolchain Health",
    defaultsHeading: "Studio Defaults",
    adminLinkTitle: "Owner Admin Panel",
    adminLinkBody:
      "Manage templates, fonts, brand kits, prompts, scoring weights, Drive routing, blockers, and audit logs.",
    adminLinkCta: "Open Admin Panel",
    errorTitle: "Couldn't load settings health",
  },
  admin: {
    shellTitle: "CLIPPER Admin",
    shellSubtitle: "Owner control plane (/admin)",
    ownerOnlyBadge: "Owner-only",
    authNotice:
      "Admin writes are audited. Secret values are never returned by the API (set/unset status only).",
    emptyTitle: "No records found",
    emptyBody: "Adjust filters or switch back from Empty preview mode.",
    errorTitle: "Admin endpoint failed",
    templatesUi: {
      searchPlaceholder: "Search templates by name or ID...",
      kindFilterLabel: "Filter by kind",
      statusFilterLabel: "Filter by status",
      importButton: "Import Template",
      importModalTitle: "Import Template Package",
      importModalDesc:
        "Upload a .cliptpl.zip or template.json file, pick from Drive, or paste JSON directly to open as a validated draft.",
      importTabs: {
        file: "File (.zip / .json)",
        drive: "Google Drive",
        paste: "Paste JSON",
      },
      importPastePlaceholder: "Paste caption_template JSON here...",
      importSubmit: "Import as Draft",
      importing: "Importing...",
      importSuccessToast: "Template imported as draft",
      duplicateCta: "Duplicate",
      duplicatedToast: "Template duplicated as draft",
      exportCta: "Export JSON",
      exportedToast: "Template exported",
      archiveCta: "Archive",
      archiveConfirmTitle: "Archive template?",
      archiveConfirmBody:
        "Archived templates are hidden from studio pickers while preserving existing rendered clips.",
      archiveConfirmCta: "Archive Template",
      archivedToast: "Template archived",
      editCta: "Open Editor",
      backToList: "Back to Templates",
      formTab: "Form",
      jsonTab: "JSON",
      saveDraftCta: "Save Draft",
      savedDraftToast: "Draft changes saved locally",
      publishCta: "Publish Version",
      publishingCta: "Publishing...",
      publishedToast: "Template published (immutable version)",
      renderTestCta: "Render test frame",
      renderingTestCta: "Rendering libass frame...",
      renderTestTitle: "Server libass test frame",
      previewLimitationsTitle: "Preview limitations",
      previewLimitationsBody:
        "CSS preview approximates font scaling, outlines, and safe areas. HarfBuzz shaping, glow blur radius, and ASS kerning are only exact in server libass test frames.",
      safeAreaLabel: "Safe-area overlay",
      sampleLangLabel: "Sample language",
      ackWarningsLabel:
        "I acknowledge these warnings (license / contrast / safe area) and want to publish anyway.",
      validationErrorsTitle: "Validation errors (blocks publish)",
      validationWarningsTitle: "Warnings (acknowledge to publish)",
      validationOk: "Schema valid — ready to publish.",
    },
    presetsUi: {
      createTitle: "Create or Update Preset Bundle",
      nameLabel: "Preset Name",
      captionLabel: "Caption Template",
      layoutLabel: "Layout Template",
      publishLabel: "Publish Template",
      brandKitLabel: "Brand Kit (optional)",
      profileLabel: "Render Profile",
      saveCta: "Save Preset",
      setDefaultCta: "Set Default",
      savedToast: "Preset bundle saved",
      defaultToast: "Default preset updated",
    },
    fontsUi: {
      uploadTitle: "Register Font",
      fileLabel: "Font file (.ttf / .otf / .woff2)",
      licenseLabel: "License",
      uploadCta: "Upload & Check Coverage",
      uploadedToast: "Font registered",
      licenseOk: "License OK",
      licenseWarn: "Check License",
      defaultFallbackCta: "Default Fallback",
      fallbackToggledToast: "Default fallback updated",
    },
    brandKitsUi: {
      createTitle: "Create or Edit Brand Kit",
      nameLabel: "Brand Kit Name",
      primaryColorLabel: "Primary Color",
      accentColorLabel: "Accent Color",
      watermarkLabel: "Watermark Text",
      logoLabel: "Logo Asset",
      defaultPresetLabel: "Default Preset",
      saveCta: "Save Brand Kit",
      savedToast: "Brand kit saved",
    },
    sections: {
      templates: {
        label: "Templates",
        title: "Templates & Editor",
        description:
          "List by kind, filter by status (draft/published/archived/invalid), validate, publish, or archive.",
      },
      presets: {
        label: "Presets",
        title: "Preset Bundles",
        description:
          "Bundle one template per kind plus a render profile and choose the studio default.",
      },
      fonts: {
        label: "Fonts",
        title: "Fonts & Script Fallbacks",
        description:
          "Register fonts with license metadata and verify glyph coverage for Latin, Devanagari, and Bengali.",
      },
      brandkits: {
        label: "Brand kits",
        title: "Brand Kits",
        description:
          "Configure logos, watermarks, intro/outro bumpers, palettes, and default presets.",
      },
      prompts: {
        label: "Prompts",
        title: "Versioned Prompts",
        description:
          "Inspect and edit config/prompts/*.md, compare versions, rollback, and test JSON schema output.",
      },
      scoring: {
        label: "Scoring",
        title: "Highlight Scoring & Weights",
        description:
          "Tune fusion weights and thresholds per genre and dry-run against historical jobs.",
      },
      settings: {
        label: "Models & providers",
        title: "Models, Providers & Secrets",
        description:
          "Configure ASR engine/model, GPU device, LLM provider, and verify secret key presence.",
      },
      drive: {
        label: "Drive",
        title: "Google Drive Workspace",
        description:
          "Monitor sync status, root folder, asset counts by route, and force resync.",
      },
      blockers: {
        label: "Blockers inbox",
        title: "HITL Blockers Inbox",
        description:
          "Review open and resolved human-in-the-loop blockers with instructions and validation.",
      },
      jobs: {
        label: "Jobs",
        title: "Admin Jobs & Artifacts",
        description:
          "Browse all jobs, inspect stage checkpoints, retry failed stages, or clean up disk artifacts.",
      },
      health: {
        label: "System health",
        title: "System & Toolchain Health",
        description:
          "Inspect FFmpeg, libass, HarfBuzz, GPU VRAM, cached ASR/LLM models, yt-dlp, and disk space.",
      },
      audit: {
        label: "Audit log",
        title: "Admin Audit Log",
        description:
          "Chronological record of template publishes, prompt updates, and setting changes.",
      },
    },
  },
  states: {
    loadingTitle: "Loading CLIPPER workspace...",
    loadingBody: "Fetching jobs, templates, and local toolchain status.",
    loadingSkeletonLabel: "Loading content",
    errorTitle: "Couldn't load workspace data",
    errorBody:
      "Check your local API server or mock handler and try again.",
    retry: "Retry",
    offlineTitle: "You are offline",
    offlineBody:
      "Requests will resume automatically when your connection returns.",
    closeToast: "Dismiss notification",
    confirmCancel: "Cancel",
  },
} as const;
