(function () {
  "use strict";

  const csrfToken = document.getElementById("notification-csrf-token")?.value || "";
  const consoleRoot = document.querySelector(".notification-console");
  const previewFailed = consoleRoot?.dataset.previewFailed || "";
  const requestFailed = consoleRoot?.dataset.requestFailed || "";

  function itemFrom(button) {
    try {
      return JSON.parse(button.dataset.item || "{}");
    } catch (_) {
      return {};
    }
  }

  function openModal(id) {
    const element = document.getElementById(id);
    if (element && window.bootstrap) window.bootstrap.Modal.getOrCreateInstance(element).show();
  }

  function resetForm(form) {
    if (!form) return;
    form.reset();
    form.querySelectorAll('input[type="hidden"][name$="_id"]').forEach((input) => { input.value = "0"; });
  }

  function setChecked(form, name, values) {
    const selected = new Set((values || []).map(String));
    form.querySelectorAll(`[name="${name}"]`).forEach((input) => { input.checked = selected.has(String(input.value)); });
  }

  function setMultiple(select, values) {
    const selected = new Set((values || []).map(String));
    Array.from(select?.options || []).forEach((option) => { option.selected = selected.has(String(option.value)); });
  }

  const tabStorageKey = "easynetbak.notifications.activeTab";
  const notificationTabs = Array.from(document.querySelectorAll('#notificationTabs [data-bs-toggle="tab"]'));
  notificationTabs.forEach((tab) => {
    tab.addEventListener("shown.bs.tab", () => {
      try { window.sessionStorage.setItem(tabStorageKey, tab.dataset.bsTarget || "#channels-pane"); } catch (_) { /* Storage may be disabled. */ }
    });
  });
  try {
    const savedTarget = window.sessionStorage.getItem(tabStorageKey);
    const savedTab = notificationTabs.find((tab) => tab.dataset.bsTarget === savedTarget);
    if (savedTab && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(savedTab).show();
  } catch (_) { /* Keep the default tab when storage is unavailable. */ }

  const channelForm = document.getElementById("channelForm");
  const channelType = channelForm?.querySelector('[name="channel_type"]');
  const channelModal = document.getElementById("channelModal");
  let pendingChannelType = "";

  function updateChannelFields() {
    const isSmtp = channelType?.value === "smtp";
    const isRobot = ["wecom", "dingtalk", "feishu"].includes(channelType?.value || "");
    document.querySelectorAll("[data-channel-fields]").forEach((section) => {
      const visible = section.dataset.channelFields === (isSmtp ? "smtp" : "webhook");
      section.hidden = !visible;
      section.querySelectorAll("input,select,textarea").forEach((input) => { input.disabled = !visible; });
    });
    const testButton = document.getElementById("btn-test-channel");
    if (testButton) testButton.hidden = !(isSmtp || isRobot);
  }

  function setChannelType(value) {
    if (!channelType) return;
    const normalized = String(value || "").trim().toLowerCase();
    const options = Array.from(channelType.options);
    let matchingIndex = options.findIndex((option) => option.value === normalized);
    if (matchingIndex < 0) matchingIndex = options.findIndex((option) => option.value === "webhook");
    if (matchingIndex < 0) matchingIndex = 0;
    channelType.selectedIndex = -1;
    options.forEach((option, index) => { option.selected = index === matchingIndex; });
    channelType.selectedIndex = matchingIndex;
    updateChannelFields();
    window.NB?.refreshSelectDropdowns?.();
  }

  channelType?.addEventListener("change", () => {
    pendingChannelType = "";
    updateChannelFields();
  });
  channelModal?.addEventListener("shown.bs.modal", () => {
    if (!pendingChannelType) return;
    setChannelType(pendingChannelType);
    pendingChannelType = "";
  });
  document.querySelector("[data-create-channel]")?.addEventListener("click", () => {
    resetForm(channelForm);
    channelType.disabled = false;
    pendingChannelType = "smtp";
    setChannelType(pendingChannelType);
    channelForm.querySelector('[name="enabled"]').checked = true;
    channelForm.querySelector('[name="smtp_port"]').value = "25";
    channelForm.querySelector('[name="timeout"]').value = "10";
  });

  document.querySelectorAll("[data-edit-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = itemFrom(button);
      const config = item.config || {};
      resetForm(channelForm);
      channelForm.querySelector('[name="channel_id"]').value = item.id || 0;
      channelForm.querySelector('[name="name"]').value = item.name || "";
      pendingChannelType = String(item.channel_type || "webhook").trim().toLowerCase();
      setChannelType(pendingChannelType);
      channelForm.querySelector('[name="enabled"]').checked = !!item.enabled;
      channelForm.querySelector('[name="smtp_host"]').value = config.host || "";
      channelForm.querySelector('[name="smtp_port"]').value = config.port || "25";
      channelForm.querySelector('[name="smtp_user"]').value = config.user || "";
      channelForm.querySelector('[name="smtp_from"]').value = config.from || "";
      channelForm.querySelector('[name="smtp_to"]').value = config.to || "";
      channelForm.querySelector('[name="smtp_password"]').value = item.password_mask || "";
      channelForm.querySelector('[name="webhook_url"]').value = item.url_mask || "";
      channelForm.querySelector('[name="signing_secret"]').value = item.signing_secret_mask || "";
      channelForm.querySelector('[name="authorization"]').value = item.authorization_mask || "";
      channelForm.querySelector('[name="timeout"]').value = config.timeout || "10";
      channelForm.querySelector('[name="allow_private"]').checked = !!config.allow_private;
      openModal("channelModal");
    });
  });

  const policyForm = document.getElementById("policyForm");
  const policyTemplate = policyForm?.querySelector('[name="template_id"]');
  const policyGroups = policyForm?.querySelector('[name="group_ids"]');
  const includeDescendants = policyForm?.querySelector('[name="include_descendants"]');

  function selectedPolicyChannelTypes() {
    return Array.from(policyForm?.querySelectorAll('[name="channel_ids"]:checked') || [])
      .map((input) => input.dataset.channelType || "");
  }

  function templateSupportsChannels(option, channelTypes) {
    const type = option.dataset.channelType || "*";
    const compatible = channelTypes.every((channelType) => (
      type === "*" || type === channelType || (type === "robot" && ["wecom", "dingtalk", "feishu"].includes(channelType))
    ));
    const channelFamilies = new Set(channelTypes.map((channelType) => (
      ["wecom", "dingtalk", "feishu"].includes(channelType) ? "robot" : channelType
    )));
    return compatible || (option.dataset.builtinFamily === "1" && channelFamilies.size > 1);
  }

  function updatePolicyTemplates() {
    if (!policyTemplate) return;
    const channelTypes = selectedPolicyChannelTypes();
    const currentValue = policyTemplate.value;
    let compatibleCount = 0;
    Array.from(policyTemplate.options).forEach((option) => {
      if (!option.value) return;
      const compatible = channelTypes.length > 0 && templateSupportsChannels(option, channelTypes);
      option.hidden = !compatible;
      option.disabled = !compatible;
      if (compatible) compatibleCount += 1;
    });
    if (!Array.from(policyTemplate.selectedOptions).every((option) => !option.value || !option.disabled)) {
      policyTemplate.value = "";
    } else if (currentValue) {
      policyTemplate.value = currentValue;
    }
    const hint = document.getElementById("policyTemplateHint");
    const messages = document.getElementById("policyTemplateMessages")?.dataset || {};
    if (hint) {
      hint.textContent = channelTypes.length === 0
        ? messages.selectChannel || ""
        : (compatibleCount === 0 ? messages.noCompatible || "" : messages.filtered || "");
    }
  }

  function updateGroupScope() {
    if (!policyGroups) return;
    const allOption = Array.from(policyGroups.options).find((option) => option.value === "0");
    if (allOption?.selected && policyGroups.selectedOptions.length > 1) {
      allOption.selected = false;
    }
    if (includeDescendants) includeDescendants.disabled = !!allOption?.selected;
  }

  policyForm?.querySelectorAll('[name="channel_ids"]').forEach((input) => input.addEventListener("change", updatePolicyTemplates));
  policyGroups?.addEventListener("change", updateGroupScope);
  document.querySelector("[data-create-policy]")?.addEventListener("click", () => {
    resetForm(policyForm);
    policyForm.querySelector('[name="priority"]').value = "100";
    policyForm.querySelector('[name="enabled"]').checked = true;
    policyForm.querySelector('[name="include_descendants"]').checked = true;
    setMultiple(policyGroups, [0]);
    updateGroupScope();
    updatePolicyTemplates();
  });

  document.querySelectorAll("[data-edit-policy]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = itemFrom(button);
      resetForm(policyForm);
      policyForm.querySelector('[name="policy_id"]').value = item.id || 0;
      policyForm.querySelector('[name="name"]').value = item.name || "";
      policyForm.querySelector('[name="priority"]').value = item.priority ?? 100;
      policyForm.querySelector('[name="enabled"]').checked = !!item.enabled;
      policyForm.querySelector('[name="include_descendants"]').checked = !!item.include_descendants;
      setMultiple(policyForm.querySelector('[name="platforms"]'), item.platforms);
      setMultiple(policyForm.querySelector('[name="failure_types"]'), item.failure_types);
      policyForm.querySelector('[name="stop_processing"]').checked = !!item.stop_processing;
      setChecked(policyForm, "event_types", item.event_types);
      setChecked(policyForm, "channel_ids", item.channel_ids);
      setMultiple(policyGroups, item.group_ids?.length ? item.group_ids : [0]);
      updateGroupScope();
      updatePolicyTemplates();
      policyTemplate.value = item.template_id ? String(item.template_id) : "";
      openModal("policyModal");
    });
  });

  const templateForm = document.getElementById("templateForm");
  const templateSubject = templateForm?.querySelector('[name="subject_template"]');
  const templateBody = templateForm?.querySelector('[name="body_template"]');
  const templateEvent = templateForm?.querySelector('[name="event_type"]');
  const templateContentType = templateForm?.querySelector('[name="content_type"]');
  const templateInsertTarget = document.getElementById("templateInsertTarget");
  const templatePreview = document.getElementById("templatePreview");
  const templatePreviewModes = document.getElementById("templatePreviewModes");
  const webhookPreviewLabels = document.getElementById("webhookPreviewLabels")?.dataset || {};
  let activeTemplateField = templateBody;
  let lastTemplatePreview = null;
  let sampleContexts = {};
  const builtinTemplateFormats = {
    builtin_backup_detailed: { channelType: "smtp", contentType: "html" },
    builtin_backup_robot: { channelType: "robot", contentType: "markdown" },
    builtin_backup_feishu: { channelType: "feishu", contentType: "json" },
    builtin_backup_webhook_json: { channelType: "webhook", contentType: "json" },
  };

  try {
    sampleContexts = JSON.parse(document.getElementById("templateSampleContexts")?.textContent || "{}");
  } catch (_) {
    sampleContexts = {};
  }

  function resetTemplatePreview() {
    lastTemplatePreview = null;
    if (templatePreview) templatePreview.replaceChildren();
    if (templatePreviewModes) templatePreviewModes.hidden = true;
  }

  function updateBuiltinFormatLock(item = {}) {
    const format = builtinTemplateFormats[item.builtin_key] || null;
    const channelField = templateForm?.querySelector('[name="channel_type"]');
    const contentField = templateForm?.querySelector('[name="content_type"]');
    const hint = document.getElementById("builtinTemplateFormatHint");
    [[channelField, format?.channelType], [contentField, format?.contentType]].forEach(([field, value]) => {
      if (!field) return;
      field.dataset.lockedValue = value || "";
      field.classList.toggle("is-format-locked", !!value);
      field.setAttribute("aria-disabled", String(!!value));
      if (value) field.value = value;
    });
    if (hint) hint.hidden = !format;
  }

  function deriveTemplateChannel() {
    const channelField = templateForm?.querySelector('[name="channel_type"]');
    if (!channelField || channelField.dataset.lockedValue) return;
    channelField.value = ({ html: "smtp", markdown: "robot", json: "webhook", text: "*" })[templateContentType?.value] || "*";
  }

  [templateForm?.querySelector('[name="channel_type"]'), templateContentType].forEach((field) => {
    field?.addEventListener("change", () => {
      if (field.dataset.lockedValue) field.value = field.dataset.lockedValue;
      deriveTemplateChannel();
    });
  });

  function updateInsertTarget(field) {
    activeTemplateField = field || templateBody;
    if (!templateInsertTarget) return;
    templateInsertTarget.textContent = activeTemplateField === templateSubject
      ? templateInsertTarget.dataset.subjectLabel
      : templateInsertTarget.dataset.bodyLabel;
  }

  [templateSubject, templateBody].forEach((field) => {
    field?.addEventListener("focus", () => updateInsertTarget(field));
  });

  document.querySelectorAll("[data-template-insert]").forEach((button) => {
    button.addEventListener("click", () => {
      const field = activeTemplateField || templateBody;
      if (!field) return;
      const snippet = button.dataset.templateInsert || "";
      const start = Number.isInteger(field.selectionStart) ? field.selectionStart : field.value.length;
      const end = Number.isInteger(field.selectionEnd) ? field.selectionEnd : start;
      field.setRangeText(snippet, start, end, "end");
      field.focus();
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });

  document.querySelectorAll("[data-template-reference-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.templateReferenceTab;
      document.querySelectorAll("[data-template-reference-tab]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", String(active));
      });
      document.querySelectorAll("[data-template-reference-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.templateReferencePanel !== tab;
      });
    });
  });

  function filterTemplateVariables() {
    const query = (document.getElementById("templateVariableSearch")?.value || "").trim().toLocaleLowerCase();
    const eventType = templateEvent?.value || "*";
    let visibleVariables = 0;
    document.querySelectorAll("[data-variable-group]").forEach((group) => {
      const events = (group.dataset.events || "*").split(",");
      const eventMatches = eventType === "*" || events.includes("*") || events.includes(eventType);
      let groupMatches = 0;
      group.querySelectorAll("[data-variable-search]").forEach((variable) => {
        const matches = eventMatches && (!query || variable.dataset.variableSearch.includes(query));
        variable.hidden = !matches;
        if (matches) groupMatches += 1;
      });
      group.hidden = groupMatches === 0;
      if (query && groupMatches > 0) group.open = true;
      visibleVariables += groupMatches;
    });
    const empty = document.getElementById("templateVariableEmpty");
    if (empty) empty.hidden = visibleVariables !== 0;
  }

  function updateTemplateSample() {
    const eventType = templateEvent?.value || "*";
    const context = sampleContexts[eventType] || sampleContexts["*"] || {};
    const sampleEvent = document.getElementById("templateSampleEvent");
    const sampleData = document.getElementById("templateSampleData");
    const scope = document.getElementById("templateEventScope");
    if (sampleEvent) sampleEvent.textContent = eventType;
    if (sampleData) sampleData.textContent = JSON.stringify(context, null, 2);
    if (scope) scope.textContent = templateEvent?.selectedOptions?.[0]?.textContent || eventType;
    filterTemplateVariables();
  }

  function collapseTemplateVariables() {
    document.querySelectorAll("[data-variable-group]").forEach((group) => { group.open = false; });
  }

  document.getElementById("templateVariableSearch")?.addEventListener("input", filterTemplateVariables);
  templateEvent?.addEventListener("change", updateTemplateSample);

  function markdownText(value) {
    return String(value || "").replace(/\\([\\`*_{}\[\]()#+\-.!])/g, "$1");
  }

  function appendMarkdownInline(container, value) {
    const source = String(value || "");
    const tokenPattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
    let cursor = 0;
    for (const match of source.matchAll(tokenPattern)) {
      container.append(document.createTextNode(markdownText(source.slice(cursor, match.index))));
      const token = match[0];
      const element = document.createElement(token.startsWith("`") ? "code" : "strong");
      element.textContent = markdownText(token.startsWith("`") ? token.slice(1, -1) : token.slice(2, -2));
      container.append(element);
      cursor = match.index + token.length;
    }
    container.append(document.createTextNode(markdownText(source.slice(cursor))));
  }

  function renderMarkdownPreview(source) {
    const rendered = document.createElement("div");
    rendered.className = "template-preview-markdown";
    const lines = String(source || "").split(/\r?\n/);

    function splitTableRow(line) {
      const cells = [];
      let value = "";
      for (let index = 0; index < line.length; index += 1) {
        const character = line[index];
        if (character === "|" && line[index - 1] !== "\\") {
          cells.push(value.trim());
          value = "";
        } else {
          value += character;
        }
      }
      cells.push(value.trim());
      if (!cells[0]) cells.shift();
      if (!cells[cells.length - 1]) cells.pop();
      return cells;
    }

    function isTableDivider(line) {
      const cells = splitTableRow(line);
      return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
    }

    for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
      const line = lines[lineIndex];
      if (line.includes("|") && isTableDivider(lines[lineIndex + 1] || "")) {
        const tableWrap = document.createElement("div");
        tableWrap.className = "markdown-table-wrap";
        const table = document.createElement("table");
        table.className = "markdown-table";
        const head = document.createElement("thead");
        const headRow = document.createElement("tr");
        splitTableRow(line).forEach((cell) => {
          const th = document.createElement("th");
          appendMarkdownInline(th, cell);
          headRow.append(th);
        });
        head.append(headRow);
        table.append(head);
        const body = document.createElement("tbody");
        lineIndex += 2;
        while (lineIndex < lines.length && lines[lineIndex].includes("|") && lines[lineIndex].trim()) {
          const row = document.createElement("tr");
          splitTableRow(lines[lineIndex]).forEach((cell) => {
            const td = document.createElement("td");
            appendMarkdownInline(td, cell);
            row.append(td);
          });
          body.append(row);
          lineIndex += 1;
        }
        table.append(body);
        tableWrap.append(table);
        rendered.append(tableWrap);
        lineIndex -= 1;
        continue;
      }
      if (!line.trim()) {
        const spacer = document.createElement("div");
        spacer.className = "markdown-spacer";
        rendered.append(spacer);
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const listItem = line.match(/^(\s*)-\s+(.+)$/);
      let element;
      let content;
      if (heading) {
        element = document.createElement(`h${Math.min(6, heading[1].length + 2)}`);
        content = heading[2];
      } else if (listItem) {
        element = document.createElement("div");
        element.className = "markdown-list-item";
        element.style.setProperty("--markdown-depth", String(Math.floor(listItem[1].length / 2)));
        const bullet = document.createElement("span");
        bullet.className = "markdown-bullet";
        bullet.textContent = "•";
        element.append(bullet);
        content = listItem[2];
      } else {
        element = document.createElement("p");
        content = line;
      }
      const inline = document.createElement("span");
      appendMarkdownInline(inline, content);
      element.append(inline);
      rendered.append(element);
    }
    return rendered;
  }

  function renderFeishuCardPreview(source) {
    let cardData;
    try {
      cardData = JSON.parse(String(source || ""));
    } catch (_) {
      return null;
    }
    if (cardData?.schema !== "2.0" || !cardData?.header || !Array.isArray(cardData?.body?.elements)) return null;

    const stage = document.createElement("div");
    stage.className = "feishu-preview-stage";
    const card = document.createElement("article");
    card.className = "feishu-preview-card";

    const header = document.createElement("header");
    const headerTemplate = String(cardData.header.template || "blue").toLowerCase();
    header.className = `feishu-preview-header is-${/^(red|orange|green|blue|turquoise|purple|grey)$/.test(headerTemplate) ? headerTemplate : "blue"}`;
    const title = document.createElement("h4");
    title.textContent = cardData.header.title?.content || cardData.config?.summary?.content || "";
    header.append(title);
    card.append(header);

    const body = document.createElement("div");
    body.className = "feishu-preview-body";
    cardData.body.elements.forEach((element) => {
      if (element?.tag === "markdown") {
        const markdown = renderMarkdownPreview(element.content || "");
        markdown.classList.add("feishu-preview-markdown");
        body.append(markdown);
        return;
      }
      if (element?.tag !== "table" || !Array.isArray(element.columns) || !Array.isArray(element.rows)) return;
      const tableWrap = document.createElement("div");
      tableWrap.className = "feishu-preview-table-wrap";
      const table = document.createElement("table");
      table.className = "feishu-preview-table";
      const headRow = document.createElement("tr");
      element.columns.forEach((column) => {
        const cell = document.createElement("th");
        cell.textContent = column.display_name || column.name || "";
        headRow.append(cell);
      });
      const head = document.createElement("thead");
      head.append(headRow);
      table.append(head);
      const tableBody = document.createElement("tbody");
      element.rows.forEach((rowData) => {
        const row = document.createElement("tr");
        element.columns.forEach((column) => {
          const cell = document.createElement("td");
          cell.textContent = rowData?.[column.name] ?? "";
          row.append(cell);
        });
        tableBody.append(row);
      });
      table.append(tableBody);
      tableWrap.append(table);
      body.append(tableWrap);
    });
    card.append(body);
    stage.append(card);
    return stage;
  }

  function renderWebhookPayloadPreview(source) {
    let payload;
    try {
      payload = JSON.parse(String(source || ""));
    } catch (_) {
      return null;
    }
    if (!payload || Array.isArray(payload) || typeof payload !== "object"
      || !payload.summary || typeof payload.summary !== "object" || !Array.isArray(payload.items)) return null;

    const stage = document.createElement("div");
    stage.className = "webhook-preview-stage";
    const card = document.createElement("article");
    card.className = "webhook-preview-card";

    const header = document.createElement("header");
    header.className = "webhook-preview-header";
    const heading = document.createElement("div");
    heading.className = "webhook-preview-heading";
    const icon = document.createElement("i");
    icon.className = "bi bi-braces";
    const title = document.createElement("h4");
    title.textContent = String(payload.title || "Webhook");
    heading.append(icon, title);
    const meta = document.createElement("div");
    meta.className = "webhook-preview-meta";
    const event = document.createElement("span");
    event.textContent = `${webhookPreviewLabels.event || "Event"}: ${payload.event || "-"}`;
    const taskTime = document.createElement("span");
    taskTime.textContent = `${webhookPreviewLabels.taskTime || "Task time"}: ${payload.task_time || "-"}`;
    meta.append(event, taskTime);
    header.append(heading, meta);
    card.append(header);

    const summary = document.createElement("div");
    summary.className = "webhook-preview-summary";
    [
      ["total", webhookPreviewLabels.total, payload.summary.total],
      ["succeeded", webhookPreviewLabels.succeeded, payload.summary.succeeded],
      ["failed", webhookPreviewLabels.failed, payload.summary.failed],
      ["cancelled", webhookPreviewLabels.cancelled, payload.summary.cancelled],
      ["changed", webhookPreviewLabels.changed, payload.summary.changed],
    ].forEach(([tone, label, value]) => {
      const stat = document.createElement("div");
      stat.className = `webhook-preview-stat is-${tone}`;
      const strong = document.createElement("strong");
      strong.textContent = String(value ?? 0);
      const caption = document.createElement("span");
      caption.textContent = label || tone;
      stat.append(strong, caption);
      summary.append(stat);
    });
    card.append(summary);

    const detail = document.createElement("section");
    detail.className = "webhook-preview-detail";
    const detailTitle = document.createElement("h5");
    detailTitle.textContent = `${webhookPreviewLabels.items || "Items"} (${payload.items.length})`;
    detail.append(detailTitle);
    if (payload.items.length) {
      const columns = [
        ["device_name", webhookPreviewLabels.deviceName],
        ["device_host", webhookPreviewLabels.deviceHost],
        ["platform", webhookPreviewLabels.platform],
        ["status", webhookPreviewLabels.status],
        ["duration", webhookPreviewLabels.duration],
        ["failure_type", webhookPreviewLabels.failureType],
        ["error_message", webhookPreviewLabels.error],
      ];
      const tableWrap = document.createElement("div");
      tableWrap.className = "webhook-preview-table-wrap";
      const table = document.createElement("table");
      table.className = "webhook-preview-table";
      const headRow = document.createElement("tr");
      columns.forEach(([, label]) => {
        const cell = document.createElement("th");
        cell.textContent = label || "-";
        headRow.append(cell);
      });
      const head = document.createElement("thead");
      head.append(headRow);
      table.append(head);
      const body = document.createElement("tbody");
      payload.items.forEach((item) => {
        const record = item && typeof item === "object" ? item : {};
        const row = document.createElement("tr");
        columns.forEach(([key]) => {
          const cell = document.createElement("td");
          if (key === "status") {
            const status = record.cancelled ? "cancelled" : !record.success ? "failed" : record.changed ? "changed" : "succeeded";
            const badge = document.createElement("span");
            badge.className = `webhook-preview-status is-${status}`;
            badge.textContent = webhookPreviewLabels[status] || status;
            cell.append(badge);
          } else {
            cell.textContent = String(record[key] ?? "-");
          }
          row.append(cell);
        });
        body.append(row);
      });
      table.append(body);
      tableWrap.append(table);
      detail.append(tableWrap);
    }
    card.append(detail);
    stage.append(card);
    return stage;
  }

  function renderTemplatePreview(mode = "visual") {
    if (!templatePreview || !lastTemplatePreview) return;
    templatePreview.replaceChildren();
    const subject = document.createElement("div");
    subject.className = "template-preview-subject";
    subject.textContent = lastTemplatePreview.subject || "";
    templatePreview.append(subject);

    if (mode === "visual" && lastTemplatePreview.contentType === "html") {
      const frame = document.createElement("iframe");
      frame.className = "template-preview-frame";
      frame.setAttribute("sandbox", "");
      frame.setAttribute("title", lastTemplatePreview.subject || "Template preview");
      const policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">';
      frame.srcdoc = `${policy}${lastTemplatePreview.body || ""}`;
      templatePreview.append(frame);
      return;
    }

    if (mode === "visual" && lastTemplatePreview.contentType === "markdown") {
      templatePreview.append(renderMarkdownPreview(lastTemplatePreview.body));
      return;
    }

    if (mode === "visual" && lastTemplatePreview.contentType === "json") {
      const feishuCard = renderFeishuCardPreview(lastTemplatePreview.body);
      if (feishuCard) {
        templatePreview.append(feishuCard);
        return;
      }
      const webhookPayload = renderWebhookPayloadPreview(lastTemplatePreview.body);
      if (webhookPayload) {
        templatePreview.append(webhookPayload);
        return;
      }
    }

    const body = document.createElement("pre");
    body.className = "template-preview-source";
    body.textContent = lastTemplatePreview.body || "";
    templatePreview.append(body);
  }

  document.querySelectorAll("[data-preview-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-preview-mode]").forEach((item) => item.classList.toggle("active", item === button));
      renderTemplatePreview(button.dataset.previewMode || "visual");
    });
  });

  document.querySelector("[data-create-template]")?.addEventListener("click", () => {
    resetForm(templateForm);
    collapseTemplateVariables();
    templateForm.querySelector('[name="enabled"]').checked = true;
    updateBuiltinFormatLock();
    deriveTemplateChannel();
    updateInsertTarget(templateBody);
    resetTemplatePreview();
    updateTemplateSample();
  });

  document.querySelectorAll("[data-edit-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = itemFrom(button);
      resetForm(templateForm);
      collapseTemplateVariables();
      ["template_id", "name", "event_type", "channel_type", "locale", "subject_template", "body_template", "content_type"].forEach((name) => {
        const field = templateForm.querySelector(`[name="${name}"]`);
        if (field) field.value = item[name] ?? (name === "template_id" ? 0 : "");
      });
      templateForm.querySelector('[name="enabled"]').checked = !!item.enabled;
      updateBuiltinFormatLock(item);
      if (templateEvent) templateEvent.value = "*";
      deriveTemplateChannel();
      updateInsertTarget(templateBody);
      resetTemplatePreview();
      updateTemplateSample();
      openModal("templateModal");
    });
  });

  document.getElementById("previewTemplate")?.addEventListener("click", async () => {
    const data = new FormData(templateForm);
    data.set("csrf_token", csrfToken);
    try {
      const result = await window.NB.api.request("/notifications/template-preview", { method: "POST", body: data });
      templatePreview.replaceChildren();
      if (!result.ok || !result.data?.success) {
        const error = result.data?.error || {};
        templatePreview.textContent = [error.message || previewFailed, error.detail].filter(Boolean).join("\n");
        if (templatePreviewModes) templatePreviewModes.hidden = true;
        return;
      }
      lastTemplatePreview = {
        subject: result.data.subject || "",
        body: result.data.body || "",
        contentType: templateContentType?.value || "text",
      };
      if (result.data.context) {
        const sampleData = document.getElementById("templateSampleData");
        if (sampleData) sampleData.textContent = JSON.stringify(result.data.context, null, 2);
      }
      if (templatePreviewModes) templatePreviewModes.hidden = false;
      document.querySelectorAll("[data-preview-mode]").forEach((item) => item.classList.toggle("active", item.dataset.previewMode === "visual"));
      renderTemplatePreview("visual");
    } catch (error) {
      templatePreview.textContent = error.message || previewFailed;
      if (templatePreviewModes) templatePreviewModes.hidden = true;
    }
  });

  const messages = document.getElementById("notificationMessages")?.dataset || {};

  function updateStatusBadge(container, enabled) {
    const badge = container.querySelector(".status-badge");
    if (!badge) return;
    const msg = enabled ? messages.enabled : messages.disabled;
    badge.className = `status-badge ${enabled ? "is-live" : "is-paused"}`;
    badge.innerHTML = `<i class="bi ${enabled ? "bi-check-circle-fill" : "bi-pause-circle-fill"}" aria-hidden="true"></i>${msg || ""}`;
  }

  function updateToggleButton(button, enabled) {
    const msg = enabled ? messages.disabled : messages.enabled;
    const icon = enabled ? "bi-pause-circle" : "bi-play-circle";
    button.dataset.enabled = enabled ? "0" : "1";
    button.className = `btn btn-sm state-action ${enabled ? "is-disable" : "is-enable"}`;
    button.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i>${msg || ""}`;
    window.NB.showToast(enabled ? messages.enabled : messages.disabled, "success");
  }

  async function ajaxToggle(url, enabled, button, container) {
    const formData = new FormData();
    formData.set("csrf_token", csrfToken);
    formData.set("enabled", enabled);
    try {
      const result = await window.NB.api.request(url, { method: "POST", body: formData });
      if (result.ok && result.data?.success) {
        const nowEnabled = result.data.enabled;
        if (container) container.classList.toggle("is-disabled", !nowEnabled);
        updateStatusBadge(container, nowEnabled);
        updateToggleButton(button, nowEnabled);
      } else {
        window.NB.showToast(result.data?.error?.message || requestFailed, "error");
      }
    } catch (error) {
      window.NB.showToast(error.message || requestFailed, "error");
    }
  }

  async function ajaxDelete(url, button, container, emptySelector, emptyHTML) {
    const formData = new FormData();
    formData.set("csrf_token", csrfToken);
    try {
      const result = await window.NB.api.request(url, { method: "POST", body: formData });
      if (result.ok && result.data?.success) {
        if (container) container.remove();
        const parent = document.querySelector(emptySelector);
        if (parent && !parent.querySelector(container?.tagName || "")) {
          parent.innerHTML = emptyHTML;
        }
        window.NB.showToast(messages.deleted || "已删除", "success");
      } else {
        window.NB.showToast(result.data?.error?.message || requestFailed, "error");
      }
    } catch (error) {
      window.NB.showToast(error.message || requestFailed, "error");
    }
  }

  document.querySelectorAll("[data-toggle-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      const channelId = button.dataset.toggleChannel;
      const card = button.closest(".channel-card");
      ajaxToggle(`/notifications/channels/${channelId}/enabled`, button.dataset.enabled, button, card);
    });
  });

  document.querySelectorAll("[data-delete-channel]").forEach((button) => {
    button.addEventListener("click", () => {
      const channelId = button.dataset.deleteChannel;
      const card = button.closest(".channel-card");
      const grid = document.querySelector(".channel-grid");
      const emptyHTML = grid?.querySelector(".notification-empty")?.outerHTML || "";
      window.NB.confirmDelete(button.dataset.confirmMsg || "", () => {
        ajaxDelete(`/notifications/channels/${channelId}/delete`, button, card, ".channel-grid", emptyHTML);
      });
    });
  });

  document.querySelectorAll("[data-toggle-policy]").forEach((button) => {
    button.addEventListener("click", () => {
      const policyId = button.dataset.togglePolicy;
      const row = button.closest(".policy-row");
      ajaxToggle(`/notifications/policies/${policyId}/enabled`, button.dataset.enabled, button, row);
    });
  });

  document.querySelectorAll("[data-delete-policy]").forEach((button) => {
    button.addEventListener("click", () => {
      const policyId = button.dataset.deletePolicy;
      const row = button.closest(".policy-row");
      const stack = document.querySelector(".policy-stack");
      const emptyHTML = stack?.querySelector(".notification-empty")?.outerHTML || "";
      window.NB.confirmDelete(button.dataset.confirmMsg || "", () => {
        ajaxDelete(`/notifications/policies/${policyId}/delete`, button, row, ".policy-stack", emptyHTML);
      });
    });
  });

  document.querySelectorAll("[data-toggle-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const templateId = button.dataset.toggleTemplate;
      const row = button.closest("tr");
      ajaxToggle(`/notifications/templates/${templateId}/enabled`, button.dataset.enabled, button, row);
    });
  });

  document.querySelectorAll("[data-delete-template]").forEach((button) => {
    button.addEventListener("click", () => {
      const templateId = button.dataset.deleteTemplate;
      const row = button.closest("tr");
      window.NB.confirmDelete(button.dataset.confirmMsg || "", () => {
        ajaxDelete(`/notifications/templates/${templateId}/delete`, button, row);
      });
    });
  });

  document.getElementById("btn-test-channel")?.addEventListener("click", async function () {
    const button = this;
    const form = channelForm;
    const data = new FormData(form);
    button.disabled = true;
    try {
      data.set("csrf_token", csrfToken);
      const result = await window.NB.api.request("/notifications/channels/test", { method: "POST", body: data });
      const message = result.data?.message || result.data?.error?.message || "";
      window.NB.showToast(message, result.ok && result.data?.success ? "success" : "error");
    } catch (error) {
      window.NB.showToast(error.message || requestFailed, "error");
    } finally {
      button.disabled = false;
    }
  });

  updateChannelFields();
  updateTemplateSample();
})();
