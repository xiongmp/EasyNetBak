(function (global) {
  "use strict";
  const NB = global.NB = global.NB || {};
  const messages = global.NB_MESSAGES || {};
  const legacyMessages = global.NB_LEGACY_MESSAGES || {};
  const legacyEntries = Object.entries(legacyMessages).sort((a, b) => b[0].length - a[0].length);

  function translateLegacy(value) {
    let text = String(value == null ? "" : value);
    legacyEntries.forEach(([source, target]) => {
      text = text.split(source).join(target);
    });
    text = text
      .replace(/\b(Last \d+ (?:hours?|days?))(Backup tasks|Success rate|Duration distribution)\b/gi, "$1 $2")
      .replace(/\b(Last \d+ days),\s*按天\b/gi, "$1, by day")
      .replace(/，\s*/g, ", ")
      .replace(/\bConfiguration change frequency\s*\((Last \d+ days?)\)/gi, "Configuration change frequency ($1)")
      .replace(/\bSelected\s+([0-9]+)\s+台\b/g, "Selected $1")
      .replace(/\bWhy\s*Configuration\s*Ignore rules[?？]?/gi, "Why configure ignore rules?")
      .replace(/\bRule\s*Configuration\s*(?:notes|说明)[:：]?/gi, "Rule configuration notes:")
      .replace(/\bBy\s*Add\s*Ignore rules\b/gi, "By adding ignore rules")
      .replace(/为什么要\s*Configuration\s*Ignore rules[?？]?/gi, "Why configure ignore rules?")
      .replace(/通过\s*Add\s*Ignore rules,\s*you can filter out this noise,\s*只关注真正的\s*Configuration\s*更改\.?/gi, "By adding ignore rules, you can filter out this noise and focus only on real configuration changes.")
      .replace(/Device group:\s*仅应用于特定\s*Group\s*内的\s*Device\.?/gi, "Device group: Applies only to devices in the selected group.")
      .replace(/\bSearch\s*Device\s*name\s*or\s*(?:IP)?\.{0,3}/gi, "Search device name or IP...")
      .replace(/([A-Za-z0-9)])：/g, "$1:")
      .replace(/\bTotal\s{2,}/g, "Total ")
      .replace(/;?\s*华为\/H3C\s+(?:Use|uses)\b/g, "; Huawei/H3C uses")
      .replace(/Example:\s*(Cisco|Huawei\/H3C)/g, "Example: $1")
      .replace(/System员(?:\s*\(Full Access\))?/g, "System admin")
      .replace(/Actions员/g, "Operator")
      .replace(/Read-only用户/g, "Read-only user")
      .replace(/对于 QQ、163 等公Total邮箱,\s*通常需要Use\s*$/g, "For public mail providers such as QQ and 163, you usually need ")
      .replace(/Recovery code作为AdministratorEnableMFA后一组一次性Use的备用Verification code,\s*old recovery codes become invalid immediately after regeneration,\s*each recovery code can only be used once,\s*used codes become invalid/g, "Recovery codes are one-time backup verification codes generated after an administrator enables MFA. Old recovery codes become invalid immediately after regeneration, and each code can only be used once.")
      .replace(/\s{2,}/g, " ");
    text = text
      .replace(/（\s*(Last \d+ days?)\s*）/gi, "($1)")
      .replace(/\((?:最近|鏈€杩?)\s*([0-9]+)\s*天\)/gi, "(Last $1 days)")
      .replace(/\bConfiguration\s*Ignore rules\b/gi, "Configuration ignore rules")
      .replace(/\bAdd\s*Ignore rules\b/gi, "add ignore rules")
      .replace(/\bSearch\s*users?\s*(?:名|name)?\s*or\s*IP\s*(?:address|地址)?\.{0,3}/gi, "Search username or IP address")
      .replace(/\bUse\s*Device\s*Default\s*Template(?:\s*commands?)?\b/gi, "Use device default template")
      .replace(/\bSelect\s*Device\b/gi, "Select device")
      .replace(/\b(Cisco|Huawei\/H3C)\s+Use\b/g, "$1 uses")
      .replace(/\bFor example\s*[:：]\s*/gi, "For example: ")
      .replace(/\bExamples\s*[:：]?/gi, "Examples:")
      .replace(/\bIP\s*address\b/gi, "IP address")
      .replace(/。/g, ".")
      .replace(/；/g, ";")
      .replace(/：/g, ":")
      .replace(/\b(System)\s*员\b/gi, "System admin")
      .replace(/SMTP ServerConfiguration/g, "SMTP server configuration")
      .replace(/SMTP 邮件ServerAddress\s*[（(]If smtp\.office365\.com[）)]/g, "SMTP mail server address (for example smtp.office365.com)")
      .replace(/请确保Sender address与 SMTP Account具有相同的域名orPermissions, 否则可能被Server拒绝\.?/g, "Make sure the sender address uses the same domain or permissions as the SMTP account, otherwise the server may reject it.")
      .replace(/对于 QQ、163 等公Total邮箱, 通常需要Use an authorization code 而非邮箱Login password\.?/g, "For public mail providers such as QQ and 163, you usually need an authorization code instead of the mailbox login password.")
      .replace(/而非邮箱Login password\.?/g, "instead of the mailbox login password.")
      .replace(/多items收件人请用英文逗day分隔, 建议先Send test emailVerifyConfiguration\.?/g, "Separate multiple recipients with commas. Send a test email first to verify the configuration.")
      .replace(/建议Enable SSL\/TLS 加密[（(]通常为Port 465 or 587[）)]\.?本System不会明文传输您的邮件Password\.?/g, "SSL/TLS encryption is recommended (usually port 465 or 587). This system never transmits your email password in plain text.")
      .replace(/控制同时进Row的Backup tasks及批量检测Tasks的数量\.?\s*建议值:\s*10-30/g, "Control concurrent backup and bulk test task counts. Recommended: 10-30.")
      .replace(/备份Failed后的自动Retry次数\.?\s*范围:\s*0-10/g, "Automatic retry count after backup failure. Range: 0-10.")
      .replace(/Initial retry interval\. Later waits increase exponentially\.[（(]For example基数为 10s, 则Retry等待Time分别为 10s, 20s, 40s\.\.\.[）)]/g, "Initial retry interval. Later waits increase exponentially. For base 10s, waits are 10s, 20s, 40s...")
      .replace(/单backup tasks的Maximum运RowTime[（(]硬Timeout[）)]\.?\s*0 表示不限制,\s*建议 300\./g, "Maximum runtime for a single backup task (hard timeout). 0 means unlimited. Recommended: 300.")
      .replace(/超过此days数的备份将被自动清理,\s*Default 90\./g, "Backups older than this many days are cleaned automatically. Default: 90.")
      .replace(/超过此days数的Audit logs将被自动清理,\s*Default 180\./g, "Audit logs older than this many days are cleaned automatically. Default: 180.")
      .replace(/超过此days数的Login logs将被自动清理,\s*Default 180\./g, "Login logs older than this many days are cleaned automatically. Default: 180.")
      .replace(/超过此days数的 Webshell Actions录像将被自动清理,\s*Default 30\./g, "WebShell recordings older than this many days are cleaned automatically. Default: 30.")
      .replace(/Upload path:\s*\[Prefix\]\/YYYY-MM-DD\/File name,\s*Default为backups/g, "Upload path: [Prefix]/YYYY-MM-DD/File name. Default: backups.")
      .replace(/Enable S3 外部Storage备份/g, "Enable S3 external backup storage")
      .replace(/Enable FTP Storage备份/g, "Enable FTP backup storage")
      .replace(/When enabled,\s*Configuration信息除Saveat数据库还会自动上传副本到指定的 S3 兼容Storage桶/g, "When enabled, configuration is saved in the database and also uploaded to the specified S3-compatible bucket.")
      .replace(/When enabled,\s*Configuration信息除Saveat数据库还会自动上传副本到 FTP Server/g, "When enabled, configuration is saved in the database and also uploaded to the FTP server.")
      .replace(/支持域名or IP address,\s*For example 192\.168\.1\.10/g, "Supports domain names or IP addresses, for example 192.168.1.10")
      .replace(/Default 21,\s*若服务端UseCustomPort请同步填写/g, "Default is 21. Enter the custom port if the server uses one.")
      .replace(/该编码仅用于 FTP 目录名和File name,\s*请与远端 FTP Server编码保持一致,\s*否则可能出现乱码\.?/g, "This encoding is only used for FTP directory and file names. Match the remote FTP server encoding to avoid garbled text.")
      .replace(/给这items API Key 起一items易于识别的名字\.?/g, "Give this API key an easy-to-recognize name.")
      .replace(/Password长度建议不少于 8 位/g, "Password should be at least 8 characters")
      .replace(/勾选Group以分配Permissions;?\s*Granting a parent automatically includes all descendants\.?/g, "Select groups to assign permissions. Granting a parent automatically includes all descendants.")
      .replace(/所有Group\s*\(无限制\)/g, "All groups (unrestricted)")
      .replace(/对于 QQ、163 等公Total邮箱,\s*通常需要Use an authorization code instead of the mailbox login password\.?\.?/g, "For public mail providers such as QQ and 163, you usually need an authorization code instead of the mailbox login password.")
      .replace(/S3\s+Connected,\s*写入PermissionsVerify通过\.?/gi, "S3 connected. Write permission verified.")
      .replace(/S3\s+连接成功,?\s*写入PermissionsVerify通过\.?/gi, "S3 connected. Write permission verified.")
      .replace(/S3\s+连接成功,?\s*写入权限验证通过\.?/gi, "S3 connected. Write permission verified.")
      .replace(/S3\s+Connected,\s*Write permission verified\.?/gi, "S3 connected. Write permission verified.")
      .replace(/S3\s+连接Failed/gi, "S3 connection failed")
      .replace(/S3\s+connection\s+Failed/gi, "S3 connection failed")
      .replace(/FTP\s+连接Failed[（(]Path encoding:\s*([^）)]+)[）)]\s*:\s*/gi, "FTP connection failed (path encoding: $1): ")
      .replace(/FTP\s+连接失败[（(]Path encoding:\s*([^）)]+)[）)]\s*:\s*/gi, "FTP connection failed (path encoding: $1): ")
      .replace(/FTP\s+连接失败[（(]路径编码:\s*([^）)]+)[）)]\s*:\s*/gi, "FTP connection failed (path encoding: $1): ")
      .replace(/FTP\s+Connected,\s*Current path encoding:\s*([^\s.]+)\.?/gi, "FTP connected. Current path encoding: $1.")
      .replace(/FTP\s+连接成功,\s*当前路径编码:\s*([^\s.]+)\.?/gi, "FTP connected. Current path encoding: $1.")
      .replace(/由于目标计算机积极拒绝，无法连接\.?/g, "The target computer actively refused the connection.")
      .replace(/由于目标计算机积极拒绝,\s*无法连接\.?/g, "The target computer actively refused the connection.")
      .replace(/\s{2,}/g, " ");
    text = text
      .replace(/Confirm要Delete此Group\?\?/g, "Delete this group?")
      .replace(/Confirm要Delete此Template\?\?/g, "Delete this template?")
      .replace(/Confirm要Delete此用户\?\?/g, "Delete this user?")
      .replace(/Confirm要Delete此Role\?\?/g, "Delete this role?")
      .replace(/Confirm要Delete此Schedules\?\?/g, "Delete this schedule?")
      .replace(/Confirm要Permanent移除这itemsIgnore rules\?\?/g, "Permanently remove these ignore rules?")
      .replace(/Confirm要Delete此(Group|Template|Role|Schedules?)\?\?/gi, function(_, name) {
        return "Delete this " + name.toLowerCase().replace(/s$/, "") + "?";
      })
      .replace(/Confirm要Delete此(?:用户|User)\?\?/gi, "Delete this user?")
      .replace(/Confirm要Permanent移除这itemsIgnore rules\?\?/gi, "Permanently remove these ignore rules?")
      .replace(/Device已Delete/g, "Device deleted")
      .replace(/Credential已Delete/g, "Credential deleted")
      .replace(/Template已Delete/g, "Template deleted")
      .replace(/Group已Delete/g, "Group deleted")
      .replace(/Role已Delete/g, "Role deleted")
      .replace(/User已Delete/g, "User deleted")
      .replace(/已Delete/g, "Deleted")
      .replace(/已Save/g, "Saved")
      .replace(/已保存/g, "Saved")
      .replace(/已删除/g, "Deleted")
      .replace(/Confirm要Revoke API Key\s*$/g, "Revoke API key")
      .replace(/Confirm要Delete permanently API Key\s*$/g, "Delete API key permanently")
      .replace(/Revoke后该 Key 将立即失效,\s*无法再用于任何 API Access,\s*但记录会保留\.?/g, "After revocation, this key becomes invalid immediately and can no longer access the API. The record is retained.")
      .replace(/This action cannot be undone!\s*If you only want to disable it temporarily,\s*useRevokeFeature\.?/g, "This action cannot be undone. If you only want to disable it temporarily, use the revoke feature.")
      .replace(/拥有最高ManagePermissions,\s*可ManageAll devices、用户及完整审计Logs\.?/g, "Has full administrative access to manage devices, users, and audit logs.")
      .replace(/避免与Username过于相似\.?/g, "Avoid making it too similar to the username.")
      .replace(/组合大\/小写字母、数字和符day\.?/g, "Use a mix of uppercase and lowercase letters, numbers, and symbols.")
      .replace(/建议每 90 days更换一次Password\.?/g, "Change your password every 90 days.")
      .replace(/\s{2,}/g, " ");
    return text;
  }

  NB.i18n = {
    locale: global.NB_LOCALE || "zh-CN",
    isEnglish: /^en\b/i.test(global.NB_LOCALE || ""),
    messages: messages,
    t(key, params, fallback) {
      let text = Object.prototype.hasOwnProperty.call(messages, key)
        ? messages[key]
        : (fallback == null ? key : fallback);
      Object.entries(params || {}).forEach(([name, value]) => {
        text = text.split("{" + name + "}").join(String(value));
      });
      return translateLegacy(text);
    }
  };
  NB.t = NB.i18n.t.bind(NB.i18n);
  NB.translateLegacy = translateLegacy;
  NB.tr = function (value) {
    return translateLegacy(value);
  };
  NB.trHtml = function (value) {
    const template = document.createElement("template");
    template.innerHTML = String(value == null ? "" : value);
    localizeMarkedNode(template.content);
    return template.innerHTML;
  };

  function shouldSkipNode(root) {
    if (!root) return true;
    const element = root.nodeType === Node.ELEMENT_NODE ? root : root.parentElement;
    if (!element) return false;
    if (element.closest("[data-i18n-preserve]")) return true;
    if (element.closest("script, style, code, pre, textarea, [contenteditable='true']")) return true;
    return false;
  }

  function localizeMarkedNode(root) {
    if (!legacyEntries.length || !root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (shouldSkipNode(root)) return;
      const translated = translateLegacy(root.nodeValue);
      if (translated !== root.nodeValue) root.nodeValue = translated;
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (shouldSkipNode(root)) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      ["title", "placeholder", "aria-label", "data-confirm-message", "data-confirm-msg"].forEach((name) => {
        if (!root.hasAttribute(name)) return;
        const current = root.getAttribute(name);
        const translated = translateLegacy(current);
        if (translated !== current) root.setAttribute(name, translated);
      });
    }
    Array.from(root.childNodes || []).forEach(localizeMarkedNode);
  }

  function localizePageText(root) {
    if (!legacyEntries.length || !root || !NB.i18n.isEnglish) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (shouldSkipNode(root)) return;
      const translated = translateLegacy(root.nodeValue);
      if (translated !== root.nodeValue) root.nodeValue = translated;
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (shouldSkipNode(root)) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      ["title", "placeholder", "aria-label", "data-title", "data-confirm-message", "data-confirm-msg"].forEach((name) => {
        if (!root.hasAttribute(name)) return;
        const current = root.getAttribute(name);
        const translated = translateLegacy(current);
        if (translated !== current) root.setAttribute(name, translated);
      });
    }
    Array.from(root.childNodes || []).forEach(localizePageText);
  }

  const markedUiSelector = "[data-i18n-legacy], [data-i18n-key]";

  function localizeElement(root) {
    if (!root || root.nodeType !== Node.ELEMENT_NODE) return;
    const key = root.getAttribute("data-i18n-key");
    if (key) {
      root.textContent = NB.t(key, undefined, root.textContent || key);
      return;
    }
    localizeMarkedNode(root);
  }

  function localizeDynamicUi(root) {
    if (!root) return;
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(markedUiSelector)) localizeElement(root);
    if (typeof root.querySelectorAll === "function") {
      root.querySelectorAll(markedUiSelector).forEach(localizeElement);
    }
  }

  NB.localizeDynamicUi = localizeDynamicUi;
  NB.localizePageText = localizePageText;

  function localizeObject(value, seen) {
    if (typeof value === "string") return translateLegacy(value);
    if (!value || typeof value !== "object") return value;
    seen = seen || new WeakSet();
    if (seen.has(value)) return value;
    seen.add(value);
    Object.keys(value).forEach((key) => {
      if (typeof value[key] === "string") value[key] = translateLegacy(value[key]);
      else if (value[key] && typeof value[key] === "object") localizeObject(value[key], seen);
    });
    return value;
  }

  function wrapEcharts(library) {
    if (!library || library.__nbI18nWrapped || typeof library.init !== "function") return;
    const nativeInit = library.init.bind(library);
    library.init = function () {
      const chart = nativeInit.apply(library, arguments);
      const nativeSetOption = chart.setOption.bind(chart);
      chart.setOption = function (option) {
        arguments[0] = localizeObject(option);
        return nativeSetOption.apply(chart, arguments);
      };
      return chart;
    };
    library.__nbI18nWrapped = true;
  }

  function installFunctionTranslator(name, mapArguments) {
    let wrapped;
    Object.defineProperty(NB, name, {
      configurable: true,
      get() { return wrapped; },
      set(fn) {
        if (typeof fn !== "function") { wrapped = fn; return; }
        wrapped = function () {
          const args = Array.from(arguments);
          mapArguments(args);
          return fn.apply(NB, args);
        };
      }
    });
  }

  if (legacyEntries.length) {
    const nativeAlert = global.alert.bind(global);
    const nativeConfirm = global.confirm.bind(global);
    global.alert = (message) => nativeAlert(translateLegacy(message));
    global.confirm = (message) => nativeConfirm(translateLegacy(message));
    installFunctionTranslator("showToast", (args) => { args[0] = translateLegacy(args[0]); });
    installFunctionTranslator("confirmDelete", (args) => { args[0] = translateLegacy(args[0]); });
    installFunctionTranslator("confirm", (args) => {
      const options = Object.assign({}, args[0] || {});
      ["title", "message", "confirmBtnText"].forEach((key) => {
        if (options[key]) options[key] = translateLegacy(options[key]);
      });
      args[0] = options;
    });
    if (global.echarts) {
      wrapEcharts(global.echarts);
    } else {
      Object.defineProperty(global, "echarts", {
        configurable: true,
        set(value) {
          Object.defineProperty(global, "echarts", {value: value, writable: true, configurable: true});
          wrapEcharts(value);
        }
      });
    }
    document.addEventListener("DOMContentLoaded", () => {
      document.documentElement.lang = NB.i18n.locale;
      if (NB.i18n.isEnglish) document.documentElement.lang = "en";
      localizePageText(document.body);
      localizeDynamicUi(document.body);
      document.querySelectorAll("input, select, textarea").forEach((field) => {
        field.addEventListener("invalid", () => {
          if (!NB.i18n.isEnglish) return;
          if (field.validity && field.validity.valueMissing) {
            field.setCustomValidity("Please fill out this field.");
          }
        });
        field.addEventListener("input", () => field.setCustomValidity(""));
        field.addEventListener("change", () => field.setCustomValidity(""));
      });
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === "characterData") {
            localizePageText(mutation.target);
            return;
          }
          if (mutation.type === "attributes") {
            localizePageText(mutation.target);
            return;
          }
          mutation.addedNodes.forEach((node) => {
            localizePageText(node);
            localizeDynamicUi(node);
          });
        });
      });
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["title", "placeholder", "aria-label", "data-title", "data-confirm-message", "data-confirm-msg"]
      });
    });
  }
})(window);
