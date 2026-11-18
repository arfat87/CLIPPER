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
    urlPlaceholder: "https://youtube.com/watch?v=... or sample://",
    uploadHint: "Chunked local upload (MP4, MOV, MKV)",
    driveHint: "Mirrored from CLIPPER/sources in Google Drive",
    languageLabel: "Language",
    scriptLabel: "Script",
    aspectLabel: "Aspect",
    presetLabel: "Preset",
    briefLabel: "Clip Brief (optional)",
    briefPlaceholder:
      "e.g. Focus on founder mistakes and unit economics hooks",
    rightsLabel: "I confirm I own or have rights to clip this content.",
    startButton: "Find Viral Clips",
    startingButton: "Starting Pipeline...",
    jobCreatedToast: "Clipping job created",
    jobCreatedToastDesc: "Pipeline queued and sample clips loaded.",
    clipUpdatedToast: "Clip template updated",
    clipsTitle: "Ranked Clips",
    previewTitle: "9:16 Live Caption Preview",
    scoreWhy: "Why this clip",
    scoreBreakdown: "Score breakdown",
    previewDisclaimer:
      "CSS approximation — server libass render is authoritative.",
    urlValidationError:
      "Paste a YouTube, Instagram, or Facebook URL to continue.",
    rightsValidationError:
      "Confirm content rights before starting a clipping job.",
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
