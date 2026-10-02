"use strict";

// Nature Exposure Snapshot frontend. Sections:
//   1. constants and state      5. uploading coordinates
//   2. map and background       6. chat and form
//   3. the site list            7. screening and results
//   4. postcode pins

// =============================================== 1. constants and state

const CONFIG = JSON.parse(document.getElementById("config").textContent);
const WORK_TYPES = CONFIG.work_types;
const GEOMETRY = CONFIG.site_geometry;
const STORM_OVERFLOW = "storm_overflow";
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

const LEVELS = [
  { key: "A1", label: "A1", colour: "#2e9e44" },
  { key: "A2", label: "A2", colour: "#f0a202" },
  { key: "A3", label: "A3", colour: "#d7263d" },
  { key: "A4", label: "A4", colour: "#7b2cbf" },
  { key: "not_screened", label: "Not screened", colour: "#9e9e9e" },
];
const COLOUR = Object.fromEntries(LEVELS.map((l) => [l.key, l.colour]));
const RANK = { not_screened: 0, A1: 1, A2: 2, A3: 3, A4: 4 };
const SITE_COLOUR = "#1f6f5c";

// The 13 IRZ code digits, in order (docs/irz_code_mapping.md).
const IRZ_CATEGORIES = [
  "irz_all_consultations", "irz_infrastructure", "irz_wind_solar", "irz_minerals_oil_gas",
  "irz_rural_non_residential", "irz_residential", "irz_rural_residential", "irz_air_pollution",
  "irz_combustion", "irz_waste", "irz_composting", "irz_discharges", "irz_water_supply",
];
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
const GENERIC_HABITAT = "No main habitat but additional habitats present";
const KIND_LABEL = { point: "Point", line: "Route", polygon: "Area" };
const SOURCE_LABEL = { drawn: "drawn", uploaded: "uploaded", postcode: "postcode", overflow: "storm overflow" };

const DATA_ATTRIBUTION =
  "Site data: © Natural England copyright, contains Ordnance Survey data © Crown copyright and database right 2026; " +
  "storm overflows © Environment Agency copyright and/or database right 2026";

// British National Grid, for showing uploaded eastings/northings on the map.
// (The server does the accurate conversion used for screening.)
proj4.defs("EPSG:27700",
  "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy " +
  "+towgs84=446.448,-125.157,542.06,0.15,0.247,0.842,-20.489 +units=m +no_defs");

// State
const history = [];           // chat messages sent to /intake
let knownAnswers = {};        // intake answers so far, from the chat or the form
let pendingIntake = null;     // confirmed-to-be intake from the chat summary
let lastIntake = null;        // the intake used for the latest screening
const sites = [];             // see addSite() for the shape
let nextSiteNo = 1;

const $ = (id) => document.getElementById(id);

function dot(colour) {
  const span = document.createElement("span");
  span.className = "dot";
  span.style.background = colour;
  return span;
}

function paragraph(text, className) {
  const p = document.createElement("p");
  p.textContent = text;
  if (className) p.className = className;
  return p;
}

function joinWords(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
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

// =============================================== 2. map and background

const map = L.map("map");
const canvas = L.canvas({ padding: 0.5 }); // the risk zones are thousands of shapes
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
map.attributionControl.addAttribution(DATA_ATTRIBUTION);

const aoiLayer = L.geoJSON(CONFIG.aoi, {
  pmIgnore: true,
  interactive: false,
  style: { color: "#555", weight: 1.5, dashArray: "6 4", fill: false },
}).addTo(map);
map.fitBounds(aoiLayer.getBounds());

const protectedLayer = L.geoJSON(null, {
  renderer: canvas,
  pmIgnore: true,
  style: (f) => f.properties.designation === "SSSI"
    ? { color: "#2e7d32", weight: 1, fillColor: "#2e7d32", fillOpacity: 0.15 }
    : { color: "#6a1b9a", weight: 1.2, dashArray: "4 3", fillColor: "#6a1b9a", fillOpacity: 0.06 },
  onEachFeature: (f, layer) => layer.bindTooltip(`${f.properties.site} (${f.properties.designation})`, { sticky: true }),
}).addTo(map);

let irzFeatures = [];
const irzLayer = L.geoJSON(null, {
  renderer: canvas,
  pmIgnore: true,
  interactive: false,
  style: { color: "#d9822b", weight: 0.6, fillColor: "#f0a202", fillOpacity: 0.07 },
}).addTo(map);

Promise.all([getJson("/layers/protected"), getJson("/layers/irz")])
  .then(([protectedSites, zones]) => {
    protectedLayer.addData(protectedSites);
    irzFeatures = zones.features;
    refreshRiskZones();
  })
  .catch(() => { $("status").textContent = "The background map layers couldn't be loaded."; });

// IRZ triggers that apply now: always "consult on all applications" (rule 4),
// plus the triggers for the work type and any yes answers, once known.
function activeTriggers() {
  const triggers = [{ category: "irz_all_consultations", values: CONFIG.all_consultations_values }];
  const workType = WORK_TYPES[knownAnswers.work_type];
  if (workType) triggers.push(...workType.triggers);
  for (const [field, extra] of Object.entries(CONFIG.intake_triggers)) {
    if (knownAnswers[field] === "yes") triggers.push(...extra);
  }
  return triggers;
}

function refreshRiskZones() {
  const triggers = activeTriggers();
  const fires = (code) => triggers.some((t) => {
    if (t.min_site_area_ha) return false; // depends on the site's size; shown once screened
    const digit = Number(code[IRZ_CATEGORIES.indexOf(t.category)]);
    return t.values.includes(digit);
  });
  irzLayer.clearLayers();
  irzLayer.addData({ type: "FeatureCollection", features: irzFeatures.filter((f) => fires(f.properties.irzcode)) });
  irzLayer.bringToBack();
  $("legend-irz").textContent = knownAnswers.work_type
    ? "Risk zones that apply to this work"
    : "Risk zones: consult on all applications";
}

const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = `
    <div><span class="swatch" style="background:rgba(46,125,50,.25);border-color:#2e7d32"></span>SSSI</div>
    <div><span class="swatch" style="background:rgba(106,27,154,.1);border-color:#6a1b9a;border-style:dashed"></span>SAC, SPA or Ramsar</div>
    <div><span class="swatch" style="background:rgba(240,162,2,.2);border-color:#d9822b"></span><span id="legend-irz"></span></div>
    <div><span class="swatch" style="background:none;border-color:#555;border-style:dashed"></span>Screened area</div>
    <div id="legend-levels" hidden></div>`;
  return div;
};
legend.addTo(map);

// Drawing tools (Leaflet-Geoman)
const DRAW_CONTROLS = {
  position: "topleft",
  drawMarker: true,
  drawPolyline: true,
  drawPolygon: true,
  drawRectangle: true,
  drawCircle: false,
  drawCircleMarker: false,
  drawText: false,
  cutPolygon: false,
  rotateMode: false,
  editMode: true,
  dragMode: true,
  removalMode: true,
};
map.pm.addControls(DRAW_CONTROLS);
map.pm.setPathOptions({ color: SITE_COLOUR, weight: 3 });
map.on("pm:create", (e) => addDrawnSite(e.layer, e.shape));
map.on("pm:remove", (e) => {
  const site = sites.find((s) => s.layer === e.layer);
  if (site) removeSite(site, { keepLayer: true });
});

// ===================================================== 3. the site list

// A site is { id, name, source, kind, layer, pending, note,
//   geometry, crs }: geometry/crs are kept for uploaded sites so their exact
//   coordinates are sent; drawn sites and pins are read from their layer.
function addSite(fields, { render = true } = {}) {
  const site = { id: nextSiteNo++, pending: false, note: null, geometry: null, crs: null, ...fields };
  sites.push(site);
  if (render) renderSites();
  return site;
}

function removeSite(site, { keepLayer = false } = {}) {
  const i = sites.indexOf(site);
  if (i === -1) return;
  sites.splice(i, 1);
  if (site.source === "overflow") setOverflowSelected(site.overflowId, false);
  else if (!keepLayer && site.layer) map.removeLayer(site.layer);
  renderSites();
}

function confirmedSites() {
  return sites.filter((s) => !s.pending);
}

function addDrawnSite(layer, shape) {
  const kind = shape === "Marker" ? "point" : shape === "Line" ? "line" : "polygon";
  const n = sites.filter((s) => s.kind === kind).length + 1;
  addSite({ name: `${KIND_LABEL[kind]} ${n}`, source: "drawn", kind, layer });
  nudgeChat();
}

const SITE_LIST_LIMIT = 100;

function renderSites() {
  const list = $("site-list");
  list.replaceChildren();
  for (const site of sites.slice(0, SITE_LIST_LIMIT)) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "site-name";
    name.textContent = site.name;
    name.title = site.note || site.name;
    const tag = document.createElement("span");
    tag.className = site.pending ? "tag pending" : "tag";
    tag.textContent = site.pending ? "check pin" : `${KIND_LABEL[site.kind].toLowerCase()} · ${SOURCE_LABEL[site.source]}`;
    li.append(name, tag);
    if (site.pending) {
      const ok = document.createElement("button");
      ok.type = "button";
      ok.textContent = "Confirm";
      ok.addEventListener("click", () => confirmPin(site));
      li.append(ok);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary";
    remove.textContent = "×";
    remove.title = `Remove ${site.name}`;
    remove.addEventListener("click", () => removeSite(site));
    li.append(remove);
    list.append(li);
  }
  if (sites.length > SITE_LIST_LIMIT) {
    list.append(paragraph(`…and ${sites.length - SITE_LIST_LIMIT} more`, "small muted"));
  }
  const confirmed = confirmedSites().length;
  $("site-count").textContent = confirmed ? `(${confirmed})` : "";
  $("sites-done").hidden = !(confirmed > 0 && chatActive() && history.length > 0 && !pendingIntake);
}

// ---- storm overflows: shown only when the work type is storm overflow

let overflows = null; // { features, markers: Map(id -> marker), cluster }

function overflowStyle(selected) {
  return { radius: 6, color: "#fff", weight: 1.5, fillColor: selected ? SITE_COLOUR : "#78909c", fillOpacity: 0.9 };
}

async function loadOverflows() {
  if (overflows) return overflows;
  const data = await getJson("/sites/examples");
  const cluster = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 40 });
  const markers = new Map();
  for (const f of data.features) {
    const [lon, lat] = f.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], { ...overflowStyle(false), pmIgnore: true });
    marker.bindTooltip(f.properties.name);
    marker.on("click", () => toggleOverflow(f));
    markers.set(f.id, marker);
    cluster.addLayer(marker);
  }
  overflows = { features: data.features, markers, cluster };
  return overflows;
}

function setOverflowSelected(id, selected) {
  const marker = overflows && overflows.markers.get(id);
  if (marker) marker.setStyle(overflowStyle(selected));
}

function toggleOverflow(feature, { render = true } = {}) {
  const existing = sites.find((s) => s.overflowId === feature.id);
  if (existing) return removeSite(existing);
  addSite({
    name: feature.properties.name, source: "overflow", kind: "point", overflowId: feature.id,
    layer: overflows.markers.get(feature.id), geometry: feature.geometry, crs: "EPSG:4326",
  }, { render });
  setOverflowSelected(feature.id, true);
  if (render) nudgeChat();
}

$("use-all-overflows").addEventListener("click", () => {
  for (const f of overflows.features) {
    if (!sites.some((s) => s.overflowId === f.id)) toggleOverflow(f, { render: false });
  }
  renderSites();
  nudgeChat();
});

async function setWorkType(workType) {
  if (workType === knownAnswers.work_type) return;
  knownAnswers.work_type = workType || null;
  refreshRiskZones();
  const isOverflow = workType === STORM_OVERFLOW;
  $("overflow-picker").hidden = !isOverflow;
  if (isOverflow) {
    try {
      map.addLayer((await loadOverflows()).cluster);
    } catch (_) {
      $("status").textContent = "The storm overflow sites couldn't be loaded.";
    }
  } else if (overflows) {
    map.removeLayer(overflows.cluster);
    sites.filter((s) => s.source === "overflow").forEach((s) => removeSite(s));
  }
}

// ==================================================== 4. postcode pins

// The AI only passes on postcodes the user typed; /geocode looks them up.
async function placePostcodes(postcodes) {
  for (const postcode of postcodes) {
    if (sites.some((s) => s.postcode === postcode)) continue;
    let place;
    try {
      place = await getJson(`/geocode?postcode=${encodeURIComponent(postcode)}`);
    } catch (error) {
      addMessage("error", `I couldn't find ${postcode}: ${error.message}`);
      continue;
    }
    const latlng = L.latLng(place.latitude, place.longitude);
    const marker = L.marker(latlng, { draggable: true, pmIgnore: true, title: place.postcode }).addTo(map);
    const site = addSite({
      name: place.postcode, source: "postcode", kind: "point", layer: marker,
      pending: true, postcode, note: place.accuracy,
    });
    marker.bindPopup(() => pinCard(site, place), { maxWidth: 280 });
    marker.on("dragend", () => marker.openPopup());
    map.setView(latlng, 15);
    marker.openPopup();
    if (!place.in_area) {
      addMessage("error", `${place.postcode} is outside the screened area, so a site there can't be checked.`);
    }
  }
}

function pinCard(site, place) {
  const card = document.createElement("div");
  card.className = "site-card";
  const title = document.createElement("h3");
  title.textContent = place.postcode;
  card.append(title, paragraph(place.accuracy, "where"));
  if (site.pending) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Confirm this location";
    button.addEventListener("click", () => confirmPin(site));
    card.append(button);
  } else {
    card.append(paragraph("Location confirmed.", "small muted"));
  }
  return card;
}

function confirmPin(site) {
  if (!site.pending) return;
  site.pending = false;
  site.layer.closePopup();
  renderSites();
  if (chatActive() && !pendingIntake) {
    sendUserMessage(`I've confirmed the location for ${site.postcode} on the map.`);
  }
}

// A postcode district (e.g. CB7) only zooms the map, for the user to place the site.
async function zoomToDistricts(districts) {
  for (const district of districts) {
    try {
      const place = await getJson(`/geocode?postcode=${encodeURIComponent(district)}`);
      map.setView([place.latitude, place.longitude], 13);
      return;
    } catch (_) { /* try the next one */ }
  }
}

// ============================================ 5. uploading coordinates

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

$("add-pasted").addEventListener("click", () => importText($("paste").value, "pasted text"));
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
    return uploadStatus(`That would make more than ${GEOMETRY.max_sites} sites, the limit for one screening.`, true);
  }

  const added = parsed.sites.map((s) => addSite({
    name: s.name || `${KIND_LABEL[s.kind]} ${nextSiteNo}`,
    source: "uploaded",
    kind: s.kind,
    geometry: s.geometry,
    crs: s.crs,
    layer: L.geoJSON(displayGeometry(s.geometry, s.crs), {
      pmIgnore: true, // uploaded coordinates are exact, so they aren't dragged
      style: { color: SITE_COLOUR, weight: 3, fillOpacity: 0.15 },
      pointToLayer: (_, latlng) => L.circleMarker(latlng, {
        radius: 6, color: "#fff", weight: 1.5, fillColor: SITE_COLOUR, fillOpacity: 0.9, pmIgnore: true,
      }),
    }).bindTooltip(s.name || "Uploaded site").addTo(map),
  }, { render: false }));
  renderSites();
  map.fitBounds(L.featureGroup(added.map((s) => s.layer)).getBounds(), { padding: [30, 30], maxZoom: 15 });
  uploadStatus(`Added ${added.length} site${added.length === 1 ? "" : "s"} from ${source}.`);
  nudgeChat();
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

// For the map only: uploaded British National Grid shapes shown in lat/long.
function displayGeometry(geometry, crs) {
  if (crs !== "EPSG:27700") return geometry;
  const convert = (c) => (typeof c[0] === "number" ? proj4("EPSG:27700", "EPSG:4326", c) : c.map(convert));
  return { type: geometry.type, coordinates: convert(geometry.coordinates) };
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

// ================================================== 6. chat and form

function chatActive() {
  return !$("chat").hidden;
}

// Called when sites change, so the chat can offer to carry on.
function nudgeChat() {
  renderSites();
}

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

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  $("chat-input").value = "";
  sendUserMessage(text);
});

$("sites-done").addEventListener("click", () => {
  const n = confirmedSites().length;
  sendUserMessage(`I've added ${n} site${n === 1 ? "" : "s"} on the map.`);
});

function sendUserMessage(text) {
  history.push({ role: "user", content: text });
  addMessage("user", text);
  return askNext();
}

async function askNext() {
  setChatEnabled(false);
  $("sites-done").hidden = true;
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
      $("confirm-box").hidden = false;
      $("chat-form").hidden = true;
    } else {
      history.push({ role: "assistant", content: turn.text });
      addMessage("assistant", turn.text);
      setChatEnabled(true);
      $("chat-input").focus();
    }
    if (turn.postcodes && turn.postcodes.length) placePostcodes(turn.postcodes);
    else if (turn.districts && turn.districts.length) zoomToDistricts(turn.districts);
    renderSites();
  } catch (error) {
    $("status").textContent = "";
    showFallback(`${error.message} The chat assistant isn't available right now, so please answer these instead.`);
  }
}

// Answers known so far shape the map: the risk zones shown, and whether the
// storm overflows appear.
function applyAnswers(answers) {
  if (!answers) return;
  const { work_type: workType, ...rest } = answers;
  for (const [key, value] of Object.entries(rest)) {
    if (value) knownAnswers[key] = value;
  }
  if (workType) setWorkType(workType);
  else refreshRiskZones();
}

$("confirm-btn").addEventListener("click", () => {
  $("confirm-box").hidden = true;
  runSnapshot(pendingIntake);
});

$("edit-btn").addEventListener("click", () => {
  const summary = $("summary-text").textContent;
  history.push({ role: "assistant", content: summary });
  addMessage("assistant", summary);
  pendingIntake = null;
  $("confirm-box").hidden = true;
  $("chat-form").hidden = false;
  setChatEnabled(true);
  $("chat-input").placeholder = "What would you like to change?";
  $("chat-input").focus();
});

// ---- the five-question form (used when the chat isn't available)

const form = $("fallback");
for (const [key, spec] of Object.entries(WORK_TYPES)) form.elements.work_type.add(new Option(spec.label, key));

function showFallback(note) {
  $("chat").hidden = true;
  form.hidden = false;
  $("fallback-note").textContent = note;
  for (const [key, value] of Object.entries(knownAnswers)) {
    if (value && form.elements[key]) form.elements[key].value = value;
  }
  renderSites();
}

$("use-form").addEventListener("click", () =>
  showFallback("Answer these five questions. The answers apply to every site."),
);

form.addEventListener("change", (event) => {
  const { name, value } = event.target;
  if (name === "work_type") setWorkType(value);
  else if (name) { knownAnswers[name] = value || null; refreshRiskZones(); }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!confirmedSites().length) {
    $("status").textContent = "Add at least one site first: draw it on the map, upload coordinates, or give a postcode.";
    return;
  }
  runSnapshot(Object.fromEntries(new FormData(form).entries()));
});

// ========================================= 7. screening and results

let results = null; // { layer, cluster }

function siteFeature(site) {
  const geometry = site.geometry || site.layer.toGeoJSON().geometry;
  return {
    type: "Feature",
    id: String(site.id),
    crs: site.crs || "EPSG:4326",
    properties: { name: site.name, source: site.source, note: site.note },
    geometry,
  };
}

async function runSnapshot(intake) {
  const chosen = confirmedSites();
  if (!chosen.length) {
    $("status").textContent = "Add at least one site first: draw it on the map, upload coordinates, or give a postcode.";
    return;
  }
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    $("status").textContent = `Screening ${chosen.length} site${chosen.length === 1 ? "" : "s"}…`;
    const result = await getJson("/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intake, sites: { type: "FeatureCollection", features: chosen.map(siteFeature) } }),
    });
    lastIntake = intake;
    $("status").textContent = "";
    showResults(result);
  } catch (error) {
    $("status").textContent = `Screening failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function levelKey(props) {
  return props.level || "not_screened";
}

function setSitesVisible(visible) {
  for (const site of sites) {
    if (site.source === "overflow") continue;
    if (visible) site.layer.addTo(map);
    else map.removeLayer(site.layer);
  }
  if (overflows) {
    if (visible && knownAnswers.work_type === STORM_OVERFLOW) map.addLayer(overflows.cluster);
    else map.removeLayer(overflows.cluster);
  }
  if (visible) map.pm.addControls(DRAW_CONTROLS); else map.pm.removeControls();
}

function clearResults() {
  if (!results) return;
  map.removeLayer(results.layer);
  map.removeLayer(results.cluster);
  results = null;
  $("legend-levels").hidden = true;
}

function showResults(result) {
  clearResults();
  setSitesVisible(false);
  $("sites-panel").hidden = true;

  const layer = L.featureGroup();
  const cluster = L.markerClusterGroup({
    showCoverageOnHover: false,
    iconCreateFunction: (c) => {
      const markers = c.getAllChildMarkers();
      const top = markers.reduce((a, m) => (RANK[m.options.level] > RANK[a] ? m.options.level : a), "not_screened");
      return L.divIcon({ html: `<div style="background:${COLOUR[top]}">${markers.length}</div>`, className: "cluster", iconSize: L.point(36, 36) });
    },
  });

  for (const feature of result.features) {
    const props = feature.properties;
    const level = levelKey(props);
    if (props.kind === "line" && props.sections) {
      for (const section of props.sections) {
        L.geoJSON(section.geometry, { pmIgnore: true, style: { color: COLOUR[section.level], weight: 6, opacity: 0.9 } })
          .bindPopup(() => sectionCard(props, section), { maxWidth: 320 })
          .addTo(layer);
      }
    } else if (props.kind === "point") {
      const latlng = L.geoJSON(feature).getBounds().getCenter();
      const marker = L.circleMarker(latlng, {
        level, pmIgnore: true, radius: 7, color: "#fff", weight: 1.5, fillColor: COLOUR[level], fillOpacity: 0.95,
      });
      marker.bindPopup(() => siteCard(props), { maxWidth: 320 });
      cluster.addLayer(marker);
    } else {
      L.geoJSON(feature.geometry, {
        pmIgnore: true,
        style: props.kind === "line"
          ? { color: COLOUR[level], weight: 5, dashArray: "6 6" }
          : { color: COLOUR[level], weight: 2, fillColor: COLOUR[level], fillOpacity: 0.3 },
      }).bindPopup(() => siteCard(props), { maxWidth: 320 }).addTo(layer);
    }
    if (props.outside) {
      L.geoJSON(props.outside.geometry, {
        pmIgnore: true,
        style: { color: "#9e9e9e", weight: props.kind === "line" ? 5 : 1.5, dashArray: "6 6", fillOpacity: 0.12 },
      }).bindTooltip(props.outside.warning, { sticky: true }).addTo(layer);
    }
  }

  layer.addTo(map);
  cluster.addTo(map);
  results = { layer, cluster };
  const bounds = L.featureGroup([layer, cluster]).getBounds();
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });

  showCounts(result.summary);
  showWarnings(result.features);
  showLevelLegend();
  $("results").hidden = false;
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
}

function showWarnings(features) {
  const list = $("warnings");
  list.replaceChildren();
  for (const f of features) {
    const props = f.properties;
    const text = props.status === "not_screened" ? props.detail : props.outside && props.outside.warning;
    if (!text) continue;
    const li = document.createElement("li");
    li.textContent = `${props.name || props.id}: ${text}`;
    list.append(li);
  }
}

function showLevelLegend() {
  const box = $("legend-levels");
  box.replaceChildren();
  for (const level of LEVELS) {
    const row = document.createElement("div");
    row.append(dot(level.colour), ` ${level.label}`);
    box.append(row);
  }
  box.hidden = false;
}

$("edit-sites").addEventListener("click", () => {
  clearResults();
  setSitesVisible(true);
  $("results").hidden = true;
  $("sites-panel").hidden = false;
  showRescreen();
});

// After changing sites, screen again with the same answers.
function showRescreen() {
  let button = $("rescreen");
  if (!button) {
    button = document.createElement("button");
    button.id = "rescreen";
    button.type = "button";
    button.textContent = "Screen again";
    button.addEventListener("click", () => runSnapshot(lastIntake));
    $("sites-panel").append(button);
  }
  button.hidden = !lastIntake;
}

// --------------------------------------------------------- site cards

function siteCard(props) {
  const card = document.createElement("div");
  card.className = "site-card";
  const title = document.createElement("h3");
  title.textContent = props.name || props.id;
  card.append(title);

  const level = levelKey(props);
  card.append(levelLine(level));
  if (props.source === "postcode") {
    card.append(paragraph("Located from a postcode, so only accurate to the postcode's centre unless the pin was moved.", "where"));
  }
  if (level === "not_screened") {
    card.append(paragraph(props.detail || "This site couldn't be screened."));
    return card;
  }
  if (props.may_escalate_to) card.append(escalation(props.level, props.may_escalate_to, props.reasons));
  card.append(reasonList(props.reasons));
  if (props.outside) card.append(paragraph(props.outside.warning, "warning"));
  appendContext(card, props.context);
  return card;
}

function sectionCard(props, section) {
  const card = document.createElement("div");
  card.className = "site-card";
  const title = document.createElement("h3");
  title.textContent = props.name || props.id;
  card.append(title);
  card.append(paragraph(
    `${section.start_km.toFixed(2)}–${section.end_km.toFixed(2)} km along a ${props.length_km.toFixed(2)} km route ` +
    `(${props.corridor_m} m either side)`, "where",
  ));
  card.append(levelLine(section.level));
  if (section.may_escalate_to) card.append(escalation(section.level, section.may_escalate_to, section.reasons));
  card.append(reasonList(section.reasons));
  card.append(paragraph(`Whole route: ${props.level} (its highest section).`, "small"));
  if (props.outside) card.append(paragraph(props.outside.warning, "warning"));
  appendContext(card, section.context);
  return card;
}

function levelLine(level) {
  const p = document.createElement("p");
  p.className = "level";
  p.append(dot(COLOUR[level]), level === "not_screened" ? "Not screened" : `Level ${level}`);
  return p;
}

function escalation(level, to, reasons) {
  const focus = [...new Set(
    reasons.filter((r) => r.level === level && ESCALATION_FOCUS[r.rule]).map((r) => ESCALATION_FOCUS[r.rule]),
  )];
  const why = focus.length ? ` after a closer look at ${joinWords(focus)}` : "";
  return paragraph(`May escalate to ${to}${why}.`);
}

function reasonList(reasons) {
  const list = document.createElement("ul");
  for (const text of plainReasons(reasons)) {
    const li = document.createElement("li");
    li.textContent = text;
    list.append(li);
  }
  return list;
}

function appendContext(card, context) {
  const lines = contextLines(context);
  if (!lines.length) return;
  const box = document.createElement("div");
  box.className = "context";
  lines.forEach((line) => box.append(paragraph(line)));
  card.append(box);
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
  return [...new Set(kept.map((r) => plainReason(r) + (r.where ? ` (${r.where})` : "")))];
}

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

renderSites();
