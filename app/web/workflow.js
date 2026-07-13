/**
 * Pure workflow helpers for AI Fun Motion Gate 8.
 * Attaches to globalThis.AIFunWorkflow — no DOM, no network.
 */
(function (root) {
  "use strict";

  var ACTIVE_STATUSES = {
    PROMPT_GENERATING: true,
    BASE_IMAGE_GENERATING: true,
    WAITING_FOR_REFERENCE: true,
    CHARACTER_EDITING: true,
    SOURCE_VIDEO_GENERATING: true,
    CONTROL_VIDEO_GENERATING: true,
    FINAL_VIDEO_ASSEMBLING: true,
    ANALYZING_TRANSITION: true,
    MERGING: true,
  };

  var FAILED_STAGE_RETRY = {
    prompt_generation: {
      action: "generate-prompts",
      endpoint: "/generate-prompts",
      label: "Generate prompts",
      paid: true,
    },
    base_image_generation: {
      action: "generate-base-image",
      endpoint: "/generate-base-image",
      label: "Generate base image",
      paid: true,
    },
    character_editing: {
      action: "generate-character-edit",
      endpoint: "/generate-character-edit",
      label: "Replace character",
      paid: true,
    },
    source_video_generation: {
      action: "generate-source-video",
      endpoint: "/generate-source-video",
      label: "Generate source motion",
      paid: true,
    },
    control_video_generation: {
      action: "generate-controlled-video",
      endpoint: "/generate-controlled-video",
      label: "Transfer motion",
      paid: true,
    },
    final_video_assembly: {
      action: "assemble-final-video",
      endpoint: "/assemble-final-video",
      label: "Assemble final video",
      paid: false,
    },
  };

  var STATUS_VIEW = {
    DRAFT: {
      step: "prompt",
      action: "generate-prompts",
      label: "Ready",
      help: "Describe the subject, scene, and motion, then generate prompts.",
      preview: "empty",
      poll: false,
      paid: true,
    },
    PROMPT_GENERATING: {
      step: "prompt",
      action: null,
      label: "In progress",
      help: "Generating prompts…",
      preview: "empty",
      poll: true,
      paid: false,
    },
    PROMPT_READY: {
      step: "base-image",
      action: "generate-base-image",
      label: "Ready",
      help: "Prompts are ready. Generate the base image when you are ready.",
      preview: "prompts",
      poll: false,
      paid: true,
    },
    BASE_IMAGE_GENERATING: {
      step: "base-image",
      action: null,
      label: "In progress",
      help: "Generating base image…",
      preview: "empty",
      poll: true,
      paid: false,
    },
    BASE_IMAGE_READY: {
      step: "reference",
      action: "upload-reference",
      label: "Ready",
      help: "Upload a reference photo of the character.",
      preview: "base-image",
      poll: false,
      paid: false,
    },
    WAITING_FOR_REFERENCE: {
      step: "reference",
      action: null,
      label: "In progress",
      help: "Validating reference image…",
      preview: "base-image",
      poll: true,
      paid: false,
    },
    REFERENCE_READY: {
      step: "character-edit",
      action: "generate-character-edit",
      label: "Ready",
      help: "Reference accepted. Run character edit, or replace the reference first.",
      preview: "reference-compare",
      poll: false,
      paid: true,
      secondaryAction: "replace-reference",
    },
    CHARACTER_EDITING: {
      step: "character-edit",
      action: null,
      label: "In progress",
      help: "Editing character…",
      preview: "reference-compare",
      poll: true,
      paid: false,
    },
    CHARACTER_EDIT_READY: {
      step: "source-motion",
      action: "generate-source-video",
      label: "Ready",
      help: "Character edit ready. Generate source motion when you are ready.",
      preview: "edited-image",
      poll: false,
      paid: true,
    },
    SOURCE_VIDEO_GENERATING: {
      step: "source-motion",
      action: null,
      label: "In progress",
      help: "Generating source motion…",
      preview: "edited-image",
      poll: true,
      paid: false,
    },
    SOURCE_VIDEO_READY: {
      step: "motion-transfer",
      action: "generate-controlled-video",
      label: "Ready",
      help: "Source motion ready. Transfer motion onto the edited character.",
      preview: "source-video",
      poll: false,
      paid: true,
    },
    CONTROL_VIDEO_GENERATING: {
      step: "motion-transfer",
      action: null,
      label: "In progress",
      help: "Transferring motion…",
      preview: "source-video",
      poll: true,
      paid: false,
    },
    CONTROL_VIDEO_READY: {
      step: "final-video",
      action: "assemble-final-video",
      label: "Ready",
      help: "Controlled video ready. Assemble the final local video (no provider charge).",
      preview: "controlled-video",
      poll: false,
      paid: false,
    },
    FINAL_VIDEO_ASSEMBLING: {
      step: "final-video",
      action: null,
      label: "In progress",
      help: "Assembling final video locally…",
      preview: "controlled-video",
      poll: true,
      paid: false,
    },
    ANALYZING_TRANSITION: {
      step: "final-video",
      action: null,
      label: "In progress",
      help: "Processing…",
      preview: "empty",
      poll: true,
      paid: false,
    },
    MERGING: {
      step: "final-video",
      action: null,
      label: "In progress",
      help: "Processing…",
      preview: "empty",
      poll: true,
      paid: false,
    },
    COMPLETED: {
      step: "final-video",
      action: "download-final",
      label: "Completed",
      help: "Final video is ready to preview and download.",
      preview: "final-video",
      poll: false,
      paid: false,
    },
    FAILED: {
      step: null,
      action: "retry",
      label: "Failed",
      help: "This stage failed. Retry only if the backend marks the failed stage as eligible.",
      preview: "error",
      poll: false,
      paid: false,
    },
  };

  var STEPS = [
    { id: "prompt", title: "Prompt" },
    { id: "base-image", title: "Base image" },
    { id: "reference", title: "Reference" },
    { id: "character-edit", title: "Character edit" },
    { id: "source-motion", title: "Source motion" },
    { id: "motion-transfer", title: "Motion transfer" },
    { id: "final-video", title: "Final video" },
  ];

  var STEP_ORDER = STEPS.map(function (s) {
    return s.id;
  });

  function getStatusView(status) {
    return STATUS_VIEW[status] || {
      step: null,
      action: null,
      label: "Unknown",
      help: "Unknown project status.",
      preview: "empty",
      poll: false,
      paid: false,
    };
  }

  function isActiveStatus(status) {
    return !!ACTIVE_STATUSES[status];
  }

  function shouldPoll(status) {
    var view = getStatusView(status);
    return !!(view && view.poll);
  }

  function retryForFailedStage(failedStage) {
    if (!failedStage) return null;
    return FAILED_STAGE_RETRY[failedStage] || null;
  }

  function normalizeProgress(value) {
    var n = Number(value);
    if (!isFinite(n) || n < 0) return 0;
    if (n > 100) return 100;
    return Math.round(n);
  }

  function nextBackoffMs(attempt) {
    var a = Math.max(0, Number(attempt) || 0);
    var ms = 2000 * Math.pow(2, a);
    return Math.min(ms, 8000);
  }

  function jobPathFromLocation(pathname) {
    var match = String(pathname || "").match(/^\/jobs\/([^/]+)\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function artifactFileUrl(jobId, kind, updatedAt) {
    var base;
    switch (kind) {
      case "base-image":
        base = "/api/jobs/" + jobId + "/base-image/file";
        break;
      case "reference-image":
        base = "/api/jobs/" + jobId + "/reference-image/file";
        break;
      case "edited-image":
        base = "/api/jobs/" + jobId + "/edited-image/file";
        break;
      case "source-video":
        base = "/api/jobs/" + jobId + "/source-video/file";
        break;
      case "controlled-video":
        base = "/api/jobs/" + jobId + "/controlled-video/file";
        break;
      case "final-video":
        base = "/api/jobs/" + jobId + "/final-video/file";
        break;
      default:
        return null;
    }
    if (updatedAt) {
      return base + "?v=" + encodeURIComponent(String(updatedAt));
    }
    return base;
  }

  function formatApiError(status, detail) {
    var text =
      typeof detail === "string" && detail.trim() ? detail.trim() : null;
    if (status === 404) return text || "Project not found.";
    if (status === 409)
      return text || "The project changed. Refreshing its latest state.";
    if (status === 422)
      return text || "Some information was invalid. Check the form and try again.";
    if (status >= 500)
      return text || "The operation could not be completed safely.";
    return text || "Request failed.";
  }

  function stepState(stepId, jobStatus, failedStage) {
    var view = getStatusView(jobStatus);
    var current = view.step;
    var currentIdx = current ? STEP_ORDER.indexOf(current) : -1;
    var idx = STEP_ORDER.indexOf(stepId);
    if (jobStatus === "FAILED") {
      var retry = retryForFailedStage(failedStage);
      if (retry) {
        // Map retry action back to a step for chip styling.
        var failedView = null;
        for (var key in STATUS_VIEW) {
          if (
            STATUS_VIEW[key].action === retry.action ||
            (key.indexOf("GENERATING") >= 0 &&
              STATUS_VIEW[key].step &&
              false)
          ) {
            /* keep scanning */
          }
        }
        var stageToStep = {
          prompt_generation: "prompt",
          base_image_generation: "base-image",
          character_editing: "character-edit",
          source_video_generation: "source-motion",
          control_video_generation: "motion-transfer",
          final_video_assembly: "final-video",
          reference_upload: "reference",
        };
        var failStep = stageToStep[failedStage];
        if (failStep === stepId) return "Failed";
        var failIdx = failStep ? STEP_ORDER.indexOf(failStep) : -1;
        if (idx < failIdx) return "Completed";
        if (idx > failIdx) return "Waiting";
        return "Failed";
      }
      return idx === 0 ? "Failed" : "Waiting";
    }
    if (jobStatus === "COMPLETED") {
      return "Completed";
    }
    if (idx < 0) return "Waiting";
    if (isActiveStatus(jobStatus) && idx === currentIdx) return "In progress";
    if (idx < currentIdx) return "Completed";
    if (idx === currentIdx) {
      if (view.action) return "Ready";
      return view.label || "Ready";
    }
    return "Waiting";
  }

  root.AIFunWorkflow = {
    ACTIVE_STATUSES: ACTIVE_STATUSES,
    FAILED_STAGE_RETRY: FAILED_STAGE_RETRY,
    STATUS_VIEW: STATUS_VIEW,
    STEPS: STEPS,
    getStatusView: getStatusView,
    isActiveStatus: isActiveStatus,
    shouldPoll: shouldPoll,
    retryForFailedStage: retryForFailedStage,
    normalizeProgress: normalizeProgress,
    nextBackoffMs: nextBackoffMs,
    jobPathFromLocation: jobPathFromLocation,
    artifactFileUrl: artifactFileUrl,
    formatApiError: formatApiError,
    stepState: stepState,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
