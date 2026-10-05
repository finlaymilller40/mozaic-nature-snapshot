"use strict";

// Nature Exposure Snapshot frontend. Two screens: describe the project in a
// chat, then see the results on a map. Sections:
//   1. constants, state and helpers     5. drawing and postcode pins
//   2. reading uploaded coordinates     6. the form
//   3. plain-English reasons            7. results screen
//   4. the chat

// ========================================= 1. constants, state and helpers

const CONFIG = JSON.parse(document.getElementById("config").textContent);
const WORK_TYPES = CONFIG.work_types;
const MEANINGS = CONFIG.level_meanings;
const GEOMETRY = CONFIG.site_geometry;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

const COLOUR = { A1: "#2e9e44", A2: "#f0a202", A3: "#d7263d", A4: "#7b2cbf", not_screened: "#9e9e9e" };
const RANK = { not_screened: 0, A1: 1, A2: 2, A3: 3, A4: 4 };
const SITE_COLOUR = "#1f6f5c";
const KIND_LABEL = { point: "Point", line: "Route", polygon: "Area" };
const SHAPE_WORDS = { point: "point", line: "route", polygon: "area" };
const GEOMAN_SHAPE = { point: "Marker", line: "Line", polygon: "Polygon" };

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
const GENERIC_HABITAT = "No main habitat but additional habitats present";

// British National Grid, for showing uploaded eastings/northings on a map.
// (The server does the accurate conversion used for screening.)
proj4.defs("EPSG:27700",
  "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy " +
  "+towgs84=446.448,-125.157,542.06,0.15,0.247,0.842,-20.489 +units=m +no_defs");

const DATA_ATTRIBUTION =
  "Site data: © Natural England copyright, contains Ordnance Survey data © Crown copyright and database right 2026";
const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// State
const history = [];         // chat messages sent to /intake
let knownAnswers = {};      // intake answers so far
let pendingIntake = null;   // intake from the chat summary, awaiting Confirm
let lastIntake = null;      // intake used for the latest results
const sites = [];           // see addSite()
let nextSiteNo = 1;

const $ = (id) => document.getElementById(id);

// Small DOM helper: el("p", {className: "x"}, "text", child, ...)
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) if (child != null) node.append(child);
  return node;
}

function dot(level) {
  const span = el("span", { className: "dot" });
  span.style.background = COLOUR[level] || level;
  return span;
}

function joinWords(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* not JSON */ }
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function shapeFor(workType) {
  return (WORK_TYPES[workType] && WORK_TYPES[workType].shape) || "point";
}

// ---- sites
// A site is { id, name, source, kind, layer, pending, postcode, geometry, crs }.
// Uploaded sites keep their exact geometry and crs; drawn sites and pins are
// read from their layer on the drawing map when screening.

function addSite(fields) {
  const site = { id: nextSiteNo++, pending: false, geometry: null, crs: null, layer: null, ...fields };
  sites.push(site);
  return site;
}

function removeSite(site) {
  const i = sites.indexOf(site);
  if (i !== -1) sites.splice(i, 1);
  if (site.layer && drawMap) drawMap.removeLayer(site.layer);
}

function confirmedSites() {
  return sites.filter((s) => !s.pending);
}

function siteFeature(site) {
  return {
    type: "Feature",
    id: String(site.id),
    crs: site.crs || "EPSG:4326",
    properties: { name: site.name, source: site.source },
    geometry: site.geometry || site.layer.toGeoJSON().geometry,
  };
}

// Display copy of an uploaded geometry (British National Grid -> lat/long).
function displayGeometry(geometry, crs) {
  if (crs !== "EPSG:27700") return geometry;
  const convert = (c) => (typeof c[0] === "number" ? proj4("EPSG:27700", "EPSG:4326", c) : c.map(convert));
  return { type: geometry.type, coordinates: convert(geometry.coordinates) };
}

function updateSitesLine() {
  const confirmed = confirmedSites();
  $("sites-line").hidden = confirmed.length === 0;
  if (!confirmed.length) return;
  const byKind = {};
  for (const s of confirmed) byKind[s.kind] = (byKind[s.kind] || 0) + 1;
  const parts = Object.entries(byKind).map(([kind, n]) => plural(n, SHAPE_WORDS[kind]));
  $("sites-text").textContent = `Added: ${joinWords(parts)}.`;
  updatePreview(confirmed);
}

// A small, still map in the chat showing the sites added so far.
let previewMap = null;
let previewLayer = null;

function updatePreview(confirmed) {
  if (!previewMap) {
    previewMap = L.map("sites-preview", {
      zoomControl: false, attributionControl: false, dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(previewMap);
    $("sites-preview").addEventListener("click", () => $("open-map").click());
  }
  previewMap.invalidateSize();
  if (previewLayer) previewMap.removeLayer(previewLayer);
  previewLayer = L.geoJSON({
    type: "FeatureCollection",
    features: confirmed.map((site) => ({
      type: "Feature", properties: {},
      geometry: site.geometry ? displayGeometry(site.geometry, site.crs) : site.layer.toGeoJSON().geometry,
    })),
  }, {
    style: { color: SITE_COLOUR, weight: 4, fillOpacity: 0.2 },
    pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 6, color: "#fff", weight: 2, fillColor: SITE_COLOUR, fillOpacity: 1 }),
  }).addTo(previewMap);
  previewMap.fitBounds(previewLayer.getBounds(), { padding: [20, 20], maxZoom: 15 });
}

// ============================================ 2. reading uploaded coordinates

const UK_LAT_LONG = { minX: -9, minY: 49, maxX: 3, maxY: 61 };
const BNG_RANGE = { minX: 0, minY: 0, maxX: 700000, maxY: 1300000 };
const GEOJSON_TYPES = ["Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"];
const COLUMN_NAMES = {
  name: ["name", "site", "site_name"],
  route: ["route", "route_id", "line"],
  easting: ["easting", "eastings", "e"],
  northing: ["northing", "northings", "n"],
  lat: ["lat", "latitude"],
  lon: ["lon", "lng", "long", "longitude"],
  grid_ref: ["grid_ref", "gridref", "grid_reference", "ngr", "os_grid_ref"],
};

$("add-pasted").addEventListener("click", () => importText($("paste").value, "the pasted text"));
$("upload-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > MAX_UPLOAD_BYTES) return uploadStatus(`${file.name} is over 5 MB.`, true);
  importText(await file.text(), file.name);
});

function uploadStatus(text, isError = false) {
  $("upload-status").textContent = text;
  $("upload-status").className = isError ? "small error" : "small";
}

function importText(text, source) {
  text = (text || "").trim();
  if (!text) return uploadStatus("Nothing to add yet: paste coordinates or choose a file.", true);
  let parsed;
  try {
    parsed = text.startsWith("{") ? parseGeoJson(JSON.parse(text)) : parseCsv(text);
  } catch (error) {
    return uploadStatus(`Couldn't read ${source}: ${error.message}`, true);
  }
  if (parsed.errors.length) {
    const shown = parsed.errors.slice(0, 5).join(" ");
    const more = parsed.errors.length > 5 ? ` (and ${parsed.errors.length - 5} more problems)` : "";
    return uploadStatus(`Nothing added from ${source}. ${shown}${more}`, true);
  }
  if (!parsed.sites.length) return uploadStatus(`No sites found in ${source}.`, true);
  if (sites.length + parsed.sites.length > GEOMETRY.max_sites) {
    return uploadStatus(`That would make more than ${GEOMETRY.max_sites} sites, the limit for one check.`, true);
  }
  for (const s of parsed.sites) {
    addSite({ name: s.name || `${KIND_LABEL[s.kind]} ${nextSiteNo}`, source: "uploaded", kind: s.kind, geometry: s.geometry, crs: s.crs });
  }
  uploadStatus(`Added ${plural(parsed.sites.length, "site")} from ${source}.`);
  $("paste").value = "";
  updateSitesLine();
  sitesChanged(`I've uploaded ${plural(parsed.sites.length, "site")}.`);
}

// ---- CSV

function parseCsv(text) {
  const rows = csvRows(text);
  if (rows.length < 2) throw new Error("it needs a header row and at least one row of coordinates.");
  const header = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, "_"));
  const col = Object.fromEntries(Object.entries(COLUMN_NAMES).map(([k, names]) => [k, header.findIndex((h) => names.includes(h))]));

  let mode;
  if (col.grid_ref >= 0) mode = "grid_ref";
  else if (col.easting >= 0 && col.northing >= 0) mode = "bng";
  else if (col.lat >= 0 && col.lon >= 0) mode = "lat_long";
  else throw new Error("the header needs easting and northing, lat and lon, or grid_ref columns.");

  const errors = [];
  const points = []; // { name, route, coords, crs }
  rows.slice(1).forEach((row, i) => {
    const line = i + 2;
    if (row.every((cell) => !cell.trim())) return;
    const cell = (k) => (col[k] >= 0 ? (row[col[k]] || "").trim() : "");
    let coords;
    let crs;
    if (mode === "grid_ref") {
      try { coords = gridRefToEastingNorthing(cell("grid_ref")); crs = "EPSG:27700"; }
      catch (error) { return errors.push(`Row ${line}: ${error.message}`); }
    } else if (mode === "bng") {
      coords = [Number(cell("easting")), Number(cell("northing"))];
      crs = "EPSG:27700";
      if (!coords.every(Number.isFinite)) return errors.push(`Row ${line}: easting and northing must be numbers.`);
      if (!inRange(coords, BNG_RANGE)) return errors.push(`Row ${line}: ${coords.join(", ")} isn't a British National Grid easting/northing.`);
    } else {
      coords = [Number(cell("lon")), Number(cell("lat"))];
      crs = "EPSG:4326";
      if (!coords.every(Number.isFinite)) return errors.push(`Row ${line}: lat and lon must be numbers.`);
      if (!inRange(coords, UK_LAT_LONG)) return errors.push(`Row ${line}: lat ${coords[1]}, lon ${coords[0]} isn't in Great Britain.`);
    }
    points.push({ name: cell("name"), route: cell("route"), coords, crs, line });
  });
  if (errors.length) return { sites: [], errors };

  // Rows sharing a route value become one line, in the order given.
  const sitesOut = [];
  const routes = new Map();
  for (const p of points) {
    if (!p.route) {
      sitesOut.push({ name: p.name, kind: "point", crs: p.crs, geometry: { type: "Point", coordinates: p.coords } });
    } else {
      if (!routes.has(p.route)) {
        routes.set(p.route, { name: p.name || p.route, kind: "line", crs: p.crs, geometry: { type: "LineString", coordinates: [] } });
        sitesOut.push(routes.get(p.route));
      }
      routes.get(p.route).geometry.coordinates.push(p.coords);
    }
  }
  for (const route of routes.values()) {
    if (route.geometry.coordinates.length < 2) errors.push(`Route "${route.name}" needs at least two rows.`);
  }
  return { sites: errors.length ? [] : sitesOut, errors };
}

function csvRows(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += ch;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

// ---- GeoJSON

function parseGeoJson(data) {
  const features = data.type === "FeatureCollection" ? data.features
    : data.type === "Feature" ? [data]
    : [{ type: "Feature", geometry: data, properties: {} }];
  if (!Array.isArray(features)) throw new Error("it isn't GeoJSON.");
  const declared = JSON.stringify(data.crs || "").includes("27700") ? "EPSG:27700" : null;

  const errors = [];
  const out = [];
  features.forEach((f, i) => {
    const geometry = f && f.geometry;
    const label = `Feature ${i + 1}`;
    if (!geometry || !GEOJSON_TYPES.includes(geometry.type)) {
      return errors.push(`${label}: must be a point, line or polygon.`);
    }
    const coords = flatCoords(geometry.coordinates);
    if (!coords.length || !coords.every((c) => Number.isFinite(c[0]) && Number.isFinite(c[1]))) {
      return errors.push(`${label}: has invalid coordinates.`);
    }
    let crs = declared;
    if (!crs) {
      if (coords.every((c) => inRange(c, UK_LAT_LONG))) crs = "EPSG:4326";
      else if (coords.every((c) => inRange(c, BNG_RANGE))) crs = "EPSG:27700";
      else return errors.push(`${label}: coordinates are neither lat/long in Great Britain nor British National Grid.`);
    }
    const props = f.properties || {};
    out.push({
      name: props.name || props.site_name || props.id || "",
      kind: geometry.type.includes("Point") ? "point" : geometry.type.includes("Line") ? "line" : "polygon",
      crs,
      geometry: { type: geometry.type, coordinates: geometry.coordinates },
    });
  });
  return { sites: errors.length ? [] : out, errors };
}

function flatCoords(coords) {
  if (!Array.isArray(coords)) return [];
  if (typeof coords[0] === "number") return [coords];
  return coords.flatMap(flatCoords);
}

function inRange([x, y], r) {
  return x >= r.minX && x <= r.maxX && y >= r.minY && y <= r.maxY;
}

// OS grid reference such as "TL2709096270" -> [easting, northing] in metres.
function gridRefToEastingNorthing(ref) {
  const letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"; // no I
  const match = ref.replace(/\s+/g, "").toUpperCase().match(/^([A-HJ-Z]{2})(\d*)$/);
  if (!match || match[2].length % 2) throw new Error(`"${ref}" isn't an OS grid reference.`);
  const [l1, l2] = [...match[1]].map((c) => letters.indexOf(c));
  const e100 = (((l1 - 2) % 5 + 5) % 5) * 5 + (l2 % 5);
  const n100 = 19 - Math.floor(l1 / 5) * 5 - Math.floor(l2 / 5);
  const half = match[2].length / 2;
  const scale = 10 ** (5 - half);
  const centre = half < 5 ? scale / 2 : 0; // a reference names a square; use its centre
  const e = Number(match[2].slice(0, half) || 0) * scale;
  const n = Number(match[2].slice(half) || 0) * scale;
  return [e100 * 100000 + e + centre, n100 * 100000 + n + centre];
}


// ================================================ 3. plain-English reasons

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
  return [...new Set(kept.map((r) => plainReason(r) + (r.where ? ` (${r.where})` : "")))];
}

function plainReason(r) {
  switch (r.rule) {
    case 1:
      return `Your work is inside ${r.value}, ${DESIGNATIONS[r.layer] || "a protected European site"}.`;
    case 2:
      return `Your work is inside ${r.value.replace(/ SSSI$/, "")}, a Site of Special Scientific Interest.`;
    case 3: {
      const distance = (r.detail.match(/([\d.]+) m away/) || [])[1];
      const name = /^(ASNW|ARW|PAWS|AWPP|IAWPP)$/.test(r.value) ? "Ancient woodland" : `Ancient woodland (${r.value})`;
      return `${name} is ${distance !== undefined ? `${distance} m` : "within 15 m"} from your work.`;
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
      return `Your work is in an SSSI risk zone that flags ${category}${source ? ` (for ${source.toLowerCase()})` : ""}.`;
    }
    case 7:
      return r.value === GENERIC_HABITAT
        ? "Your work overlaps priority habitat."
        : `Your work overlaps priority habitat: ${r.value.toLowerCase()}.`;
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


// One line for the side panel: what drives this part's level.
function shortReason(part) {
  if (part.level === "A1") return "Nothing protected found. A desk check is enough.";
  const bits = [];
  for (const r of part.reasons) {
    let text = null;
    if (r.rule === 1 || r.rule === 2) text = `inside ${r.value.replace(/ SSSI$/, "")}`;
    else if (r.rule === 3) text = "next to ancient woodland";
    else if (r.rule === 4) text = "risk zone: consult on all applications";
    else if (r.rule === 5) text = "abstraction or dewatering in a risk zone";
    else if (r.rule === 6 && !r.value.startsWith("irz_all_consultations")) text = `risk zone flags ${IRZ_LABELS[r.value.split("=")[0]]}`;
    else if (r.rule === 6) text = "risk zone covers work beyond the site";
    else if (r.rule === 7 && r.value !== GENERIC_HABITAT) text = `${r.value.toLowerCase()} habitat`;
    else if (r.rule === 7) text = "priority habitat";
    else if (r.rule === 10) text = "site survey needed";
    if (text && !bits.includes(text)) bits.push(text);
  }
  const shown = bits.slice(0, 2).join("; ");
  const text = bits.length > 2 ? `${shown}; +${bits.length - 2} more` : shown;
  return text.charAt(0).toUpperCase() + text.slice(1) + ".";
}

// What a protected feature is, for its explanation box on the map.
function featureWhat(f) {
  switch (f.kind) {
    case "sssi":
      return "A Site of Special Scientific Interest: land protected by law for its wildlife, habitats or geology.";
    case "sac":
      return "A Special Area of Conservation: protected under the Habitats Regulations for rare habitats and species.";
    case "spa":
      return "A Special Protection Area: protected under the Habitats Regulations for rare and migrating birds.";
    case "ramsar":
      return "A Ramsar site: a wetland of international importance.";
    case "ancient_woodland":
      return `Ancient woodland${f.status ? ` (${f.status})` : ""}: land wooded since at least 1600, which can't be replaced. ` +
        `Natural England and Forestry Commission standing advice asks for a buffer of at least ${CONFIG.ancient_woodland_buffer_m} m.`;
    case "priority_habitat":
      return "Priority habitat: one of the habitats of principal importance for nature in England.";
    case "risk_zone": {
      const linked = (f.european_sites || []).length ? ` It also covers ${joinWords(f.european_sites)}.` : "";
      return "An SSSI Impact Risk Zone: an area Natural England has drawn around nearby SSSIs, where certain kinds of " +
        `work could harm them.${linked}`;
    }
    default:
      return "";
  }
}

function featureTitle(f) {
  if (f.kind === "priority_habitat") return f.name === GENERIC_HABITAT ? "Priority habitat" : f.name;
  if (f.kind === "ancient_woodland") return f.name || "Ancient woodland";
  if (f.kind === "risk_zone") return "SSSI Impact Risk Zone";
  return f.name;
}

// ========================================================== 4. the chat

let mode = "chat"; // "chat" or "form"

function usingChat() {
  return mode === "chat";
}

function addMessage(role, text) {
  const div = el("div", { className: `msg ${role}`, textContent: text });
  $("messages").append(div);
  div.scrollIntoView({ block: "nearest" });
}

function say(text) {
  // An assistant message added by the page; kept in the history so the AI sees it.
  history.push({ role: "assistant", content: text });
  addMessage("assistant", text);
}

function setChatEnabled(enabled) {
  $("chat-input").disabled = !enabled;
  $("chat-form").querySelector("button").disabled = !enabled;
}

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-input").value = "";
  sendUserMessage(text);
});

function sendUserMessage(text) {
  history.push({ role: "user", content: text });
  addMessage("user", text);
  return askNext();
}

function allAnswersKnown() {
  return ["work_type", "beyond_site", "abstraction", "dewatering", "physical_survey_required"].every((k) => knownAnswers[k]);
}

async function askNext() {
  setChatEnabled(false);
  $("status").textContent = "Thinking…";
  try {
    const response = await fetch("/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history, sites_placed: confirmedSites().length }),
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
    applyAnswers(turn.type === "confirm" ? turn.intake : turn.answers);

    if (turn.type === "confirm") {
      pendingIntake = turn.intake;
      $("summary-text").textContent = turn.summary;
      $("confirm-card").hidden = false;
      $("location-card").hidden = true;
      $("upload-card").hidden = true;
      $("chat-form").hidden = true;
      return;
    }
    history.push({ role: "assistant", content: turn.text });
    addMessage("assistant", turn.text);
    if (turn.example) $("chat-input").placeholder = turn.example; // an example answer to this question
    setChatEnabled(true);
    $("chat-input").focus();

    // The location step: show the right tool.
    if (turn.postcodes && turn.postcodes.length) placePostcodes(turn.postcodes);
    else if (turn.districts && turn.districts.length) zoomToDistrict(turn.districts);
    else if (turn.location_method && !confirmedSites().length) chooseLocation(turn.location_method, { announce: false });
    else if (allAnswersKnown() && !confirmedSites().length) $("location-card").hidden = false;
  } catch (error) {
    $("status").textContent = "";
    showFallback(`${error.message} The chat assistant isn't available right now, so please answer these instead.`);
  }
}

function applyAnswers(answers) {
  if (!answers) return;
  for (const [key, value] of Object.entries(answers)) if (value) knownAnswers[key] = value;
}

// ---- choosing how to give the location

for (const button of document.querySelectorAll("#location-card .choice")) {
  button.addEventListener("click", () => chooseLocation(button.dataset.method, { announce: true }));
}

function chooseLocation(method, { announce }) {
  if (usingChat()) $("location-card").hidden = true;
  if (method === "coordinates") {
    $("upload-card").hidden = false;
    $("paste").focus();
  } else if (method === "postcode") {
    if (announce && usingChat()) say("What's the postcode for each site? Type them below.");
    if (!usingChat()) promptPostcodeInForm();
    $("chat-input").focus();
  } else if (method === "draw") {
    openDrawModal({ shape: shapeFor(knownAnswers.work_type) });
  }
}

// Called when sites are added or confirmed, so the chat can carry on.
function sitesChanged(message) {
  updateSitesLine();
  $("upload-card").hidden = true;
  $("postcode-box")?.remove();
  if (usingChat()) $("location-card").hidden = true;
  if (usingChat() && history.length && !pendingIntake) sendUserMessage(message);
}

$("open-map").addEventListener("click", () => openDrawModal({ shape: shapeFor(knownAnswers.work_type), startDrawing: false }));

$("confirm-btn").addEventListener("click", () => runSnapshot(pendingIntake));

$("edit-btn").addEventListener("click", () => {
  const summary = $("summary-text").textContent;
  history.push({ role: "assistant", content: summary });
  addMessage("assistant", summary);
  pendingIntake = null;
  $("confirm-card").hidden = true;
  $("chat-form").hidden = false;
  setChatEnabled(true);
  $("chat-input").placeholder = "What would you like to change?";
  $("chat-input").focus();
});

// ============================================== 5. drawing and postcode pins

let drawMap = null;
let drawShape = "point";
let drawSession = null; // { mode, added: [sites], pins: [sites] } while the pop-up is open

function ensureDrawMap() {
  if (drawMap) return;
  drawMap = L.map("draw-map");
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(drawMap);
  const aoi = L.geoJSON(CONFIG.aoi, {
    pmIgnore: true, interactive: false,
    style: { color: "#555", weight: 1.5, dashArray: "6 4", fill: false },
  }).addTo(drawMap);
  drawMap.fitBounds(aoi.getBounds());
  drawMap.pm.setPathOptions({ color: SITE_COLOUR, weight: 4 });

  drawMap.on("pm:create", (e) => {
    const kind = e.shape === "Marker" ? "point" : e.shape === "Line" ? "line" : "polygon";
    const n = sites.filter((s) => s.kind === kind).length + 1;
    const site = addSite({ name: `${KIND_LABEL[kind]} ${n}`, source: "drawn", kind, layer: e.layer });
    if (drawSession) drawSession.added.push(site);
    updateDrawCount();
  });
  drawMap.on("pm:remove", (e) => {
    const site = sites.find((s) => s.layer === e.layer);
    if (site) sites.splice(sites.indexOf(site), 1);
    updateDrawCount();
  });
}

function setDrawControls(allShapes) {
  drawMap.pm.removeControls();
  drawMap.pm.addControls({
    position: "topleft",
    drawMarker: allShapes || drawShape === "point",
    drawPolyline: allShapes || drawShape === "line",
    drawPolygon: allShapes || drawShape === "polygon",
    drawRectangle: allShapes || drawShape === "polygon",
    drawCircle: false, drawCircleMarker: false, drawText: false,
    cutPolygon: false, rotateMode: false,
    editMode: true, dragMode: true, removalMode: true,
  });
  $("other-shape").hidden = allShapes;
}

$("other-shape").addEventListener("click", () => {
  setDrawControls(true);
  $("draw-hint").textContent = "Choose a tool on the left: a point, a route, or an area.";
});

const DRAW_HINTS = {
  point: "Click the map where the work is.",
  line: "Click along the route from the start (A) to the end (B). Click the last point again to finish.",
  polygon: "Click around the edge of the site. Click the first point again to finish.",
};

function openDrawModal({ shape, mode = "draw", focus = null, startDrawing = true }) {
  $("draw-modal").hidden = false;
  ensureDrawMap();
  drawMap.invalidateSize();
  drawShape = shape;
  drawSession = { mode, added: [], pins: [] };
  setDrawControls(false);

  // Show sites added in other ways (uploads), for context.
  for (const site of sites) {
    if (!site.layer && site.geometry) {
      site.layer = L.geoJSON(displayGeometry(site.geometry, site.crs), {
        pmIgnore: true,
        style: { color: SITE_COLOUR, weight: 4, fillOpacity: 0.15 },
        pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 7, color: "#fff", weight: 2, fillColor: SITE_COLOUR, fillOpacity: 1, pmIgnore: true }),
      }).bindTooltip(site.name);
    }
    if (site.layer && !drawMap.hasLayer(site.layer)) site.layer.addTo(drawMap);
  }

  if (mode === "pins") {
    $("draw-title").textContent = "Check the postcode location";
    $("draw-hint").textContent = "The pin marks the centre of the postcode. Drag it to your exact site, then press Done.";
  } else {
    $("draw-title").textContent = `Draw your ${SHAPE_WORDS[shape]}`;
    $("draw-hint").textContent = DRAW_HINTS[shape];
  }

  const layers = sites.filter((s) => s.layer).map((s) => s.layer);
  if (focus) drawMap.setView(focus.latlng, focus.zoom);
  else if (layers.length) drawMap.fitBounds(L.featureGroup(layers).getBounds(), { padding: [40, 40], maxZoom: 16 });
  if (mode === "draw" && startDrawing && !confirmedSites().length) drawMap.pm.enableDraw(GEOMAN_SHAPE[shape]);
  updateDrawCount();
}

function updateDrawCount() {
  const drawn = sites.filter((s) => s.source === "drawn").length;
  $("draw-count").textContent = drawn ? `${plural(drawn, "shape")} drawn` : "";
}

function closeDrawModal() {
  drawMap.pm.disableDraw();
  $("draw-modal").hidden = true;
  drawSession = null;
}

$("draw-cancel").addEventListener("click", () => {
  // Undo anything added while the pop-up was open.
  for (const site of [...drawSession.added, ...drawSession.pins]) removeSite(site);
  closeDrawModal();
  updateSitesLine();
});

// A line or area isn't a shape until it's finished (clicking the last point
// again). If the user presses Done mid-drawing, finish it for them.
const MIN_VERTICES = { Line: 2, Polygon: 3 };

function finishOpenShape() {
  const shape = drawMap.pm.Draw.getActiveShape();
  const draw = shape && drawMap.pm.Draw[shape];
  if (!draw || !MIN_VERTICES[shape] || !draw._layer || typeof draw._finishShape !== "function") return;
  const vertices = draw._layer.getLatLngs().flat(Infinity).length;
  if (vertices >= MIN_VERTICES[shape]) draw._finishShape();
}

$("draw-done").addEventListener("click", () => {
  finishOpenShape();
  const { added, pins, mode } = drawSession;
  if (mode === "draw" && !added.length && !confirmedSites().length) {
    $("draw-hint").textContent = `Nothing drawn yet. ${DRAW_HINTS[drawShape]}`;
    $("draw-hint").classList.add("error-text");
    return;
  }
  $("draw-hint").classList.remove("error-text");
  for (const pin of pins) pin.pending = false;
  closeDrawModal();
  if (pins.length) {
    sitesChanged(`I've checked the location for ${joinWords(pins.map((p) => p.postcode))} on the map.`);
  } else if (added.length) {
    const counts = {};
    for (const s of added) counts[s.kind] = (counts[s.kind] || 0) + 1;
    sitesChanged(`I've drawn ${joinWords(Object.entries(counts).map(([k, n]) => plural(n, SHAPE_WORDS[k])))} on the map.`);
  } else {
    updateSitesLine();
  }
});

// ---- postcodes: the AI passes on postcodes the user typed; /geocode looks them up.

async function placePostcodes(postcodes) {
  const found = [];
  for (const postcode of postcodes) {
    if (sites.some((s) => s.postcode === postcode)) continue;
    try {
      const place = await getJson(`/geocode?postcode=${encodeURIComponent(postcode)}`);
      found.push(place);
      if (!place.in_area) addMessage("error", `${place.postcode} is outside the area we can check, so a site there can't be screened.`);
    } catch (error) {
      addMessage("error", `I couldn't find ${postcode}: ${error.message}`);
    }
  }
  if (!found.length) return;
  openDrawModal({ shape: "point", mode: "pins", startDrawing: false });
  for (const place of found) {
    const marker = L.marker([place.latitude, place.longitude], { draggable: true, pmIgnore: true, title: place.postcode })
      .bindTooltip(place.postcode, { permanent: true, direction: "top", offset: [0, -30] })
      .addTo(drawMap);
    const site = addSite({ name: place.postcode, source: "postcode", kind: "point", layer: marker, pending: true, postcode: place.postcode });
    drawSession.pins.push(site);
  }
  drawMap.fitBounds(L.featureGroup(drawSession.pins.map((p) => p.layer)).getBounds(), { maxZoom: 16, padding: [60, 60] });
}

// A postcode district (e.g. CB7) only zooms the map, for the user to draw the site.
async function zoomToDistrict(districts) {
  for (const district of districts) {
    try {
      const place = await getJson(`/geocode?postcode=${encodeURIComponent(district)}`);
      openDrawModal({ shape: shapeFor(knownAnswers.work_type), focus: { latlng: [place.latitude, place.longitude], zoom: 13 } });
      return;
    } catch (_) { /* try the next one */ }
  }
}

// ============================================================= 6. the form

const form = $("project-form");
for (const [key, spec] of Object.entries(WORK_TYPES)) form.elements.work_type.add(new Option(spec.label, key));

// The chat and the form are two separate ways to describe the project. The
// location tools are shared, and move into whichever one is in use.
function setMode(next, note = null) {
  mode = next;
  const chat = next === "chat";
  $("chat-card").hidden = !chat;
  form.hidden = chat;
  for (const [id, on] of [["mode-chat", chat], ["mode-form", !chat]]) {
    $(id).classList.toggle("active", on);
    $(id).setAttribute("aria-selected", String(on));
  }
  $(chat ? "chat-location-slot" : "form-location-slot").append($("location-block"));
  $("form-note").hidden = !note;
  $("form-note").textContent = note || "";
  $("status").textContent = "";

  if (chat) {
    $("location-card").hidden = !(allAnswersKnown() && !confirmedSites().length && !pendingIntake);
  } else {
    // Carry over anything the chat already found out.
    for (const [key, value] of Object.entries(knownAnswers)) {
      if (value && form.elements[key]) form.elements[key].value = value;
    }
    $("location-card").hidden = false;
  }
}

function showFallback(note) {
  setMode("form", note);
}

$("mode-chat").addEventListener("click", () => setMode("chat"));
$("mode-form").addEventListener("click", () => setMode("form"));

form.addEventListener("change", (event) => {
  if (event.target.name) knownAnswers[event.target.name] = event.target.value || null;
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!confirmedSites().length) {
    $("status").textContent = "Show where the work is first, in step 2.";
    return;
  }
  runSnapshot(Object.fromEntries(new FormData(form).entries()));
});

// Without the chat, postcodes are typed into a small box instead.
function promptPostcodeInForm() {
  if ($("postcode-box")) return;
  const input = el("input", { type: "text", placeholder: "e.g. CB7 4DL", ariaLabel: "Postcode" });
  const box = el("form", { id: "postcode-box", className: "inline-card" },
    el("p", { className: "card-title", textContent: "Postcode of the site" }),
    el("div", { className: "button-row" }, input, el("button", { type: "submit", textContent: "Find" })),
  );
  input.style.flex = "1";
  box.addEventListener("submit", (event) => {
    event.preventDefault();
    const codes = input.value.split(/[,;]+/).map((c) => c.trim().toUpperCase()).filter(Boolean);
    if (codes.length) placePostcodes(codes);
  });
  $("location-card").after(box);
  input.focus();
}

// ===================================================== 7. results screen

const FEATURE_STYLE = {
  risk_zone: { color: "#b26b00", weight: 1.5, dashArray: "5 5", fillColor: "#f0a202", fillOpacity: 0.04 },
  sac: { color: "#6a1b9a", weight: 1, fillColor: "#6a1b9a", fillOpacity: 0.12 },
  spa: { color: "#6a1b9a", weight: 1, fillColor: "#6a1b9a", fillOpacity: 0.12 },
  ramsar: { color: "#6a1b9a", weight: 1, fillColor: "#6a1b9a", fillOpacity: 0.12 },
  sssi: { color: "#2e7d32", weight: 1, fillColor: "#2e7d32", fillOpacity: 0.16 },
  ancient_woodland: { color: "#1b5e20", weight: 1, fillColor: "#1b5e20", fillOpacity: 0.25 },
  priority_habitat: { color: "#00897b", weight: 1, fillColor: "#00897b", fillOpacity: 0.16 },
};
const FEATURE_LABEL = {
  risk_zone: "SSSI Impact Risk Zone", sac: "Special Area of Conservation", spa: "Special Protection Area",
  ramsar: "Ramsar site", sssi: "Site of Special Scientific Interest", ancient_woodland: "Ancient woodland",
  priority_habitat: "Priority habitat",
};
const LEGEND_GROUPS = [
  { kinds: ["sssi"], label: "SSSI" },
  { kinds: ["sac", "spa", "ramsar"], label: "SAC, SPA or Ramsar" },
  { kinds: ["ancient_woodland"], label: "Ancient woodland" },
  { kinds: ["priority_habitat"], label: "Priority habitat" },
  { kinds: ["risk_zone"], label: "Risk zone that triggered" },
];

let resultsMap = null;
let resultsLayer = null;
let legendControl = null;
let lastResult = null;
let active = null;          // the part currently highlighted
let featureLayers = new Map(); // ref -> { layer, feature }
let partsByRef = new Map();    // "S1.P2" -> the part's map entry
let lastRequest = null;        // the screening request, re-sent to /report and /ask

async function runSnapshot(intake) {
  if (!confirmedSites().length) {
    $("status").textContent = "Show where the work is first.";
    $("location-card").hidden = false;
    return;
  }
  const buttons = [$("confirm-btn"), form.querySelector("button[type=submit]")];
  buttons.forEach((b) => { b.disabled = true; });
  $("status").textContent = "Checking your project…";
  try {
    const request = { intake, sites: { type: "FeatureCollection", features: confirmedSites().map(siteFeature) } };
    const result = await getJson("/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    lastIntake = intake;
    lastRequest = request;
    $("status").textContent = "";
    showResults(result);
  } catch (error) {
    $("status").textContent = `The check failed: ${error.message}`;
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

function ensureResultsMap() {
  if (resultsMap) return;
  resultsMap = L.map("map");
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(resultsMap);
  resultsMap.attributionControl.addAttribution(DATA_ATTRIBUTION);
}

function badge(level) {
  const span = el("span", { className: "badge", textContent: level === "not_screened" ? "–" : level });
  span.style.background = COLOUR[level];
  return span;
}

function showResults(result) {
  lastResult = result;
  $("intake-screen").hidden = true;
  $("results-screen").hidden = false;
  window.scrollTo(0, 0);
  ensureResultsMap();
  resultsMap.invalidateSize();
  if (resultsLayer) resultsMap.removeLayer(resultsLayer);
  resultsLayer = L.featureGroup().addTo(resultsMap);
  featureLayers = new Map();
  active = null;

  // Protected areas and risk zones the work touches, drawn dimly underneath.
  const kindsShown = new Set();
  for (const site of result.features) {
    for (const f of site.properties.features || []) {
      if (featureLayers.has(f.ref)) continue;
      const layer = L.geoJSON(f.geometry, { style: FEATURE_STYLE[f.kind] })
        .bindPopup(() => featureCard(f), { maxWidth: 340 })
        .addTo(resultsLayer);
      featureLayers.set(f.ref, { layer, feature: f });
      kindsShown.add(f.kind);
    }
  }

  // The work itself, part by part, with a matching row in the side panel.
  const work = L.featureGroup().addTo(resultsLayer);
  const panel = $("site-results");
  panel.replaceChildren();
  partsByRef = new Map();
  for (const [siteIndex, site] of result.features.entries()) {
    const props = site.properties;
    const level = props.level || "not_screened";
    const block = el("div", { className: "site-block" },
      el("h3", {}, badge(level), props.name || `Site ${props.id}`));
    panel.append(block);

    if (!props.level) {
      block.append(el("p", { className: "small muted", textContent: props.detail }));
      L.geoJSON(site.geometry, { style: { color: COLOUR.not_screened, weight: 4, dashArray: "6 6" } }).addTo(work);
      continue;
    }
    const list = el("ul", { className: "part-list" });
    for (const [partIndex, part] of props.parts.entries()) {
      const entry = drawPart(props, part, work);
      entry.ref = `S${siteIndex + 1}.P${partIndex + 1}`;
      partsByRef.set(entry.ref, entry);
      entry.row = el("li", { className: "part-row" },
        dot(part.level),
        el("span", { className: "where", textContent: partLabel(props, part) }),
        el("span", { className: "lvl", textContent: part.level }),
        el("span", { className: "why", textContent: shortReason(part) }));
      entry.row.addEventListener("click", () => activate(entry, true));
      entry.main.on("click", () => activate(entry, false));
      list.append(entry.row);
    }
    block.append(list);

    if (props.outside) {
      L.geoJSON(props.outside.geometry, {
        style: { color: COLOUR.not_screened, weight: props.kind === "line" ? 6 : 1.5, dashArray: "6 6", fillOpacity: 0.12 },
      }).bindTooltip(props.outside.warning, { sticky: true }).addTo(work);
    }
    if (props.kind === "line") addEndMarkers(site.geometry, work);
    if (props.kind === "point") {
      L.geoJSON(site.geometry, {
        pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 4, color: "#1f2933", fillColor: "#1f2933", fillOpacity: 1, interactive: false }),
      }).addTo(work);
    }
  }

  showOverall(result);
  showA1Note(result);
  showWarnings(result);
  showLevelKey();
  showLegend(kindsShown);
  const bounds = work.getBounds();
  if (bounds.isValid()) resultsMap.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
  showTab("sections");
  resetAsk();
  loadReport();
}

function drawPart(props, part, group) {
  const colour = COLOUR[part.level];
  if (props.kind === "line") {
    const casing = L.geoJSON(part.geometry, { interactive: false, style: { color: "#fff", weight: 13, opacity: 0.95 } }).addTo(group);
    const main = L.geoJSON(part.geometry, { style: { color: colour, weight: 7, opacity: 1 } })
      .bindPopup(() => partCard(props, part), { maxWidth: 340 }).addTo(group);
    return { main, casing, part, props, base: { weight: 7 }, focus: { weight: 10 } };
  }
  const main = L.geoJSON(part.geometry, { style: { color: colour, weight: 2, fillColor: colour, fillOpacity: 0.45 } })
    .bindPopup(() => partCard(props, part), { maxWidth: 340 }).addTo(group);
  return { main, part, props, base: { weight: 2, fillOpacity: 0.45 }, focus: { weight: 4, fillOpacity: 0.7 } };
}

function addEndMarkers(geometry, group) {
  const lines = geometry.type === "LineString" ? [geometry.coordinates] : geometry.coordinates;
  const first = lines[0][0];
  const lastLine = lines[lines.length - 1];
  const last = lastLine[lastLine.length - 1];
  for (const [label, [lon, lat]] of [["A", first], ["B", last]]) {
    L.marker([lat, lon], {
      interactive: false,
      icon: L.divIcon({ className: "ab-marker", html: label, iconSize: [24, 24] }),
    }).addTo(group);
  }
}

// Highlight a part on the map, its row in the panel, and the features behind it.
function activate(entry, fromPanel) {
  if (active) {
    active.main.setStyle(active.base);
    if (active.casing) active.casing.setStyle({ weight: 13 });
    active.row.classList.remove("active");
  }
  for (const { layer, feature } of featureLayers.values()) layer.setStyle(FEATURE_STYLE[feature.kind]);

  active = entry;
  entry.row.classList.add("active");
  entry.main.setStyle(entry.focus);
  if (entry.casing) { entry.casing.setStyle({ weight: 17 }); entry.casing.bringToFront(); }
  entry.main.bringToFront();
  const refs = new Set(entry.part.reasons.flatMap((r) => r.refs || []));
  for (const ref of refs) {
    const found = featureLayers.get(ref);
    if (!found) continue;
    const base = FEATURE_STYLE[found.feature.kind];
    found.layer.setStyle({ weight: 2.5, fillOpacity: Math.min(base.fillOpacity * 2.5, 0.4) });
  }
  if (fromPanel) {
    const bounds = entry.main.getBounds();
    resultsMap.fitBounds(bounds, { padding: [80, 80], maxZoom: 16 });
    entry.main.openPopup(bounds.getCenter());
  } else {
    entry.row.scrollIntoView({ block: "nearest" });
  }
}

function partLabel(props, part) {
  if (props.kind === "line") return `${part.start_km.toFixed(2)}–${part.end_km.toFixed(2)} km`;
  if (props.parts.length === 1) return props.kind === "point" ? `Whole ${props.buffer_m} m area` : "Whole site";
  const size = part.area_ha >= 0.1 ? `${part.area_ha.toFixed(1)} ha` : `${Math.round(part.area_ha * 10000)} m²`;
  return `${size} (${Math.max(1, Math.round(part.share * 100))}%)`;
}

// ---- the explanation boxes

function partCard(props, part) {
  const card = el("div", { className: "card" },
    el("h3", { textContent: props.name || `Site ${props.id}` }),
    el("p", { className: "sub", textContent: props.kind === "line"
      ? `${partLabel(props, part)} along a ${props.length_km.toFixed(2)} km route (${props.corridor_m} m either side)`
      : partLabel(props, part) }),
    el("p", { className: "lvl-line" }, dot(part.level), `Level ${part.level}`),
    el("p", { className: "meaning", textContent: MEANINGS[part.level] }));
  if (part.may_escalate_to) {
    card.append(el("p", { className: "small", textContent: `May rise to ${part.may_escalate_to} if a closer look confirms an effect.` }));
  }
  card.append(el("ul", {}, ...plainReasons(part.reasons).map((t) => el("li", { textContent: t }))));
  if (props.source === "postcode") {
    card.append(el("p", { className: "sub", textContent: "Located from a postcode, so only as accurate as the pin." }));
  }
  if (props.outside) card.append(el("p", { className: "warning", textContent: props.outside.warning }));
  const context = contextLines(part.context);
  if (context.length) card.append(el("div", { className: "context" }, ...context.map((t) => el("p", { textContent: t }))));
  return card;
}

function featureCard(f) {
  // Every reason, across all sites, that this feature is behind.
  const why = new Set();
  const where = [];
  for (const site of lastResult.features) {
    for (const part of site.properties.parts || []) {
      const hits = part.reasons.filter((r) => (r.refs || []).includes(f.ref));
      if (!hits.length) continue;
      hits.forEach((r) => why.add(plainReason(r)));
      where.push(`${site.properties.name || "Site"}: ${partLabel(site.properties, part)}`);
    }
  }
  const card = el("div", { className: "card" },
    el("h3", { textContent: featureTitle(f) }),
    el("p", { className: "sub", textContent: f.kind === "risk_zone" ? "Drawn by Natural England" : FEATURE_LABEL[f.kind] + (f.code ? ` (${f.code})` : "") }),
    el("p", { textContent: featureWhat(f) }),
    el("p", { className: "small", textContent: "Why it matters for your project:" }),
    el("ul", {}, ...[...why].map((t) => el("li", { textContent: t }))),
    el("p", { className: "lvl-line" }, dot(f.level), `So these parts are ${f.level}`),
    el("p", { className: "meaning", textContent: MEANINGS[f.level] }));
  if (where.length) card.append(el("p", { className: "sub", textContent: `Where: ${where.slice(0, 4).join("; ")}${where.length > 4 ? "…" : ""}` }));
  return card;
}

// ---- the side panel

function showOverall(result) {
  const screened = result.features.map((f) => f.properties.level).filter(Boolean);
  const level = screened.length ? screened.reduce((a, b) => (RANK[b] > RANK[a] ? b : a)) : "not_screened";
  const workType = WORK_TYPES[lastIntake.work_type];
  $("overall").replaceChildren(
    el("div", { className: "overall-level" }, badge(level),
      el("h2", { textContent: level === "not_screened" ? "Not screened" : `Overall level ${level}` })),
    el("p", { className: "overall-meaning", textContent:
      `${workType ? workType.label : ""}. ${level === "not_screened" ? "None of your sites are in the area we can check." : MEANINGS[level]}` }));
}

function showA1Note(result) {
  const hasA1 = result.features.some((f) => (f.properties.parts || []).some((p) => p.level === "A1"));
  $("a1-note")?.remove();
  if (!hasA1) return;
  $("site-results").append(el("p", { id: "a1-note", className: "a1-note", textContent:
    "Why A1? Where your work is A1, we found no protected sites, no ancient woodland within " +
    `${CONFIG.ancient_woodland_buffer_m} m, no priority habitat and no Natural England risk zone that flags this kind of work. ` +
    "A desk check to confirm is enough." }));
}

function showWarnings(result) {
  const list = $("warnings");
  list.replaceChildren();
  for (const f of result.features) {
    const props = f.properties;
    const text = props.status === "not_screened" ? props.detail : props.outside && props.outside.warning;
    if (text) list.append(el("li", { textContent: `${props.name || `Site ${props.id}`}: ${text}` }));
  }
}

function showLevelKey() {
  $("level-key").replaceChildren(
    el("h4", { textContent: "What the levels mean" }),
    ...CONFIG.levels.map((level) => el("div", { className: "key-row" }, dot(level), el("strong", { textContent: level }), MEANINGS[level])));
}

function showLegend(kindsShown) {
  if (legendControl) resultsMap.removeControl(legendControl);
  legendControl = L.control({ position: "bottomright" });
  legendControl.onAdd = () => {
    const div = L.DomUtil.create("div", "map-legend");
    for (const level of CONFIG.levels) {
      div.append(el("div", {}, Object.assign(el("span", { className: "swatch" }), { style: `background:${COLOUR[level]};border-color:${COLOUR[level]}` }), `${level} section`));
    }
    for (const group of LEGEND_GROUPS) {
      if (!group.kinds.some((k) => kindsShown.has(k))) continue;
      const style = FEATURE_STYLE[group.kinds[0]];
      const swatch = el("span", { className: "swatch" });
      swatch.style.cssText = `background:${style.fillColor}33;border-color:${style.color};${style.dashArray ? "border-style:dashed" : ""}`;
      div.append(el("div", {}, swatch, group.label));
    }
    return div;
  };
  legendControl.addTo(resultsMap);
}

$("back-btn").addEventListener("click", () => {
  $("results-screen").hidden = true;
  $("intake-screen").hidden = false;
  if (usingChat()) {
    pendingIntake = null;
    $("confirm-card").hidden = true;
    $("chat-form").hidden = false;
    say("What would you like to change? You can also edit the sites on the map.");
    setChatEnabled(true);
    $("chat-input").focus();
  }
});

// ================================================ 8. report and assistant

for (const button of document.querySelectorAll(".tab")) {
  button.addEventListener("click", () => showTab(button.dataset.tab));
}

function showTab(name) {
  for (const button of document.querySelectorAll(".tab")) {
    const on = button.dataset.tab === name;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", String(on));
  }
  for (const tab of ["sections", "report", "ask"]) $(`tab-${tab}`).hidden = tab !== name;
}

// Show a part on the map from a reference like "S1.P2".
function focusRef(ref) {
  const entry = partsByRef.get(ref);
  if (entry) activate(entry, true);
}

function refLabel(ref) {
  const entry = partsByRef.get(ref);
  return entry ? partLabel(entry.props, entry.part) : ref;
}

// Small, safe renderer for AI text: paragraphs, "- " lists, **bold** and
// [S1.P2] part links. Builds elements; never uses innerHTML.
function renderAiText(text) {
  const box = document.createDocumentFragment();
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    const target = bullet ? (list ||= box.appendChild(el("ul"))).appendChild(el("li")) : box.appendChild(el("p"));
    if (!bullet) list = null;
    appendInline(target, bullet ? bullet[1] : line);
  }
  return box;
}

function appendInline(node, text) {
  // The link already shows the part's location, so drop a repeat like "[S1.P1] (0.00–0.95 km)".
  text = text.replace(/\[(S\d+\.P\d+)\]\s*\(([^)]*)\)/g, (all, ref, inner) => (inner.trim() === refLabel(ref) ? `[${ref}]` : all));
  for (const piece of text.split(/(\*\*[^*]+\*\*|\[S\d+\.P\d+\])/)) {
    if (!piece) continue;
    const bold = piece.match(/^\*\*(.+)\*\*$/);
    const ref = piece.match(/^\[(S\d+\.P\d+)\]$/);
    if (bold) appendInline(node.appendChild(el("strong")), bold[1]);
    else if (ref) {
      const link = el("button", { type: "button", className: "ref-link", textContent: refLabel(ref[1]), title: "Show on the map" });
      link.addEventListener("click", () => focusRef(ref[1]));
      node.append(link);
    } else node.append(piece);
  }
}

// ---- the report

let lastReport = null;

async function loadReport() {
  lastReport = null;
  $("download-report").hidden = true;
  $("report-body").replaceChildren(el("p", { className: "muted small", textContent: "Writing your report…" }));
  try {
    lastReport = await getJson("/report", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(lastRequest),
    });
    renderReport(lastReport);
  } catch (error) {
    $("report-body").replaceChildren(el("p", { className: "muted small", textContent:
      `The report isn't available right now (${error.message}). The sections and map are unaffected.` }));
  }
}

function renderReport(report) {
  const body = $("report-body");
  body.replaceChildren(el("h3", { textContent: report.headline }), el("p", { className: "overview", textContent: report.overview }));
  for (const finding of report.findings) {
    const levels = [...new Set(finding.parts.map((ref) => partsByRef.get(ref)?.part.level).filter(Boolean))];
    const card = el("div", { className: "finding" },
      el("h4", {}, ...levels.map((l) => dot(l)), finding.title),
      el("p", { textContent: finding.explanation }));
    if (finding.next_steps.length) card.append(el("ul", {}, ...finding.next_steps.map((t) => el("li", { textContent: t }))));
    const show = el("button", { type: "button", className: "link show", textContent: "Show on the map" });
    show.addEventListener("click", () => focusRef(finding.parts[0]));
    card.append(show);
    body.append(card);
  }
  if (report.next_steps.length) {
    body.append(el("h4", { className: "section", textContent: "Next steps" }),
      el("ul", {}, ...report.next_steps.map((t) => el("li", { textContent: t }))));
  }
  body.append(el("div", { className: "caveats" }, ...report.caveats.map((t) => el("p", { textContent: t }))));
  if (report.source !== "ai") {
    body.append(el("p", { className: "source-note", textContent: "The AI summary wasn't available, so this report is built directly from the screening results." }));
  }
  $("download-report").hidden = false;
}

$("download-report").addEventListener("click", () => {
  if (!lastReport) return;
  const r = lastReport;
  const lines = [
    "# Nature screening report", "",
    `**${r.headline}**`, "", r.overview, "",
    `Type of work: ${WORK_TYPES[lastIntake.work_type].label}`, "",
    "## Findings", "",
  ];
  for (const f of r.findings) {
    const where = f.parts.map(refLabel).join(", ");
    lines.push(`### ${f.title}`, "", `Where: ${where}`, "", f.explanation, "", ...f.next_steps.map((t) => `- ${t}`), "");
  }
  if (r.next_steps.length) lines.push("## Next steps", "", ...r.next_steps.map((t) => `- ${t}`), "");
  lines.push("## Sections", "", "| Site | Where | Level |", "|---|---|---|");
  for (const entry of partsByRef.values()) {
    lines.push(`| ${entry.props.name || `Site ${entry.props.id}`} | ${partLabel(entry.props, entry.part)} | ${entry.part.level} |`);
  }
  lines.push("", "## What the levels mean", "", ...CONFIG.levels.map((l) => `- **${l}**: ${MEANINGS[l]}`), "");
  lines.push("## Caveats", "", ...r.caveats.map((t) => `- ${t}`), "");
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const link = el("a", { href: URL.createObjectURL(blob), download: "nature-screening-report.md" });
  link.click();
  URL.revokeObjectURL(link.href);
});

// ---- the assistant

const askHistory = [];

function resetAsk() {
  askHistory.length = 0;
  $("ask-messages").replaceChildren();
  const suggestions = ["Which part is the problem?", "What should we do next?"];
  if (lastIntake.dewatering === "yes") suggestions.push("What if we didn't need to pump out groundwater?");
  else if (lastIntake.beyond_site === "yes") suggestions.push("What if the work stayed on land we already operate?");
  if (partsByRef.size) suggestions.push("How far is the nearest SSSI?");
  $("ask-suggestions").replaceChildren(...suggestions.map((q) => {
    const chip = el("button", { type: "button", className: "chip", textContent: q });
    chip.addEventListener("click", () => askQuestion(q));
    return chip;
  }));
}

$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("ask-input").value.trim();
  if (!question) return;
  $("ask-input").value = "";
  askQuestion(question);
});

async function askQuestion(question) {
  askHistory.push({ role: "user", content: question });
  $("ask-messages").append(el("div", { className: "msg user", textContent: question }));
  const thinking = el("div", { className: "msg assistant muted", textContent: "Looking into it…" });
  $("ask-messages").append(thinking);
  $("ask-suggestions").hidden = true;
  const buttons = $("ask-form").querySelectorAll("input, button");
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const reply = await getJson("/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...lastRequest, history: askHistory }),
    });
    askHistory.push({ role: "assistant", content: reply.answer });
    thinking.replaceWith(el("div", { className: "msg assistant" }, renderAiText(reply.answer)));
    if (reply.refs.length) focusRef(reply.refs[0]);
  } catch (error) {
    askHistory.pop();
    thinking.replaceWith(el("div", { className: "msg error", textContent: `The assistant isn't available right now: ${error.message}` }));
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
    $("ask-input").focus();
  }
}

updateSitesLine();
setMode("chat");
