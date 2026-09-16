(() => {
  "use strict";
  const MF = window.MultiFilter;
  const $ = (s) => document.querySelector(s);
  const all = (s) => [...document.querySelectorAll(s)];
  const root = $(".app-shell");
  if (!root) return;
  MF.enable($('#dashboard-category'));
  const role = root.dataset.role;
  const readOnly = ['hr_viewer', 'rotation', 'recruitment'].includes(role);
  const state = { crews: [], crew: null, members: [], selected: new Set(), candidates: [],
    sites: [], objects: [], subs: [], busy: false, boardRequest: 0, location: null,
    display: "total", summaryMode: "week", calendars: {}, expanded: { plan: new Set(), dashboard: new Set() },
    expandedSubs: new Set(), pendingPlans: 0, planErrors: new Map(), planDrafts: new Set(), planWaiters: [], users: [], lastLocationObjectId: null };
  const E = (tag, props = {}, ...children) => {
    const el = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key.startsWith("on")) el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key in el) el[key] = value;
      else el.setAttribute(key, value);
    });
    children.flat().forEach((child) => { if (child != null) el.append(child); });
    return el;
  };
  const normalized = (text) => String(text || "").toLocaleLowerCase("ru").replace(/ё/g, "е");
  const matches = (text, query) => normalized(query).split(/\s+/).filter(Boolean).every((part) => text.includes(part));
  const humanDate = (value) => new Date(value + "T12:00:00").toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  const nextDate = (value, offset) => {
    const date = new Date(value + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10);
  };
  async function api(url, options = {}) {
    const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json",
      "X-CSRF-Token": root.dataset.csrf }, cache: "no-store" });
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || "Не удалось выполнить запрос."); error.status = response.status; throw error; }
    return data;
  }
  let toastTimer;
  function toast(message, error = false) {
    $("#toast").textContent = message;
    $("#toast").className = "toast show" + (error ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("#toast").classList.remove("show"), error ? 7000 : 3500);
  }
  function status(id, message, error = false) {
    const el = $(id);
    if (el) { el.textContent = message; el.classList.toggle("error-text", error); }
  }
  async function switchView(view, staffingFilter = null) {
    if (view === "categories") view = "catalogs";
    if (window.outstaffScreen && !window.outstaffScreen.canLeave()) return;
    if (window.outstaffImport && !window.outstaffImport.canLeave()) return;
    if (window.employeesScreen && !window.employeesScreen.canLeave()) return;
    if (window.categoriesScreen && !window.categoriesScreen.canLeave()) return;
    if (window.staffingScreen && !window.staffingScreen.canLeave()) return;
    if (window.backupsScreen && !window.backupsScreen.canLeave()) return;
    if (window.locationsScreen && !window.locationsScreen.canLeave()) return;
    if (window.workforceScreen && !window.workforceScreen.canLeave()) return;
    if (window.catalogsScreen && !window.catalogsScreen.canLeave()) return;
    if (!$("#view-" + view)) view = role === "viewer" ? "dashboard" : readOnly ? "staffing" : "placement";
    if (state.busy) { toast("Дождитесь сохранения."); return; }
    if (state.pendingPlans) await new Promise((resolve) => state.planWaiters.push(resolve));
    if ((state.planErrors.size || state.planDrafts.size) && view !== "plan") { toast("Завершите ввод в несохранённых ячейках плана.", true); return; }
    all(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + view));
    all("[data-view]").forEach((el) => { el.classList.toggle("active", el.dataset.view === view); });
    history.replaceState(null, "", "#" + view);
    try {
      if (view === 'workforce') { await window.workforceScreen.load(); return; }
      await loadLocationReference();
      if (view === "staffing") {
        if (staffingFilter) await window.staffingScreen.openFromCalendar(staffingFilter);
        else await window.staffingScreen.load();
      }
      if (view === "verification") await window.placementVerificationScreen.load();
      if (view === "outstaff") await window.outstaffScreen.load();
      if (view === "employees") await window.employeesScreen.load();
      if (view === "analytics") await window.personnelDashboard.load();
      if (view === "placement") await loadCrews();
      if (view === "accounts") await loadManagement();
      if (view === "backups") await window.backupsScreen.load();
      if (view === "logs") await window.logsScreen.load();
      if (view === "catalogs") await window.catalogsScreen.load();
      if (view === "plan" || view === "dashboard") await loadCalendar(view);
    } catch (error) { toast(error.message, true); }
  }
  window.openStaffingReport = filter => switchView('staffing', filter);
  all("[data-view]").forEach((el) => el.addEventListener("click", () => switchView(el.dataset.view)));
  $(".brand").addEventListener("click", (event) => { event.preventDefault(); switchView(role === "viewer" ? "dashboard" : "staffing"); });
  function setBusy(busy) {
    state.busy = busy;
    ["#work-date", "#work-shift", "#recruit-open", "#location-object-search", "#location-search"].forEach((id) => { if ($(id)) $(id).disabled = busy; });
    if ($("#crew-roster")) $("#crew-roster").inert = busy;
    if ($("#selection-clear")) $("#selection-clear").disabled = busy || !state.selected.size;
    renderLocations();
  }
  async function loadCrews() {
    if (role === "viewer") return;
    state.crews = (await api("/api/crews")).rows;
    if (!state.crews.some((crew) => crew.id === state.crew)) state.crew = state.crews[0]?.id || null;
    $("#crew-empty").hidden = !!state.crews.length;
    $("#crew-workspace").hidden = !state.crews.length;
    renderCrewTabs();
    if (state.crew) await loadBoard();
  }
  function renderCrewTabs() {
    $("#crew-tabs").replaceChildren(...state.crews.map((crew) => E("button", {
      className: crew.id === state.crew ? "active" : "", "aria-pressed": String(crew.id === state.crew),
      onclick: async () => { if (state.busy) return; state.crew = crew.id; state.selected.clear(); renderCrewTabs(); await loadBoard(); }
    }, E("strong", {}, crew.name), E("small", {}, ["admin", "super_admin"].includes(role) ? crew.owner_name : crew.member_count + " чел."))));
  }
  async function loadBoard() {
    const requestId = ++state.boardRequest;
    const crew = state.crew;
    if (!crew) return;
    state.location = null;
    renderLocations();
    status("#board-status", "Загрузка…");
    $("#crew-workspace").inert = true;
    try {
      const data = await api("/api/crews/" + crew + "/board?date=" + $("#work-date").value + "&shift=" + encodeURIComponent($("#work-shift").value));
      if (requestId !== state.boardRequest) return;
      state.members = data.members;
      state.selected = new Set([...state.selected].filter((id) => data.members.some((m) => m.id === id && !m.locked)));
      $("#crew-title").textContent = data.crew.name;
      renderMembers();
      status("#board-status", "Расстановка за " + humanDate(data.date) + " · " + ($("#work-shift").value === "1 смена" ? "день" : "ночь"));
    } catch (error) {
      if (requestId !== state.boardRequest) return;
      state.members = []; state.selected.clear(); renderMembers();
      status("#board-status", error.message, true);
      throw error;
    } finally { if (requestId === state.boardRequest) $("#crew-workspace").inert = false; }
  }
  function renderMembers() {
    $("#crew-progress").textContent = state.members.filter((m) => m.assignment_id).length + " из " + state.members.length + " с местом работы";
    $("#crew-members").replaceChildren(...state.members.map((member) => {
      const checkbox = E("input", { type: "checkbox", checked: state.selected.has(member.id), disabled: member.locked,
        "aria-label": "Выбрать " + member.full_name,
        onchange: () => { if (state.busy) return; checkbox.checked ? state.selected.add(member.id) : state.selected.delete(member.id); updateSelection(); } });
      const place = E("button", { className: "workplace" + (!member.assignment_id ? " unassigned" : ""),
        disabled: member.locked, "aria-controls": "location-panel", onclick: () => selectForLocation(member.id) },
        E("strong", {}, member.subobject_name || "Указать место работы"),
        E("small", {}, member.object_name || "Выберите объект / подобъект"));
      return E("div", { className: "roster-row" },
        E("label", { className: "member-person" }, checkbox, E("span", {}, E("strong", {}, member.full_name),
          E("small", {}, member.profession + " · " + member.personnel_no + " · " + member.employer))),
        E("div", { className: "member-site" }, place, member.locked ? E("small", {}, "Назначен другой бригадой") : null),
        E("button", { className: "remove-member", title: "Убрать из состава", "aria-label": "Убрать из состава: " + member.full_name,
          onclick: async () => {
            if (state.busy || !confirm("Убрать " + member.full_name + " из состава бригады? Расстановка по датам сохранится.")) return;
            setBusy(true);
            try { await api("/api/crews/" + state.crew + "/members/" + member.id, { method: "DELETE" }); await loadCrews(); }
            catch (error) { toast(error.message, true); } finally { setBusy(false); }
          } }, "×"));
    }));
    if (!state.members.length) $("#crew-members").append(E("div", { className: "empty-state" }, E("h2", {}, "Состав пока пуст"), E("p", {}, "Нажмите «Добавить людей» и наберите бригаду из списка сотрудников.")));
    updateSelection();
  }
  function updateSelection() {
    const available = state.members.filter((m) => !m.locked);
    $("#select-crew").checked = !!available.length && state.selected.size === available.length;
    $("#select-crew").indeterminate = state.selected.size > 0 && state.selected.size < available.length;
    syncLocationSelection();
  }
  $("#select-crew")?.addEventListener("change", (event) => {
    if (state.busy) return;
    state.selected = event.target.checked ? new Set(state.members.filter((m) => !m.locked).map((m) => m.id)) : new Set();
    renderMembers();
  });
  $("#selection-clear")?.addEventListener("click", () => { if (state.busy) return; state.selected.clear(); renderMembers(); });
  ["#work-date", "#work-shift"].forEach((id) => $(id)?.addEventListener("change", async () => {
    state.selected.clear();
    try { await loadBoard(); } catch (_) { /* Error remains visible on the board. */ }
  }));
  all("[data-close]").forEach((button) => button.addEventListener("click", () => { if (!state.busy) $("#" + button.dataset.close).close(); }));
  all("dialog").forEach((dialog) => dialog.addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); }));
  $("#recruit-open")?.addEventListener("click", async () => {
    if (state.busy) return;
    $("#recruit-dialog").showModal();
    $("#recruit-search").value = "";
    $("#recruit-list").replaceChildren();
    status("#recruit-status", "Загрузка списка…");
    try { state.candidates = (await api("/api/crews/" + state.crew + "/candidates")).rows; renderCandidates(); status("#recruit-status", ""); }
    catch (error) { status("#recruit-status", error.message, true); }
  });
  function renderCandidates() {
    const rows = state.candidates.filter((m) => matches(normalized([m.full_name, m.personnel_no, m.profession, m.employer].join(" ")), $("#recruit-search").value));
    $("#recruit-list").replaceChildren(...rows.map((m) => E("button", {
      className: "picker-item", disabled: m.crew_id != null || state.busy, "aria-label": "Добавить " + m.full_name,
      onclick: async () => {
        if (state.busy) return;
        setBusy(true); renderCandidates(); status("#recruit-status", "Добавление…");
        try {
          await api("/api/crews/" + state.crew + "/members", { method: "POST", body: JSON.stringify({ worker_ids: [m.id] }) });
          m.crew_id = state.crew; m.crew_name = state.crews.find((c) => c.id === state.crew).name;
          await loadCrews(); status("#recruit-status", "Добавлен: " + m.full_name);
        } catch (error) { status("#recruit-status", error.message, true); }
        finally { setBusy(false); renderCandidates(); }
      }
    }, E("span", {}, E("strong", {}, m.full_name), E("small", {}, m.personnel_no + " · " + m.profession + " · " + m.employer)),
      E("span", { className: "candidate-state" }, m.crew_id === state.crew ? "В бригаде ✓" : m.crew_id ? m.crew_name : "+"))));
    if (!rows.length) $("#recruit-list").append(E("p", {}, "Сотрудники не найдены."));
  }
  $("#recruit-search")?.addEventListener("input", renderCandidates);
  function selectForLocation(id) {
    if (state.busy) return;
    state.selected = new Set([id]);
    renderMembers();
    $("#location-panel").focus({ preventScroll: true });
    $("#location-panel").scrollIntoView({ block: "nearest" });
  }
  function syncLocationSelection() {
    const ids = [...state.selected];
    const changed = !state.location || ids.join(",") !== state.location.ids.join(",");
    const objectIds = ids.map((id) => {
      const subobjectId = state.members.find((member) => member.id === id)?.subobject_id;
      return state.sites.find((site) => site.id === subobjectId)?.object_id ?? null;
    });
    const sharedObjectId = objectIds.every((id) => id !== null) && new Set(objectIds).size === 1 ? objectIds[0] : null;
    if (changed && sharedObjectId && sharedObjectId !== state.lastLocationObjectId) {
      state.lastLocationObjectId = sharedObjectId;
      $("#location-object-search").value = ""; $("#location-search").value = "";
    }
    state.location = ids.length ? { crew: state.crew, date: $("#work-date").value, shift: $("#work-shift").value, ids,
      tokens: Object.fromEntries(ids.map((id) => [id, state.members.find((m) => m.id === id).edit_token])) } : null;
    $("#location-context").textContent = ids.length ?
      (ids.length === 1 ? state.members.find((m) => m.id === ids[0]).full_name : "Выбрано сотрудников: " + ids.length) +
      " · " + humanDate(state.location.date) + " · " + (state.location.shift === "1 смена" ? "день" : "ночь") :
      "Выберите сотрудников в составе бригады.";
    $("#location-panel").classList.toggle("has-selection", !!ids.length);
    $("#selection-clear").disabled = state.busy || !ids.length;
    status("#location-status", "");
    renderLocations();
  }
  function renderLocations() {
    if (!$("#location-panel")) return;
    $("#location-clear").disabled = state.busy || !state.location;
    const objectQuery = $("#location-object-search").value;
    const objects = state.objects.filter((object) => matches(normalized(object.name), objectQuery));
    $("#location-object-list").replaceChildren(...objects.map((object) => E("button", { className: "picker-item location-object-button",
      disabled: state.busy, "aria-pressed": String(state.lastLocationObjectId === object.id), onclick: () => {
        if (state.busy) return;
        state.lastLocationObjectId = object.id; $("#location-search").value = ""; renderLocations();
      } }, E("span", {}, E("strong", {}, object.name)))));
    if (!objects.length) $("#location-object-list").append(E("p", {}, "Объекты не найдены."));
    const selectedObject = state.objects.find((object) => object.id === state.lastLocationObjectId);
    $("#location-subobject-heading").textContent = selectedObject ? selectedObject.name : "Выберите объект слева";
    if (!selectedObject) {
      $("#location-list").replaceChildren(E("p", {}, "Сначала выберите объект."));
      return;
    }
    const query = $("#location-search").value;
    const sites = state.sites.filter((site) => site.object_id === selectedObject.id && matches(site.search, query));
    $("#location-list").replaceChildren(...sites.map((site) => E("button", { className: "picker-item", disabled: state.busy || !state.location,
      onclick: () => assignLocation(site.id) }, E("span", {}, E("strong", {}, site.name)))));
    if (!sites.length) $("#location-list").append(E("p", {}, "Подобъекты не найдены."));
  }
  async function assignLocation(site) {
    if (state.busy || !state.location) return;
    const intent = state.location;
    setBusy(true); status("#location-status", "Сохранение…");
    try {
      await api("/api/crews/" + intent.crew + "/assignments", { method: "PUT", body: JSON.stringify({
        date: intent.date, shift: intent.shift, worker_ids: intent.ids, subobject_id: site, expected_tokens: intent.tokens
      }) });
      state.selected.clear();
      await loadBoard();
      status("#location-status", "Сохранено. Выберите следующих сотрудников.");
      toast("Сохранено за " + humanDate(intent.date) + ": " + intent.ids.length + " чел.");
    } catch (error) {
      if (error.status === 409) {
        state.selected.clear();
        await loadBoard().catch(() => {});
      }
      status("#location-status", error.message + (error.status === 409 ? " Выберите сотрудников заново." : ""), true);
    } finally { setBusy(false); }
  }
  $("#location-object-search")?.addEventListener("input", renderLocations);
  $("#location-search")?.addEventListener("input", renderLocations);
  $("#location-clear")?.addEventListener("click", () => assignLocation(null));

  function calendarKey(site, day) { return site + ":" + day; }
  function calendarIndex(data) {
    const plans = new Map(data.plans.map((p) => [calendarKey(p.subobject_id, p.work_date), p]));
    const facts = new Map();
    data.facts.forEach((f) => {
      const key = calendarKey(f.subobject_id, f.work_date);
      const entry = facts.get(key) || { day: 0, night: 0, orgs: new Map(), companies: new Map() };
      entry.day += f.day_count; entry.night += f.night_count;
      const org = entry.orgs.get(f.employer) || {day: 0, night: 0};
      org.day += f.day_count; org.night += f.night_count; entry.orgs.set(f.employer, org);
      const company = entry.companies.get(f.contractor) || {day: 0, night: 0, orgs: new Map()};
      company.day += f.day_count; company.night += f.night_count;
      const companyOrg = company.orgs.get(f.employer) || {day: 0, night: 0};
      companyOrg.day += f.day_count; companyOrg.night += f.night_count;
      company.orgs.set(f.employer, companyOrg); entry.companies.set(f.contractor, company);
      facts.set(key, entry);
    });
    return { ...data, plansByKey: plans, factsByKey: facts };
  }
  async function loadCalendar(view) {
    if (view === 'dashboard' && ['placement', 'category'].includes(state.summaryMode)) return window.placementReport.load();
    const report = view === "dashboard" && state.summaryMode === "date";
    const dateInput = report ? $("#report-date") : $("#" + view + "-start");
    if (state.pendingPlans) await new Promise((resolve) => state.planWaiters.push(resolve));
    if (state.pendingPlans || state.planErrors.size || state.planDrafts.size) {
      if (state.calendars[view]?.dates) dateInput.value = state.calendars[view].dates[0];
      if (view === "dashboard") MF.set($("#dashboard-category"), state.calendars[view]?.categorySelection || "");
      toast("Сначала завершите ввод в плане."); return;
    }
    const start = dateInput.value;
    if (!start) {
      state.calendars[view] = {requestId: (state.calendars[view]?.requestId || 0) + 1};
      $("#" + view + "-table").replaceChildren(); $("#" + view + "-table").inert = false;
      if (view === "dashboard") $("#dashboard-absences").replaceChildren();
      status("#" + view + "-status", "Укажите дату.", true); return;
    }
    const categorySelection = view === "dashboard" ? MF.get($("#dashboard-category")) : "";
    const query = new URLSearchParams({start, days: report ? '1' : '7'});
    MF.params(query, 'category', categorySelection, value => JSON.parse(value));
    status("#" + view + "-status", "Загрузка…");
    if (view === "dashboard") $("#dashboard-absences").replaceChildren();
    const requestId = (state.calendars[view]?.requestId || 0) + 1;
    state.calendars[view] = { ...state.calendars[view], requestId };
    $("#" + view + "-table").inert = true;
    try {
      const data = await api("/api/calendar?" + query);
      if (state.calendars[view].requestId !== requestId) return;
      state.calendars[view] = { ...calendarIndex(data), requestId, categorySelection, summaryMode: report ? 'date' : 'week' };
      if (view === "dashboard") {
        const categories = [...data.categories];
        const selected = MF.values(categorySelection).map(value => JSON.parse(value));
        selected.forEach(value => { if(!categories.includes(value)) categories.push(value); });
        $("#dashboard-category").replaceChildren(E('option', {value: ''}, 'Все категории'),
          ...categories.map(category => E('option', {value: JSON.stringify(category)}, category || 'Без категории')));
        MF.set($("#dashboard-category"), categorySelection);
        const note = $("#dashboard-category-note");
        note.hidden = !categorySelection;
        note.textContent = categorySelection ? 'Факт: ' + selected.map(value => value || 'Без категории').join(', ') + (report ? '.' : '. План — общий по всем категориям.') : '';
      }
      renderCalendar(view); status("#" + view + "-status", view === "dashboard" ? "" : "Актуально");
    } catch (error) {
      if (state.calendars[view].requestId !== requestId) return;
      $("#" + view + "-table").replaceChildren(E("div", { className: "empty-state" }, E("p", {}, error.message),
        E("button", { className: "secondary-button", onclick: () => loadCalendar(view) }, "Повторить")));
      status("#" + view + "-status", "Не удалось загрузить", true);
    } finally { if (state.calendars[view].requestId === requestId) $("#" + view + "-table").inert = false; }
  }
  const columns = () => state.display === "total" ? ["Всего"] : state.display === "shifts" ? ["День", "Ночь"] : ["День", "Ночь", "Всего"];
  function aggregate(data, ids, day, organization, contractor) {
    let planned = 0, hasPlan = false, actualDay = 0, actualNight = 0;
    ids.forEach((id) => {
      const plan = data.plansByKey.get(calendarKey(id, day));
      if (plan && organization === undefined && contractor === undefined) { hasPlan = true; planned += plan.planned_count; }
      const fact = data.factsByKey.get(calendarKey(id, day));
      const source = contractor === undefined ? (organization === undefined ? fact : fact?.orgs.get(organization))
        : organization === undefined ? fact?.companies.get(contractor) : fact?.companies.get(contractor)?.orgs.get(organization);
      actualDay += source?.day || 0; actualNight += source?.night || 0;
    });
    return { planned: hasPlan ? planned : null, day: actualDay, night: actualNight };
  }
  function hasDashboardActivity(data, subobjectId) {
    return data.dates.some((day) => {
      if (data.plansByKey.has(calendarKey(subobjectId, day))) return true;
      const fact = data.factsByKey.get(calendarKey(subobjectId, day));
      return (fact?.day || 0) > 0 || (fact?.night || 0) > 0;
    });
  }
  function labelCell(text, level, expanded, toggle, span) {
    const contents = toggle ? E("button", { className: "tree-label", "aria-expanded": String(expanded), onclick: toggle },
      E("span", { className: "disclosure", "aria-hidden": "true" }, expanded ? "▾" : "▸"), E("span", {}, text))
      : E("span", { className: "tree-label" }, text);
    return E("th", { className: "structure level-" + level, rowSpan: span || 1, scope: "row" }, contents);
  }
  function renderCalendar(view) {
    const data = state.calendars[view]; if (!data?.dates) return;
    if (view === "dashboard") window.attendanceSummary.render($("#dashboard-absences"), data, $("#dashboard-search").value);
    if (view === "dashboard" && state.summaryMode === "date") {
      if (data.summaryMode !== 'date') return;
      window.reportDateSummary.render($("#dashboard-table"), {
        data, objects: state.objects, subobjects: state.subs, query: $("#dashboard-search").value,
        canDrill: role !== 'viewer', openStaffing: filter => switchView('staffing', filter)
      });
      return;
    }
    if (view === "dashboard" && data.summaryMode === 'date') return;
    const isPlan = view === "plan", cols = isPlan ? ["План"] : columns(), width = cols.length;
    const contractors = [...new Set(data.facts.map(f => f.contractor))].sort((a,b) => a.localeCompare(b, 'ru'));
    const companyColumns = isPlan ? [undefined] : [...contractors, undefined];
    const dateWidth = width * companyColumns.length;
    const query = $("#" + view + "-search").value;
    const table = E("table", { className: "calendar-table " + (isPlan ? "plan-table" : "fact-table") });
    const head = E("thead"), main = E("tr", {}, E("th", { className: "structure", rowSpan: isPlan ? 1 : 3, scope: "col" }, "Объект / подобъект"));
    if (!isPlan) main.append(E("th", { className: "kind-head", rowSpan: 3, scope: "col" }, ""));
    data.dates.forEach((day) => main.append(E("th", { colSpan: dateWidth, scope: "colgroup", className: "date-heading" }, humanDate(day),
      E("small", {}, new Date(day + "T12:00:00").toLocaleDateString("ru-RU", { weekday: "short" })))));
    head.append(main);
    if (!isPlan) {
      head.append(E("tr", {}, data.dates.flatMap(() => companyColumns.map(company => E("th", {colSpan: width, scope: "colgroup", className: "company-heading"}, company === undefined ? "Общий итог" : company || "Подрядчик не указан")))));
      head.append(E("tr", {}, data.dates.flatMap(() => companyColumns.flatMap(() => cols.map(name => E("th", {scope: "col", className: "shift-heading"}, name))))));
    }
    table.append(head);
    const body = E("tbody");
    const appendRows = (label, ids, level, expanded, toggle, organization, totalKey) => {
      if (isPlan) {
        const row = E("tr", { className: level === 0 ? "object-row" : "subobject-row" }, labelCell(label, level, expanded, toggle));
        data.dates.forEach((day) => {
          const td = E("td", { className: "num" });
          if (level === 1 && !readOnly) {
            const key = calendarKey(ids[0], day), plan = data.plansByKey.get(key);
            const input = E("input", { type: "text", inputMode: "numeric", pattern: "[0-9]*",
              className: "plan-cell", value: plan ? String(plan.planned_count) : "",
              placeholder: "—", "aria-label": label + ", " + humanDate(day), "data-plan-key": key,
              oninput: (event) => {
                const saved = data.plansByKey.get(key);
                if (event.target.value === (saved ? String(saved.planned_count) : "")) state.planDrafts.delete(key);
                else state.planDrafts.add(key);
              },
              onfocus: (event) => event.target.select(), onblur: (event) => savePlanCell(event.target, ids[0], day),
              onkeydown: (event) => {
                if (event.key === "Enter") {
                  event.preventDefault(); const inputs = all("#plan-table input.plan-cell");
                  const next = inputs[inputs.indexOf(event.target) + 1]; if (next) next.focus(); else event.target.blur();
                }
              }
            });
            td.append(input);
          } else {
            const value = aggregate(data, ids, day).planned;
            td.textContent = value === null ? "—" : String(value);
            td.dataset.sumIds = ids.join(","); td.dataset.sumDate = day;
          }
          row.append(td);
        });
        body.append(row); return;
      }
      const planned = E("tr", { className: "plan-row " + (level === 0 ? "object-row" : "") },
        labelCell(label, level, expanded, toggle, 2), E("th", { className: "kind", scope: "row" }, "План"));
      const actual = E("tr", { className: "actual-row " + (level === 0 ? "object-row" : "") }, E("th", { className: "kind", scope: "row" }, "Факт"));
      data.dates.forEach((day) => {
        const values = aggregate(data, ids, day, organization);
        planned.append(E("td", { colSpan: dateWidth, className: "num daily-plan", title: "Общий план на сутки" }, values.planned === null ? "—" : String(values.planned)));
        companyColumns.forEach(contractor => {
        const companyValues = aggregate(data, ids, day, organization, contractor);
        cols.forEach((col) => {
          const value = col === "День" ? companyValues.day : col === "Ночь" ? companyValues.night : companyValues.day + companyValues.night;
          const shift = col === "День" ? '1 смена' : col === "Ночь" ? '2 смена' : '';
          const caption = 'Показать сотрудников: ' + label + ', ' + humanDate(day) + ', ' + col + ', ' + value + ' чел.';
          actual.append(E("td", { className: "num" + (value === 0 ? " zero" : "") },
            value > 0 && role !== 'viewer' ? E('button', {type: 'button', className: 'fact-link',
              title: caption, 'aria-label': caption,
              onclick: () => switchView('staffing', {sites: ids, date: day, shift,
                label: organization === undefined ? label : state.subs.find(site => site.id === ids[0]).name + ' · ' + label,
                employer: organization, contractor,
                category: data.categorySelection ? MF.values(data.categorySelection).map(value => JSON.parse(value)) : undefined})}, String(value)) : String(value)));
        });
        });
      });
      body.append(planned, actual);
    };
    const onlyWithActivity = view === "dashboard" && $("#dashboard-nonempty").checked;
    const visible = state.objects.map((object) => ({ object, subs: state.subs.filter((s) => s.object_id === object.id &&
      (!query || matches(normalized(object.name + " " + s.name), query)) &&
      (!onlyWithActivity || hasDashboardActivity(data, s.id))) })).filter((item) => item.subs.length);
    if (!visible.length) {
      body.append(E("tr", {}, E("td", { colSpan: (isPlan ? 1 : 2) + data.dates.length * dateWidth, className: "calendar-empty" },
        onlyWithActivity ? "Нет объектов с планом или фактом за выбранную неделю." : "По вашему запросу ничего не найдено.")));
      table.append(body);
      $("#" + view + "-table").replaceChildren(table);
      return;
    }
    appendRows(query ? "Итого по найденному" : "Итого", visible.flatMap((v) => v.subs.map((s) => s.id)), 0, false, null);
    visible.forEach(({ object, subs }) => {
      const open = !!query || state.expanded[view].has(object.id);
      appendRows(object.name, subs.map((s) => s.id), 0, open, () => {
        if (state.pendingPlans || state.planErrors.size) return;
        open ? state.expanded[view].delete(object.id) : state.expanded[view].add(object.id); renderCalendar(view);
      });
      if (open) subs.forEach((sub) => {
        const orgs = new Set(data.facts.filter((f) => f.subobject_id === sub.id).map((f) => f.employer));
        const orgOpen = state.expandedSubs.has(sub.id);
        appendRows(sub.name, [sub.id], 1, orgOpen, !isPlan && orgs.size ? () => {
          orgOpen ? state.expandedSubs.delete(sub.id) : state.expandedSubs.add(sub.id); renderCalendar(view);
        } : null);
        if (!isPlan && orgOpen) [...orgs].sort().filter((org) => !onlyWithActivity || data.dates.some((day) => {
          const fact = data.factsByKey.get(calendarKey(sub.id, day))?.orgs.get(org);
          return (fact?.day || 0) > 0 || (fact?.night || 0) > 0;
        })).forEach((org) => appendRows(org || "Организация не указана", [sub.id], 2, false, null, org));
      });
    });
    table.append(body);
    $("#" + view + "-table").replaceChildren(table);
  }
  async function savePlanCell(input, site, day) {
    if (readOnly) return;
    const key = calendarKey(site, day), data = state.calendars.plan;
    const old = data.plansByKey.get(key), text = input.value.trim();
    if (!/^\d*$/.test(text) || (text !== "" && Number(text) > 100000)) {
      input.classList.add("cell-error"); state.planErrors.set(key, true); status("#plan-status", "Введите целое число от 0 до 100000.", true); return;
    }
    const count = text === "" ? null : Number(text);
    if (count === (old?.planned_count ?? null) && !state.planErrors.has(key)) { state.planDrafts.delete(key); return; }
    state.pendingPlans++; input.readOnly = true; input.classList.add("cell-saving");
    status("#plan-status", "Сохранение…");
    try {
      const result = await api("/api/daily-plans", { method: "PUT", body: JSON.stringify({
        subobject_id: site, date: day, planned_count: count, expected_token: old?.edit_token ?? null
      }) });
      if (count === null) data.plansByKey.delete(key);
      else data.plansByKey.set(key, { subobject_id: site, work_date: day, planned_count: count, edit_token: result.edit_token });
      state.planErrors.delete(key); state.planDrafts.delete(key); input.classList.remove("cell-error");
      all("#plan-table [data-sum-ids]").forEach((cell) => {
        const value = aggregate(data, cell.dataset.sumIds.split(",").map(Number), cell.dataset.sumDate).planned;
        cell.textContent = value === null ? "—" : String(value);
      });
    } catch (error) {
      state.planErrors.set(key, true); input.classList.add("cell-error"); toast(error.message, true);
      if (error.status === 409) {
        // Show current stored value; keep the user's draft for a deliberate retry.
        const fresh = await api("/api/calendar?start=" + day + "&days=1").catch(() => null);
        if (fresh) {
          const current = fresh.plans.find((p) => p.subobject_id === site);
          current ? data.plansByKey.set(key, current) : data.plansByKey.delete(key);
          input.title = "Сейчас сохранено: " + (current?.planned_count ?? "не задано") + ". Повторите ввод, чтобы заменить.";
        }
      }
    } finally {
      state.pendingPlans--; input.readOnly = false; input.classList.remove("cell-saving");
      if (!state.pendingPlans) state.planWaiters.splice(0).forEach((resolve) => resolve());
      status("#plan-status", state.pendingPlans ? "Сохранение…" : state.planErrors.size ? "Есть несохранённые ячейки. Исправьте их и нажмите Enter." : "Все изменения сохранены", !!state.planErrors.size);
    }
  }
  ["plan", "dashboard"].forEach((view) => {
    $("#" + view + "-start")?.addEventListener("change", () => loadCalendar(view));
    $("#" + view + "-search")?.addEventListener("input", () => { if (!state.pendingPlans && !state.planErrors.size) renderCalendar(view); });
  });
  $("#dashboard-nonempty")?.addEventListener("change", (event) => {
    event.target.setAttribute("aria-checked", String(event.target.checked));
    renderCalendar("dashboard");
  });
  $("#dashboard-category")?.addEventListener("change", () => loadCalendar("dashboard"));
  $("#report-date").addEventListener("change", () => loadCalendar("dashboard"));
  all("[data-summary-mode]").forEach(button => button.addEventListener("click", () => {
    if (state.pendingPlans || state.planErrors.size || state.planDrafts.size) { toast("Завершите ввод в плане."); return; }
    const previousMode = state.summaryMode;
    state.summaryMode = button.dataset.summaryMode;
    const placement = ['placement', 'category'].includes(state.summaryMode);
    window.placementReport.setMode(state.summaryMode);
    $('#placement-report').hidden = !placement;
    $('#dashboard-calendar-panel').hidden = placement;
    if (placement) {
      state.calendars.dashboard = {...state.calendars.dashboard, requestId: (state.calendars.dashboard?.requestId || 0) + 1};
      all('[data-summary-mode]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      $('#view-dashboard .page-heading p').textContent = state.summaryMode === 'category' ? 'Расставленные и нерасставленные по категориям ГДТЛР на выбранную дату' : 'Расставленные, нерасставленные и неявки по ГДЛР и ППС';
      const selectedDate = $('#report-date').value;
      if (state.summaryMode === 'category' && previousMode === 'date' && selectedDate) $('#placement-report-date').value = selectedDate;
      status('#dashboard-status', '');
      window.placementReport.load();
      return;
    }
    const report = state.summaryMode === 'date';
    $("#view-dashboard .page-heading p").textContent = report ? 'Персонал по компаниям и сменам на отчётную дату' : 'План и факт по объектам за каждый день';
    all("[data-summary-mode]").forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    $("#dashboard-start").closest('.period-controls').hidden = report;
    $("#report-date-control").hidden = !report;
    $("#view-dashboard [data-display]").closest('.segmented').hidden = report;
    $("#dashboard-nonempty").closest('label').hidden = report;
    $("#dashboard-calendar-panel > .table-note").hidden = report;
    $("#dashboard-table").classList.toggle('report-date-view', report);
    $("#dashboard-table").replaceChildren();
    loadCalendar('dashboard');
  }));
  all("[data-week-step]").forEach((button) => button.addEventListener("click", () => {
    if (state.pendingPlans || state.planErrors.size) { toast("Завершите ввод в плане."); return; }
    const view = button.dataset.calendar;
    $("#" + view + "-start").value = nextDate($("#" + view + "-start").value, Number(button.dataset.weekStep));
    loadCalendar(view);
  }));
  all("[data-display]").forEach((button) => button.addEventListener("click", () => {
    state.display = button.dataset.display;
    all("[data-display]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    renderCalendar("dashboard");
  }));

  async function loadManagement() {
    const [users, access] = await Promise.all([api("/api/users"), api("/api/user-smu-access")]);
    state.users = users.rows;
    const accessByUser = new Map(access.rows.map(item => [item.user_id, item]));
    const scopeEditor = user => {
      const saved = accessByUser.get(user.id);
      if (user.role === 'hr_viewer') return E('p', {className: 'account-scope'}, 'Просмотр всех СМУ. Изменение данных недоступно.');
      if (user.role === 'viewer' || user.role === 'super_admin') return E('p', {className: 'account-scope'},
        user.role === 'viewer' ? 'Только просмотр — редактирование недоступно.' : 'Редактирование всех СМУ — супер-администратор.');
      if (role !== 'super_admin') {
        if (!saved.configured && user.role === 'foreman') return E('p', {className: 'account-scope'}, 'Доступ по назначенным бригадам.');
        if (saved.mode === 'all') return E('p', {className: 'account-scope'}, 'Редактирование всех СМУ.');
        if (!saved.departments.length) return E('p', {className: 'account-scope'}, 'Доступ к редактированию СМУ не назначен.');
        return E('details', {className: 'account-scope account-scope-readonly'},
          E('summary', {}, 'Доступ к СМУ: ' + saved.departments.length),
          E('ul', {}, ...saved.departments.map(name => E('li', {}, name))));
      }
      let mode = saved.mode;
      const selected = new Set(saved.departments);
      const departments = [...new Set([...access.departments.map(item => item.name), ...saved.departments])];
      const feedback = E('p', {className: 'save-status', role: 'status'});
      const choices = E('div', {className: 'account-smu-choices'}, ...departments.map(name => E('label', {},
        E('input', {type: 'checkbox', value: name, checked: selected.has(name), onchange: event => {
          if (event.target.checked) selected.add(name); else selected.delete(name);
          update();
        }}), E('span', {}, name))));
      const summary = E('summary');
      const list = E('details', {className: 'account-smu-list'}, summary, choices);
      const save = E('button', {type: 'button', className: 'secondary-button', disabled: true, onclick: async () => {
        editor.inert = true; feedback.textContent = 'Сохранение…'; feedback.classList.remove('error-text');
        try {
          await api('/api/users/' + user.id + '/smu-access', {method: 'PUT', body: JSON.stringify({
            mode, departments: mode === 'all' ? [] : [...selected], expected_token: saved.expected_token})});
          await loadManagement(); toast('Доступ к СМУ сохранён.');
        } catch (error) { feedback.textContent = error.message; feedback.classList.add('error-text'); }
        finally { editor.inert = false; }
      }}, 'Сохранить доступ');
      function update() {
        list.hidden = mode === 'all';
        summary.textContent = selected.size ? 'Выбрано СМУ: ' + selected.size : 'Выбрать СМУ — ни одного не отмечено';
        save.disabled = saved.configured && mode === saved.mode && (mode === 'all' ||
          (selected.size === saved.departments.length && saved.departments.every(name => selected.has(name))));
        feedback.textContent = mode === 'selected' && !selected.size ? 'Без выбранных СМУ редактирование недоступно.' : '';
      }
      const modes = E('div', {className: 'account-scope-modes'}, ...[
        ['all', 'Редактировать все СМУ'], ['selected', 'Редактировать конкретные СМУ']
      ].map(([value, label]) => E('label', {}, E('input', {type: 'radio', name: 'smu-mode-' + user.id,
        value, checked: mode === value, onchange: () => { mode = value; update(); }}), label)));
      const editor = E('fieldset', {className: 'account-scope', 'data-user-id': user.id},
        E('legend', {}, 'Доступ к расстановке'), modes, list,
        !saved.configured && user.role === 'foreman' ? E('p', {className: 'table-note'},
          'Сейчас действуют права по назначенным бригадам. Сохраните доступ, чтобы перейти к выбранным СМУ.') : null,
        feedback, save);
      update();
      return editor;
    };
    const labels = { super_admin: "Супер-администратор", admin: "Администратор", foreman: "Ответственный за расстановку", viewer: "Просмотр", hr_viewer: "Управление по работе с персоналом", rotation: "Перевахта", recruitment: "Комплектация" };
    const canManage = () => role === 'super_admin';
    const roleEditor = (user) => {
      if (!canManage(user)) return null;
      const select = E("select", {"aria-label": "Роль пользователя " + user.username},
        ...Object.entries(labels).filter(([value]) => role === 'super_admin' || value !== 'super_admin').map(([value, label]) => E("option", {value, selected: value === user.role}, label)));
      const save = E("button", {className: "secondary-button", disabled: true, onclick: async () => {
        save.disabled = true; select.disabled = true;
        try {
          await api("/api/users/" + user.id, {method: "PATCH", body: JSON.stringify({role: select.value, expected_role: user.role})});
          if (user.id === Number(root.dataset.userId)) { location.assign("/"); return; }
          await loadManagement(); toast("Роль пользователя изменена.");
        } catch (error) { select.value = user.role; toast(error.message, true); }
        finally { select.disabled = false; save.disabled = select.value === user.role; }
      }}, "Сохранить роль");
      select.addEventListener("change", () => { save.disabled = select.value === user.role; });
      return E("div", {className: "account-role-editor"}, E("label", {}, "Роль", select), save);
    };
    $("#account-list").replaceChildren(...state.users.map((user) => E("div", { className: "account-card" + (canManage(user) ? "" : " account-readonly") },
      E("span", {}, E("strong", {}, user.full_name), E("small", {}, user.username + " · " + labels[user.role] + (user.active ? "" : " · отключён")), E("small", {}, "Бригад: " + user.crew_count)),
      roleEditor(user),
      scopeEditor(user),
      E("div", { className: "account-actions", hidden: !canManage(user) },
        E("button", { className: "text-button", onclick: async () => {
          const name = prompt("ФИО пользователя (фамилия, имя, отчество):", user.full_name);
          if (name === null || name.trim() === user.full_name) return;
          try {
            await api("/api/users/" + user.id, {method: "PATCH", body: JSON.stringify({full_name: name, expected_full_name: user.full_name})});
            await loadManagement(); toast("ФИО сохранено. Оно будет показано в поле «Назначил».");
          } catch (error) { toast(error.message, true); }
        }}, "Изменить ФИО"),
        E("button", { className: "text-button", onclick: async () => {
          const password = prompt("Новый пароль (не менее 10 символов):"); if (!password) return;
          try { await api("/api/users/" + user.id, { method: "PATCH", body: JSON.stringify({ password }) }); toast("Пароль изменён."); }
          catch (error) { toast(error.message, true); }
        } }, "Пароль"),
        E("button", { className: "text-button", onclick: async () => {
          try { await api("/api/users/" + user.id, { method: "PATCH", body: JSON.stringify({ active: !user.active }) }); await loadManagement(); }
          catch (error) { toast(error.message, true); }
        } }, user.active ? "Отключить" : "Включить")))));
  }
  $("#account-form")?.addEventListener("submit", async (event) => {
    event.preventDefault(); const button = event.target.querySelector("button"); button.disabled = true;
    try {
      await api("/api/users", { method: "POST", body: JSON.stringify({ full_name: $("#account-name").value,
        username: $("#account-username").value, password: $("#account-password").value, role: $("#account-role").value }) });
      event.target.reset(); await loadManagement(); toast("Учётная запись создана. Настройте доступ к СМУ в её карточке.");
    } catch (error) { toast(error.message, true); } finally { button.disabled = false; }
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.busy || state.pendingPlans || state.planErrors.size || state.planDrafts.size) { event.preventDefault(); event.returnValue = ""; }
  });
  async function loadLocationReference() {
      const ref = await window.appReference.get();
      state.objects = ref.objects; state.subs = ref.subobjects;
      const objectNames = new Map(ref.objects.map((o) => [o.id, o.name]));
      state.sites = ref.subobjects.map((s) => ({ ...s, object: objectNames.get(s.object_id),
        search: normalized(objectNames.get(s.object_id) + " " + s.name) }));
  }
  async function init() {
    try {
      await switchView(location.hash.slice(1) || (role === "viewer" ? "dashboard" : "staffing"));
    } catch (error) { toast(error.message, true); status("#board-status", error.message, true); }
  }
  init();
})();
