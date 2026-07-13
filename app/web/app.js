/**
 * AI Fun Motion Gate 8 UI — thin client over FastAPI.
 * No provider calls, no API keys, no innerHTML for server strings.
 */
(function () {
  "use strict";

  var W = globalThis.AIFunWorkflow;
  if (!W) {
    throw new Error("AIFunWorkflow missing");
  }

  var POLL_MS = 1750;
  var RECENT_PAGE = 10;
  var LAST_JOB_KEY = "last_job_id";

  var state = {
    job: null,
    pollTimer: null,
    pollAbort: null,
    backoffAttempt: 0,
    busy: false,
    previewObjectUrl: null,
    recentOffset: 0,
    recentTotal: 0,
    pendingDeleteId: null,
    confirmResolver: null,
    metaCache: {},
    pageHiddenSince: null,
  };

  var els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function setText(node, text) {
    if (node) node.textContent = text == null ? "" : String(text);
  }

  function clearChildren(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function el(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        var val = props[key];
        if (key === "className") node.className = val;
        else if (key === "text") node.textContent = val;
        else if (key === "htmlFor") node.htmlFor = val;
        else if (key.indexOf("on") === 0 && typeof val === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), val);
        } else if (val !== undefined && val !== null) {
          node.setAttribute(key, String(val));
        }
      });
    }
    (children || []).forEach(function (child) {
      if (child == null) return;
      if (typeof child === "string") node.appendChild(document.createTextNode(child));
      else node.appendChild(child);
    });
    return node;
  }

  function announce(message) {
    setText(els.live, message || "");
  }

  function rememberJobId(jobId) {
    try {
      if (jobId) localStorage.setItem(LAST_JOB_KEY, jobId);
      else localStorage.removeItem(LAST_JOB_KEY);
    } catch (_err) {
      /* localStorage may be unavailable */
    }
  }

  function readRememberedJobId() {
    try {
      return localStorage.getItem(LAST_JOB_KEY);
    } catch (_err) {
      return null;
    }
  }

  function setUrlJob(jobId, replace) {
    var path = jobId ? "/jobs/" + encodeURIComponent(jobId) : "/";
    if (replace) history.replaceState({ jobId: jobId }, "", path);
    else history.pushState({ jobId: jobId }, "", path);
  }

  async function api(path, options) {
    var opts = options || {};
    var headers = opts.headers ? Object.assign({}, opts.headers) : {};
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    var init = {
      method: opts.method || "GET",
      headers: headers,
      signal: opts.signal,
      body:
        opts.json !== undefined
          ? JSON.stringify(opts.json)
          : opts.body !== undefined
            ? opts.body
            : undefined,
    };
    var response;
    try {
      response = await fetch(path, init);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      var net = new Error("Connection lost. Retrying…");
      net.status = 0;
      net.network = true;
      throw net;
    }
    var data = null;
    var contentType = response.headers.get("content-type") || "";
    if (contentType.indexOf("application/json") >= 0) {
      try {
        data = await response.json();
      } catch (_err) {
        data = null;
      }
    } else {
      try {
        await response.text();
      } catch (_err) {
        /* ignore */
      }
    }
    if (!response.ok) {
      var detail =
        data && typeof data.detail === "string"
          ? data.detail
          : data && data.detail
            ? JSON.stringify(data.detail)
            : null;
      var error = new Error(W.formatApiError(response.status, detail));
      error.status = response.status;
      error.detail = detail;
      error.data = data;
      throw error;
    }
    return data;
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.pollAbort) {
      state.pollAbort.abort();
      state.pollAbort = null;
    }
  }

  function schedulePoll(delay) {
    stopPolling();
    if (!state.job) return;
    if (!W.shouldPoll(state.job.status)) return;
    state.pollTimer = setTimeout(runPoll, delay == null ? POLL_MS : delay);
  }

  async function runPoll() {
    if (!state.job) return;
    if (document.hidden && state.pageHiddenSince) {
      var hiddenFor = Date.now() - state.pageHiddenSince;
      if (hiddenFor > 30000) {
        schedulePoll(POLL_MS);
        return;
      }
    }
    if (state.pollAbort) state.pollAbort.abort();
    state.pollAbort = new AbortController();
    try {
      var job = await api("/api/jobs/" + encodeURIComponent(state.job.id), {
        signal: state.pollAbort.signal,
      });
      state.backoffAttempt = 0;
      state.job = job;
      renderAll();
      if (W.shouldPoll(job.status)) schedulePoll(POLL_MS);
    } catch (err) {
      if (err && err.name === "AbortError") return;
      if (err && err.network) {
        announce(err.message);
        var delay = W.nextBackoffMs(state.backoffAttempt);
        state.backoffAttempt += 1;
        schedulePoll(delay);
        return;
      }
      announce(err.message || "Could not refresh project.");
      schedulePoll(POLL_MS);
    }
  }

  function revokePreviewUrl() {
    if (state.previewObjectUrl) {
      URL.revokeObjectURL(state.previewObjectUrl);
      state.previewObjectUrl = null;
    }
  }

  function openConfirm(title, message) {
    return new Promise(function (resolve) {
      state.confirmResolver = resolve;
      setText(els.confirmTitle, title);
      setText(els.confirmMessage, message);
      els.confirmDialog.showModal();
      els.confirmOk.focus();
    });
  }

  function closeConfirm(result) {
    if (els.confirmDialog.open) els.confirmDialog.close();
    var resolver = state.confirmResolver;
    state.confirmResolver = null;
    if (resolver) resolver(!!result);
  }

  async function ensurePaidConfirm(paid) {
    if (!paid) return true;
    return openConfirm(
      "Paid generation",
      "This starts a paid AI generation request. It will not retry automatically."
    );
  }

  async function refreshJob(jobId) {
    var job = await api("/api/jobs/" + encodeURIComponent(jobId));
    state.job = job;
    state.metaCache = {};
    rememberJobId(job.id);
    renderAll();
    if (W.shouldPoll(job.status)) schedulePoll(POLL_MS);
    else stopPolling();
    return job;
  }

  async function createProject() {
    if (state.busy) return;
    state.busy = true;
    try {
      var job = await api("/api/jobs", { method: "POST", json: {} });
      state.job = job;
      state.metaCache = {};
      rememberJobId(job.id);
      setUrlJob(job.id, false);
      announce("Project created. Generate prompts when ready.");
      renderAll();
      stopPolling();
    } catch (err) {
      announce(err.message || "Could not create project.");
    } finally {
      state.busy = false;
    }
  }

  async function handleConflictAndRefresh(err) {
    if (err && err.status === 409 && state.job) {
      announce(err.message);
      try {
        await refreshJob(state.job.id);
      } catch (_e) {
        /* keep prior */
      }
      return true;
    }
    return false;
  }

  function renderStepper() {
    clearChildren(els.stepper);
    var job = state.job;
    var status = job ? job.status : null;
    var failedStage = job ? job.failed_stage : null;
    W.STEPS.forEach(function (step) {
      var label = job ? W.stepState(step.id, status, failedStage) : "Waiting";
      var li = el("li", {
        className: "step",
        "data-state": label,
      });
      li.appendChild(el("span", { className: "step-title", text: step.title }));
      li.appendChild(el("span", { className: "step-state", text: label }));
      els.stepper.appendChild(li);
    });
  }

  function mediaUrl(kind) {
    if (!state.job) return null;
    return W.artifactFileUrl(state.job.id, kind, state.job.updated_at);
  }

  function renderPreview() {
    var canvas = els.previewCanvas;
    clearChildren(canvas);
    revokePreviewUrl();
    var job = state.job;
    if (!job) {
      canvas.appendChild(els.emptyState);
      els.emptyState.classList.remove("hidden");
      clearChildren(els.previewMeta);
      return;
    }
    els.emptyState.classList.add("hidden");
    var view = W.getStatusView(job.status);
    var preview = view.preview;
    if (job.status === "FAILED") preview = "error";

    if (preview === "prompts") {
      canvas.appendChild(
        el("div", { className: "empty-state" }, [
          el("h3", { text: "Prompts ready" }),
          el("p", {
            text: "Open Advanced prompt details in the action panel if needed.",
          }),
        ])
      );
    } else if (preview === "base-image" || preview === "edited-image") {
      var kind = preview === "edited-image" ? "edited-image" : "base-image";
      canvas.appendChild(
        el("img", {
          src: mediaUrl(kind),
          alt: kind === "edited-image" ? "Edited character image" : "Base image",
        })
      );
    } else if (preview === "reference-compare") {
      var compare = el("div", { className: "preview-compare" });
      compare.appendChild(
        el("figure", null, [
          el("figcaption", { text: "Base" }),
          el("img", { src: mediaUrl("base-image"), alt: "Base image" }),
        ])
      );
      compare.appendChild(
        el("figure", null, [
          el("figcaption", { text: "Reference" }),
          el("img", {
            src: mediaUrl("reference-image"),
            alt: "Reference image",
          }),
        ])
      );
      canvas.appendChild(compare);
    } else if (
      preview === "source-video" ||
      preview === "controlled-video" ||
      preview === "final-video"
    ) {
      var vkind =
        preview === "final-video"
          ? "final-video"
          : preview === "controlled-video"
            ? "controlled-video"
            : "source-video";
      canvas.appendChild(
        el("video", {
          src: mediaUrl(vkind),
          controls: "controls",
          playsinline: "playsinline",
          preload: "metadata",
          "aria-label": vkind.replace("-", " "),
        })
      );
    } else if (preview === "error") {
      canvas.appendChild(
        el("div", { className: "empty-state" }, [
          el("h3", { text: "Stage failed" }),
          el("p", {
            text: job.error_message || "See the error panel for details.",
          }),
        ])
      );
    } else {
      canvas.appendChild(
        el("div", { className: "empty-state" }, [
          el("h3", { text: view.help || "Working…" }),
        ])
      );
    }

    clearChildren(els.previewMeta);
    els.previewMeta.appendChild(
      el("span", {
        className: "job-id",
        text: "Status: " + job.status,
        title: job.id,
      })
    );
    if (job.transition_time_seconds != null && job.status === "COMPLETED") {
      els.previewMeta.appendChild(
        el("span", {
          text:
            "Transition: " +
            Number(job.transition_time_seconds).toFixed(2) +
            "s",
        })
      );
    }
  }

  function renderErrorPanel() {
    var box = els.errorPanel;
    clearChildren(box);
    var job = state.job;
    if (!job || job.status !== "FAILED") {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    box.appendChild(
      el("p", { text: "Error code: " + (job.error_code || "unknown") })
    );
    box.appendChild(
      el("p", { text: "Failed stage: " + (job.failed_stage || "unknown") })
    );
    box.appendChild(
      el("p", { text: job.error_message || "The operation failed safely." })
    );
    var copyBtn = el("button", {
      type: "button",
      className: "btn",
      text: "Copy error code",
      onClick: function () {
        var code = job.error_code || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(code).then(
            function () {
              announce("Error code copied.");
            },
            function () {
              announce("Could not copy.");
            }
          );
        }
      },
    });
    box.appendChild(copyBtn);
  }

  function buildPromptForm() {
    var defaults = {
      subject_description: "one young Asian child looking directly at the camera",
      scene_description: "a simple ordinary indoor room with soft natural daylight",
      motion_description: "a quick playful hand flick that briefly crosses the face",
      duration_seconds: 5,
    };
    var form = el("form", { className: "form-stack", id: "prompt-form" });
    form.appendChild(
      el("label", null, [
        "Subject",
        el("textarea", {
          name: "subject_description",
          required: "required",
          maxlength: "500",
          text: defaults.subject_description,
        }),
      ])
    );
    form.appendChild(
      el("label", null, [
        "Scene",
        el("textarea", {
          name: "scene_description",
          required: "required",
          maxlength: "500",
          text: defaults.scene_description,
        }),
      ])
    );
    form.appendChild(
      el("label", null, [
        "Motion",
        el("textarea", {
          name: "motion_description",
          required: "required",
          maxlength: "500",
          text: defaults.motion_description,
        }),
      ])
    );
    form.appendChild(
      el("p", {
        className: "help",
        text: "Duration is fixed at 5 seconds by the backend.",
      })
    );
    var submit = el("button", {
      type: "submit",
      className: "btn btn-primary",
      text: "Generate prompts",
    });
    form.appendChild(submit);
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      onGeneratePrompts(form, submit);
    });
    return form;
  }

  function buildUploadZone(replace) {
    var wrap = el("div", { className: "form-stack" });
    var input = el("input", {
      type: "file",
      accept: "image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp",
      className: "sr-only",
      id: "reference-file",
    });
    var zone = el("div", {
      className: "dropzone",
      role: "button",
      tabindex: "0",
      "aria-label": replace ? "Replace reference image" : "Upload reference image",
    });
    var hint = el("span", {
      text: "PNG, JPEG, or WebP — drop or choose a file",
    });
    zone.appendChild(hint);
    var previewImg = null;

    function setLocalPreview(file) {
      revokePreviewUrl();
      if (previewImg) {
        zone.removeChild(previewImg);
        previewImg = null;
      }
      if (!file) return;
      state.previewObjectUrl = URL.createObjectURL(file);
      previewImg = el("img", {
        src: state.previewObjectUrl,
        alt: "Local reference preview",
      });
      zone.insertBefore(previewImg, hint);
    }

    function pick() {
      input.click();
    }
    zone.addEventListener("click", pick);
    zone.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        pick();
      }
    });
    zone.addEventListener("dragover", function (ev) {
      ev.preventDefault();
      zone.setAttribute("data-active", "true");
    });
    zone.addEventListener("dragleave", function () {
      zone.setAttribute("data-active", "false");
    });
    zone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      zone.setAttribute("data-active", "false");
      var file = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      if (file) {
        input.files = ev.dataTransfer.files;
        setLocalPreview(file);
      }
    });
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      setLocalPreview(file || null);
    });

    var uploadBtn = el("button", {
      type: "button",
      className: "btn btn-primary",
      text: replace ? "Replace reference image" : "Upload reference image",
      onClick: function () {
        var file = input.files && input.files[0];
        if (!file) {
          announce("Choose a reference image first.");
          return;
        }
        onUploadReference(file, uploadBtn, replace);
      },
    });
    if (replace) {
      wrap.appendChild(
        el("p", {
          className: "help",
          text: "Replacing the reference changes the next character-edit attempt but does not rerun editing automatically.",
        })
      );
    }
    wrap.appendChild(input);
    wrap.appendChild(zone);
    wrap.appendChild(uploadBtn);
    return wrap;
  }

  async function loadPromptSummary(container) {
    if (!state.job) return;
    try {
      var envelope = await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + "/prompts"
      );
      var req = envelope.request || {};
      container.appendChild(
        el("div", { className: "form-stack" }, [
          el("p", { text: "Subject: " + (req.subject_description || "") }),
          el("p", { text: "Scene: " + (req.scene_description || "") }),
          el("p", { text: "Motion: " + (req.motion_description || "") }),
        ])
      );
      var details = el("details", { className: "advanced" });
      details.appendChild(el("summary", { text: "Advanced prompt details" }));
      var pre = el("pre");
      setText(
        pre,
        JSON.stringify(envelope.prompts || {}, null, 2)
      );
      details.appendChild(pre);
      container.appendChild(details);
    } catch (_err) {
      container.appendChild(
        el("p", { className: "help", text: "Prompt summary unavailable." })
      );
    }
  }

  function primaryButton(label, onClick, opts) {
    opts = opts || {};
    return el("button", {
      type: "button",
      className: opts.danger ? "btn btn-danger" : "btn btn-primary",
      text: label,
      disabled: opts.disabled ? "disabled" : undefined,
      onClick: onClick,
    });
  }

  function renderActionPanel() {
    var body = els.actionBody;
    clearChildren(body);
    var job = state.job;
    if (!job) {
      setText(els.actionHelp, "Start a project to unlock the next step.");
      els.progressWrap.classList.add("hidden");
      setText(els.jobIdLine, "");
      els.jobIdLine.hidden = true;
      body.appendChild(
        primaryButton("Create new project", function () {
          createProject();
        })
      );
      return;
    }

    var view = W.getStatusView(job.status);
    setText(els.actionHelp, view.help || "");
    els.progressWrap.classList.toggle("hidden", !W.isActiveStatus(job.status));
    els.jobProgress.value = W.normalizeProgress(job.progress_percent);
    els.jobIdLine.hidden = false;
    setText(els.jobIdLine, "Project " + job.id);
    els.jobIdLine.title = job.id;

    if (job.status === "DRAFT") {
      body.appendChild(buildPromptForm());
      return;
    }
    if (job.status === "PROMPT_READY") {
      var promptBox = el("div");
      body.appendChild(promptBox);
      loadPromptSummary(promptBox);
      body.appendChild(
        primaryButton("Generate base image", function () {
          onPaidAction("generate-base-image", true, "Generate base image");
        })
      );
      return;
    }
    if (job.status === "BASE_IMAGE_READY") {
      body.appendChild(buildUploadZone(false));
      return;
    }
    if (job.status === "WAITING_FOR_REFERENCE") {
      body.appendChild(
        el("p", {
          className: "help",
          text: "Validating reference image… Character editing is disabled until validation finishes.",
        })
      );
      return;
    }
    if (job.status === "REFERENCE_READY") {
      body.appendChild(
        primaryButton("Replace character", function () {
          onPaidAction(
            "generate-character-edit",
            true,
            "Replace character"
          );
        })
      );
      body.appendChild(buildUploadZone(true));
      return;
    }
    if (job.status === "CHARACTER_EDIT_READY") {
      body.appendChild(
        primaryButton("Generate source motion", function () {
          onPaidAction(
            "generate-source-video",
            true,
            "Generate source motion"
          );
        })
      );
      return;
    }
    if (job.status === "SOURCE_VIDEO_READY") {
      body.appendChild(
        primaryButton("Transfer motion", function () {
          onPaidAction(
            "generate-controlled-video",
            true,
            "Transfer motion"
          );
        })
      );
      return;
    }
    if (job.status === "CONTROL_VIDEO_READY") {
      body.appendChild(
        el("p", {
          className: "help",
          text: "Local processing — no provider charge",
        })
      );
      body.appendChild(
        primaryButton("Assemble final video", function () {
          onPaidAction("assemble-final-video", false, "Assemble final video");
        })
      );
      return;
    }
    if (job.status === "COMPLETED") {
      body.appendChild(
        primaryButton("Download final video", function () {
          window.location.href =
            "/api/jobs/" +
            encodeURIComponent(job.id) +
            "/final-video/file";
        })
      );
      body.appendChild(
        el("button", {
          type: "button",
          className: "btn",
          text: "View source motion",
          onClick: function () {
            window.open(mediaUrl("source-video"), "_blank");
          },
        })
      );
      body.appendChild(
        el("button", {
          type: "button",
          className: "btn",
          text: "View controlled motion",
          onClick: function () {
            window.open(mediaUrl("controlled-video"), "_blank");
          },
        })
      );
      body.appendChild(
        el("button", {
          type: "button",
          className: "btn",
          text: "Start new project",
          onClick: function () {
            createProject();
          },
        })
      );
      loadFinalMeta(body);
      return;
    }
    if (job.status === "FAILED") {
      var retry = W.retryForFailedStage(job.failed_stage);
      if (retry && retry.action === "generate-prompts") {
        body.appendChild(buildPromptForm());
      } else if (retry) {
        body.appendChild(
          primaryButton(retry.label, function () {
            onPaidAction(retry.action, !!retry.paid, retry.label);
          })
        );
      } else if (job.failed_stage === "reference_upload") {
        body.appendChild(buildUploadZone(false));
      } else {
        body.appendChild(
          el("p", {
            className: "help",
            text: "This failure is not eligible for a stage retry from the UI.",
          })
        );
      }
      return;
    }
    if (W.isActiveStatus(job.status)) {
      body.appendChild(
        el("p", {
          className: "help",
          text: view.help || "Working…",
        })
      );
    }
  }

  async function loadFinalMeta(container) {
    if (!state.job) return;
    try {
      var meta = await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + "/final-video"
      );
      var transition = await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + "/transition"
      );
      container.appendChild(
        el("div", { className: "meta-row" }, [
          el("span", {
            text:
              meta.width +
              "×" +
              meta.height +
              " · " +
              Number(meta.duration_seconds).toFixed(2) +
              "s",
          }),
          el("span", {
            text:
              "Transition " +
              Number(transition.transition_seconds).toFixed(2) +
              "s (" +
              transition.method +
              ", conf " +
              Number(transition.confidence).toFixed(2) +
              ")",
          }),
          el("span", { text: "No audio in final output" }),
        ])
      );
    } catch (_err) {
      /* optional */
    }
  }

  async function onGeneratePrompts(form, submitBtn) {
    if (!state.job || state.busy) return;
    var fd = new FormData(form);
    var payload = {
      subject_description: String(fd.get("subject_description") || "").trim(),
      scene_description: String(fd.get("scene_description") || "").trim(),
      motion_description: String(fd.get("motion_description") || "").trim(),
      duration_seconds: 5,
    };
    if (
      !payload.subject_description ||
      !payload.scene_description ||
      !payload.motion_description
    ) {
      announce("Please fill in subject, scene, and motion.");
      return;
    }
    var ok = await ensurePaidConfirm(true);
    if (!ok) return;
    state.busy = true;
    submitBtn.disabled = true;
    try {
      await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + "/generate-prompts",
        { method: "POST", json: payload }
      );
      await refreshJob(state.job.id);
      announce("Generating prompts…");
    } catch (err) {
      if (await handleConflictAndRefresh(err)) return;
      announce(err.message || "Prompt generation failed.");
    } finally {
      state.busy = false;
      submitBtn.disabled = false;
    }
  }

  async function onPaidAction(action, paid, label) {
    if (!state.job || state.busy) return;
    var ok = await ensurePaidConfirm(paid);
    if (!ok) return;
    var pathMap = {
      "generate-base-image": "/generate-base-image",
      "generate-character-edit": "/generate-character-edit",
      "generate-source-video": "/generate-source-video",
      "generate-controlled-video": "/generate-controlled-video",
      "assemble-final-video": "/assemble-final-video",
    };
    var suffix = pathMap[action];
    if (!suffix) return;
    state.busy = true;
    try {
      await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + suffix,
        { method: "POST" }
      );
      await refreshJob(state.job.id);
      announce(label + " accepted.");
    } catch (err) {
      if (await handleConflictAndRefresh(err)) return;
      announce(err.message || "Request failed.");
    } finally {
      state.busy = false;
      renderActionPanel();
    }
  }

  async function onUploadReference(file, button, replace) {
    if (!state.job || state.busy) return;
    state.busy = true;
    button.disabled = true;
    try {
      var body = new FormData();
      body.append("file", file, file.name || "reference.png");
      await api(
        "/api/jobs/" + encodeURIComponent(state.job.id) + "/reference-image",
        { method: "POST", body: body }
      );
      revokePreviewUrl();
      await refreshJob(state.job.id);
      announce(replace ? "Reference replaced." : "Reference uploaded.");
    } catch (err) {
      if (await handleConflictAndRefresh(err)) return;
      announce(err.message || "Upload failed.");
    } finally {
      state.busy = false;
      button.disabled = false;
      renderActionPanel();
    }
  }

  function artifactReady(kind) {
    var job = state.job;
    if (!job) return false;
    if (kind === "base-image")
      return !!(
        job.base_image_url ||
        [
          "BASE_IMAGE_READY",
          "WAITING_FOR_REFERENCE",
          "REFERENCE_READY",
          "CHARACTER_EDITING",
          "CHARACTER_EDIT_READY",
          "SOURCE_VIDEO_GENERATING",
          "SOURCE_VIDEO_READY",
          "CONTROL_VIDEO_GENERATING",
          "CONTROL_VIDEO_READY",
          "FINAL_VIDEO_ASSEMBLING",
          "COMPLETED",
        ].indexOf(job.status) >= 0
      );
    if (kind === "reference-image")
      return !!(
        job.reference_image_path ||
        [
          "REFERENCE_READY",
          "CHARACTER_EDITING",
          "CHARACTER_EDIT_READY",
          "SOURCE_VIDEO_GENERATING",
          "SOURCE_VIDEO_READY",
          "CONTROL_VIDEO_GENERATING",
          "CONTROL_VIDEO_READY",
          "FINAL_VIDEO_ASSEMBLING",
          "COMPLETED",
        ].indexOf(job.status) >= 0
      );
    if (kind === "edited-image")
      return !!(
        job.edited_image_url ||
        [
          "CHARACTER_EDIT_READY",
          "SOURCE_VIDEO_GENERATING",
          "SOURCE_VIDEO_READY",
          "CONTROL_VIDEO_GENERATING",
          "CONTROL_VIDEO_READY",
          "FINAL_VIDEO_ASSEMBLING",
          "COMPLETED",
        ].indexOf(job.status) >= 0
      );
    if (kind === "source-video")
      return !!(
        job.source_video_url ||
        [
          "SOURCE_VIDEO_READY",
          "CONTROL_VIDEO_GENERATING",
          "CONTROL_VIDEO_READY",
          "FINAL_VIDEO_ASSEMBLING",
          "COMPLETED",
        ].indexOf(job.status) >= 0
      );
    if (kind === "controlled-video")
      return !!(
        job.controlled_video_url ||
        ["CONTROL_VIDEO_READY", "FINAL_VIDEO_ASSEMBLING", "COMPLETED"].indexOf(
          job.status
        ) >= 0
      );
    if (kind === "final-video")
      return !!(job.final_video_path || job.status === "COMPLETED");
    return false;
  }

  function renderArtifacts() {
    clearChildren(els.artifactGrid);
    if (!state.job) {
      els.artifactGrid.appendChild(
        el("p", { className: "help", text: "Artifacts appear as stages complete." })
      );
      return;
    }
    var items = [
      { kind: "base-image", title: "Base image", media: "img" },
      { kind: "reference-image", title: "Reference image", media: "img" },
      { kind: "edited-image", title: "Character edit", media: "img" },
      { kind: "source-video", title: "Source motion", media: "video" },
      { kind: "controlled-video", title: "Controlled motion", media: "video" },
      { kind: "final-video", title: "Final video", media: "video" },
    ];
    items.forEach(function (item) {
      var ready = artifactReady(item.kind);
      var card = el("article", { className: "card" });
      card.appendChild(el("h3", { text: item.title }));
      card.appendChild(
        el("p", {
          className: "help",
          text: ready ? "Ready" : "Not available",
        })
      );
      var thumb = el("div", { className: "thumb" });
      if (ready) {
        var url = mediaUrl(item.kind);
        if (item.media === "img") {
          thumb.appendChild(
            el("img", { src: url, alt: item.title, loading: "lazy" })
          );
        } else {
          thumb.appendChild(
            el("video", {
              src: url,
              preload: "metadata",
              muted: "muted",
              playsinline: "playsinline",
              "aria-label": item.title,
            })
          );
        }
      }
      card.appendChild(thumb);
      var actions = el("div", { className: "card-actions" });
      if (ready) {
        actions.appendChild(
          el("button", {
            type: "button",
            className: "btn",
            text: "Open",
            onClick: function () {
              window.open(mediaUrl(item.kind), "_blank");
            },
          })
        );
        actions.appendChild(
          el("a", {
            className: "btn",
            href: mediaUrl(item.kind),
            download: "",
            text: "Download",
          })
        );
      }
      card.appendChild(actions);
      els.artifactGrid.appendChild(card);
    });
  }

  function renderAll() {
    renderStepper();
    renderPreview();
    renderErrorPanel();
    renderActionPanel();
    renderArtifacts();
  }

  async function refreshHealth() {
    try {
      var health = await api("/health");
      var tone =
        health.status === "ok"
          ? "ok"
          : health.status === "degraded"
            ? "warn"
            : "error";
      els.healthChip.setAttribute("data-tone", tone);
      setText(els.healthLabel, "Backend " + health.status);
      var ws = (health.checks || []).find(function (c) {
        return c.name === "wavespeed";
      });
      if (ws) {
        var configured = ws.status === "ok";
        els.providerChip.setAttribute("data-tone", configured ? "ok" : "warn");
        setText(
          els.providerLabel,
          configured ? "Provider configured" : "Provider not configured"
        );
      }
    } catch (_err) {
      els.healthChip.setAttribute("data-tone", "error");
      setText(els.healthLabel, "Backend unreachable");
    }
  }

  async function loadRecent(reset) {
    if (reset) {
      state.recentOffset = 0;
      clearChildren(els.recentGrid);
    }
    try {
      var data = await api(
        "/api/jobs?limit=" +
          RECENT_PAGE +
          "&offset=" +
          state.recentOffset
      );
      state.recentTotal = data.total || 0;
      (data.items || []).forEach(function (job) {
        var card = el("article", { className: "card" });
        card.appendChild(
          el("h3", {
            className: "job-id",
            text: job.id,
            title: job.id,
          })
        );
        card.appendChild(
          el("p", {
            text:
              job.status +
              " · " +
              W.normalizeProgress(job.progress_percent) +
              "%",
          })
        );
        if (job.created_at) {
          card.appendChild(
            el("p", {
              className: "help",
              text: String(job.created_at).replace("T", " ").slice(0, 19),
            })
          );
        }
        var actions = el("div", { className: "card-actions" });
        actions.appendChild(
          el("button", {
            type: "button",
            className: "btn",
            text: "Open",
            onClick: function () {
              openJob(job.id);
            },
          })
        );
        actions.appendChild(
          el("button", {
            type: "button",
            className: "btn btn-danger",
            text: "Delete",
            onClick: function () {
              state.pendingDeleteId = job.id;
              els.deleteDialog.showModal();
              els.deleteOk.focus();
            },
          })
        );
        card.appendChild(actions);
        els.recentGrid.appendChild(card);
      });
      state.recentOffset += (data.items || []).length;
      els.btnLoadMore.hidden = state.recentOffset >= state.recentTotal;
    } catch (err) {
      announce(err.message || "Could not load recent projects.");
    }
  }

  async function openJob(jobId) {
    try {
      await refreshJob(jobId);
      setUrlJob(jobId, false);
      announce("Opened project.");
      els.recentSection.classList.add("hidden");
      els.btnRecent.setAttribute("aria-expanded", "false");
    } catch (err) {
      announce(err.message || "Project not found.");
    }
  }

  async function deletePending() {
    var id = state.pendingDeleteId;
    state.pendingDeleteId = null;
    if (!id) return;
    try {
      await api("/api/jobs/" + encodeURIComponent(id), { method: "DELETE" });
      announce("Project deleted.");
      if (state.job && state.job.id === id) {
        state.job = null;
        rememberJobId(null);
        setUrlJob(null, true);
        stopPolling();
        renderAll();
      }
      if (!els.recentSection.classList.contains("hidden")) {
        await loadRecent(true);
      }
    } catch (err) {
      announce(err.message || "Could not delete project.");
    }
  }

  function bindDialog(dialog, onCancel) {
    dialog.addEventListener("cancel", function (ev) {
      ev.preventDefault();
      onCancel();
    });
    dialog.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        onCancel();
      }
    });
  }

  function cacheEls() {
    els.live = $("live-region");
    els.stepper = $("stepper");
    els.previewCanvas = $("preview-canvas");
    els.emptyState = $("empty-state");
    els.previewMeta = $("preview-meta");
    els.actionHelp = $("action-help");
    els.actionBody = $("action-body");
    els.progressWrap = $("progress-wrap");
    els.jobProgress = $("job-progress");
    els.errorPanel = $("error-panel");
    els.jobIdLine = $("job-id-line");
    els.artifactGrid = $("artifact-grid");
    els.recentSection = $("recent-section");
    els.recentGrid = $("recent-grid");
    els.btnLoadMore = $("btn-load-more");
    els.btnNew = $("btn-new-project");
    els.btnRecent = $("btn-recent");
    els.btnCreateEmpty = $("btn-create-empty");
    els.healthChip = $("health-chip");
    els.healthLabel = $("health-label");
    els.providerChip = $("provider-chip");
    els.providerLabel = $("provider-label");
    els.confirmDialog = $("confirm-dialog");
    els.confirmTitle = $("confirm-title");
    els.confirmMessage = $("confirm-message");
    els.confirmOk = $("confirm-ok");
    els.confirmCancel = $("confirm-cancel");
    els.deleteDialog = $("delete-dialog");
    els.deleteOk = $("delete-ok");
    els.deleteCancel = $("delete-cancel");
  }

  async function boot() {
    cacheEls();
    renderStepper();
    renderAll();
    refreshHealth();

    els.btnNew.addEventListener("click", function () {
      createProject();
    });
    els.btnCreateEmpty.addEventListener("click", function () {
      createProject();
    });
    els.btnRecent.addEventListener("click", function () {
      var open = els.recentSection.classList.toggle("hidden") === false;
      els.btnRecent.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) loadRecent(true);
    });
    els.btnLoadMore.addEventListener("click", function () {
      loadRecent(false);
    });
    els.confirmOk.addEventListener("click", function () {
      closeConfirm(true);
    });
    els.confirmCancel.addEventListener("click", function () {
      closeConfirm(false);
    });
    bindDialog(els.confirmDialog, function () {
      closeConfirm(false);
    });
    els.deleteOk.addEventListener("click", function () {
      if (els.deleteDialog.open) els.deleteDialog.close();
      deletePending();
    });
    els.deleteCancel.addEventListener("click", function () {
      state.pendingDeleteId = null;
      if (els.deleteDialog.open) els.deleteDialog.close();
    });
    bindDialog(els.deleteDialog, function () {
      state.pendingDeleteId = null;
      if (els.deleteDialog.open) els.deleteDialog.close();
    });

    window.addEventListener("popstate", function () {
      var id = W.jobPathFromLocation(location.pathname);
      if (id) openJob(id);
      else {
        state.job = null;
        stopPolling();
        renderAll();
      }
    });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        state.pageHiddenSince = Date.now();
      } else {
        state.pageHiddenSince = null;
        if (state.job && W.shouldPoll(state.job.status)) schedulePoll(0);
      }
    });

    var fromPath = W.jobPathFromLocation(location.pathname);
    var remembered = readRememberedJobId();
    var initial = fromPath || remembered;
    if (initial) {
      try {
        await refreshJob(initial);
        if (!fromPath) setUrlJob(initial, true);
      } catch (_err) {
        rememberJobId(null);
        if (fromPath) announce("Project not found.");
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
