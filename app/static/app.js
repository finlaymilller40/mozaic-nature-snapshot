"use strict";

// ------------------------------------------------------------- constants

const LEVELS = [
  { key: "A1", label: "A1", colour: "#2e9e44" },
  { key: "A2", label: "A2", colour: "#f0a202" },
  { key: "A3", label: "A3", colour: "#d7263d" },
  { key: "A4", label: "A4", colour: "#7b2cbf" },
  { key: "not_screened", label: "Not screened", colour: "#9e9e9e" },
];
const COLOUR = Object.fromEntries(LEVELS.map((l) => [l.key, l.colour]));
const RANK = { not_screened: 0, A1: 1, A2: 2, A3: 3, A4: 4 };

const WORK_TYPES = JSON.parse(document.getElementById("work-types").textContent);

const IRZ_LABELS = {
  irz_all_consultations: "all planning applications",
  irz_infrastructure: "infrastructure",
  irz_wind_solar: "wind and solar energy",
  irz_minerals_oil_gas: "minerals, oil and gas",
  irz_rural_non_residential: "rural non-residential development",
  irz_residential: "residential development",
  irz_rural_residential: "rural residential development",
  irz_air_pollution: "air pollution",
  irz_combustion: "combustion",
  irz_waste: "waste",
  irz_composting: "composting",
  irz_discharges: "discharges",
  irz_water_supply: "water supply",
};
const DESIGNATIONS = {
  sac: "a Special Area of Conservation",
  spa: "a Special Protection Area",
  ramsar: "a Ramsar wetland",
};
// What a closer look would focus on, per rule.
const ESCALATION_FOCUS = {
  1: "the protected European site",
  2: "the SSSI",
  3: "the nearby ancient woodland",
  4: "the risk zone's consultation requirement",
  6: "the risk-zone trigger",
  7: "the priority habitat",
};

const DATA_ATTRIBUTION =
  "Site data: © Natural England copyright, contains Ordnance Survey data © Crown copyright and database right 2026; " +
  "storm overflows © Environment Agency copyright and/or database right 2026";

// ------------------------------------------------------------------ state

const history = [];
let pendingIntake = null;
let clusters = null;

const $ = (id) => document.getElementById(id);

// -------------------------------------------------------------------- map

const map = L.map("map").setView([52.35, 0.05], 9);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
map.attributionControl.addAttribution(DATA_ATTRIBUTION);

// ------------------------------------------------------------------- chat

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("messages").appendChild(div);
  div.scrollIntoView({ block: "nearest" });
}

function setChatEnabled(enabled) {
  $("chat-input").disabled = !enabled;
  $("chat-form").querySelector("button").disabled = !enabled;
}

$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-input").value = "";
  history.push({ role: "user", content: text });
  addMessage("user", text);
  await askNext();
});

async function askNext() {
  setChatEnabled(false);
  $("status").textContent = "Thinking…";
  try {
    const response = await fetch("/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(history),
    });
    if (response.status === 429) {
      const detail = (await response.json().catch(() => ({}))).detail;
      $("status").textContent = "";
      showFallback(detail || "This session has reached its chat limit. Please use the form to finish.");
      return;
    }
    if (!response.ok) throw new Error(`The assistant returned an error (${response.status}).`);
    const turn = await response.json();
    $("status").textContent = "";
    if (turn.type === "confirm") {
      pendingIntake = turn.intake;
      $("summary-text").textContent = turn.summary;
      $("confirm-box").hidden = false;
      $("chat-form").hidden = true;
    } else {
      history.push({ role: "assistant", content: turn.text });
      addMessage("assistant", turn.text);
      setChatEnabled(true);
      $("chat-input").focus();
    }
  } catch (error) {
    $("status").textContent = "";
    showFallback(`${error.message} The chat assistant isn't available right now, so please answer these instead.`);
  }
}

$("confirm-btn").addEventListener("click", () => {
  $("confirm-box").hidden = true;
  runSnapshot(pendingIntake);
});

$("edit-btn").addEventListener("click", () => {
  const summary = $("summary-text").textContent;
  history.push({ role: "assistant", content: summary });
  addMessage("assistant", summary);
  $("confirm-box").hidden = true;
  $("chat-form").hidden = false;
  setChatEnabled(true);
  $("chat-input").placeholder = "What would you like to change?";
  $("chat-input").focus();
});

// --------------------------------------------------------------- fallback

const workTypeSelect = $("fallback").elements.work_type;
for (const [key, label] of Object.entries(WORK_TYPES)) {
  workTypeSelect.add(new Option(label, key));
}

function showFallback(note) {
  $("chat").hidden = true;
  $("fallback").hidden = false;
  $("fallback-note").textContent = note;
}

$("use-form").addEventListener("click", () =>
  showFallback("Answer these five questions. The answers apply to every site."),
);

$("fallback").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const intake = Object.fromEntries(form.entries());
  event.target.querySelector("button").disabled = true;
  runSnapshot(intake);
});

// --------------------------------------------------------------- snapshot

async function runSnapshot(intake) {
  try {
    $("status").textContent = "Loading the example sites…";
    const sites = await getJson("/sites/examples");
    $("status").textContent = `Screening ${sites.features.length} sites…`;
    const result = await getJson("/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intake, sites }),
    });
    $("status").textContent = "";
    showCounts(result.summary);
    showSites(result.features);
  } catch (error) {
    $("status").textContent = `Screening failed: ${error.message}`;
    const button = $("fallback").querySelector("button");
    if (button) button.disabled = false;
  }
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json();
}

function showCounts(summary) {
  const list = $("counts");
  list.replaceChildren();
  for (const level of LEVELS) {
    const count = summary.counts[level.key] ?? 0;
    if (level.key === "not_screened" && count === 0) continue;
    const li = document.createElement("li");
    li.append(dot(level.colour), level.label);
    const n = document.createElement("span");
    n.className = "count";
    n.textContent = count;
    li.append(n);
    list.append(li);
  }
  $("results").hidden = false;
}

function dot(colour) {
  const span = document.createElement("span");
  span.className = "dot";
  span.style.background = colour;
  return span;
}

function levelKey(props) {
  return props.level || "not_screened";
}

function showSites(features) {
  if (clusters) map.removeLayer(clusters);
  clusters = L.markerClusterGroup({
    showCoverageOnHover: false,
    iconCreateFunction: (cluster) => {
      const markers = cluster.getAllChildMarkers();
      const top = markers.reduce((a, m) => (RANK[m.options.level] > RANK[a] ? m.options.level : a), "not_screened");
      return L.divIcon({
        html: `<div style="background:${COLOUR[top]}">${markers.length}</div>`,
        className: "cluster",
        iconSize: L.point(36, 36),
      });
    },
  });

  for (const feature of features) {
    const props = feature.properties;
    const level = levelKey(props);
    const latlng = feature.geometry.type === "Point"
      ? L.latLng(feature.geometry.coordinates[1], feature.geometry.coordinates[0])
      : L.geoJSON(feature).getBounds().getCenter();
    const marker = L.circleMarker(latlng, {
      level,
      radius: 7,
      color: "#fff",
      weight: 1.5,
      fillColor: COLOUR[level],
      fillOpacity: 0.95,
    });
    marker.bindPopup(() => siteCard(props), { maxWidth: 320 });
    clusters.addLayer(marker);
  }
  map.addLayer(clusters);
  if (features.length) map.fitBounds(clusters.getBounds(), { padding: [20, 20] });
}

// -------------------------------------------------------------- site card

function siteCard(props) {
  const card = document.createElement("div");
  card.className = "site-card";

  const title = document.createElement("h3");
  title.textContent = props.name || props.id;
  card.append(title);

  const level = levelKey(props);
  const levelLine = document.createElement("p");
  levelLine.className = "level";
  levelLine.append(dot(COLOUR[level]), level === "not_screened" ? "Not screened" : `Level ${level}`);
  card.append(levelLine);

  if (level === "not_screened") {
    card.append(paragraph(props.detail || "This site couldn't be screened."));
    return card;
  }

  if (props.may_escalate_to) {
    const focus = [...new Set(
      props.reasons.filter((r) => r.level === props.level && ESCALATION_FOCUS[r.rule]).map((r) => ESCALATION_FOCUS[r.rule]),
    )];
    const why = focus.length ? ` after a closer look at ${joinWords(focus)}` : "";
    card.append(paragraph(`May escalate to ${props.may_escalate_to}${why}.`));
  }

  const list = document.createElement("ul");
  for (const text of plainReasons(props.reasons)) {
    const li = document.createElement("li");
    li.textContent = text;
    list.append(li);
  }
  card.append(list);

  const context = contextLines(props.context);
  if (context.length) {
    const box = document.createElement("div");
    box.className = "context";
    context.forEach((line) => box.append(paragraph(line)));
    card.append(box);
  }
  return card;
}

// Plain-English reasons, without lines another reason already covers.
function plainReasons(reasons) {
  const hasRule = (rule, test) => reasons.some((r) => r.rule === rule && test(r));
  const kept = reasons.filter((r) => {
    // "All applications" (1) already covers "all except householder" (2).
    if (r.rule === 4 && r.value.endsWith("=2")) return !hasRule(4, (o) => o.value.endsWith("=1"));
    // A named habitat already says the site overlaps priority habitat.
    if (r.rule === 7 && r.value === GENERIC_HABITAT) return !hasRule(7, (o) => o.value !== GENERIC_HABITAT);
    return true;
  });
  return [...new Set(kept.map(plainReason))];
}

const GENERIC_HABITAT = "No main habitat but additional habitats present";

function plainReason(r) {
  switch (r.rule) {
    case 1:
      return `The site is inside ${r.value}, ${DESIGNATIONS[r.layer] || "a protected European site"}.`;
    case 2:
      return `The site is inside ${r.value.replace(/ SSSI$/, "")}, a Site of Special Scientific Interest.`;
    case 3: {
      const distance = (r.detail.match(/([\d.]+) m away/) || [])[1];
      const name = /^(ASNW|ARW|PAWS|AWPP|IAWPP)$/.test(r.value) ? "Ancient woodland" : `Ancient woodland (${r.value})`;
      return `${name} is ${distance !== undefined ? `${distance} m` : "within 15 m"} from the site.`;
    }
    case 4:
      return r.value.endsWith("=1")
        ? "Natural England's risk zones ask to be consulted on all planning applications here."
        : "Natural England's risk zones ask to be consulted on all planning applications here except householder ones.";
    case 5:
      return "The work involves abstraction or dewatering in a risk zone it triggers. A groundwater specialist may be needed.";
    case 6: {
      const category = IRZ_LABELS[r.value.split("=")[0]] || "this kind of work";
      const source = (r.detail.match(/^Triggered for (.+?) \(/) || [])[1];
      return `The site is in an SSSI risk zone that flags ${category}${source ? ` (for ${source.toLowerCase()})` : ""}.`;
    }
    case 7:
      return r.value === GENERIC_HABITAT
        ? "The site overlaps priority habitat."
        : `The site overlaps priority habitat: ${r.value.toLowerCase()}.`;
    case 8:
      return "No protected sites, habitats or risk-zone triggers found. Confirm remotely.";
    case 10:
      return "A physical survey is needed, which raises A3 to A4.";
    default:
      return r.detail;
  }
}

function contextLines(context) {
  if (!context) return [];
  const lines = [];
  const unit = context.sssi_condition;
  if (unit) {
    const date = unit.condition_date ? `, assessed ${unit.condition_date}` : "";
    const where = unit.distance_m > 0 ? `${Math.round(unit.distance_m).toLocaleString()} m away` : "at the site";
    lines.push(`Nearest SSSI unit (${unit.sssi_name}, ${where}): ${unit.condition.toLowerCase()}${date}.`);
  }
  const linked = [...new Set((context.european_site_linkage || []).flatMap((z) => z.european_sites))];
  if (linked.length) lines.push(`A triggered risk zone also covers ${joinWords(linked)}.`);
  if (context.direction_of_effect) lines.push(`Direction of effect: ${context.direction_of_effect}.`);
  return lines;
}

function paragraph(text) {
  const p = document.createElement("p");
  p.textContent = text;
  return p;
}

function joinWords(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
