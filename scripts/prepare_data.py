"""Clean the raw nature datasets for the Cambridgeshire AOI.

For each file in data/raw/: load it, reproject to British National Grid
(EPSG:27700), clip to the AOI rectangle in data/aoi/cambs_aoi.zip and save
it to data/clean/ as a GeoPackage with a short name. Then print a summary.
"""

import re
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import force_2d, make_valid

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"
AOI_ZIP = ROOT / "data" / "aoi" / "cambs_aoi.zip"

BNG = "EPSG:27700"

# Raw filename prefix -> short output name, for the single-layer datasets
SIMPLE_LAYERS = {
    "SSSI_Impact_Risk_Zones_England": "irz",
    "Sites_of_Special_Scientific_Interest_England": "sssi",
    "SSSI_Units_England": "sssi_units",
    "Special_Areas_of_Conservation_England": "sac",
    "Special_Protection_Areas_England": "spa",
    "Ramsar_England": "ramsar",
    "Priority_Habitat_Inventory_England": "priority_habitat",
}
PRIORITY_HABITAT_SIMPLIFY_M = 1
AW_REVISED_PREFIX = "Ancient_Woodland_Inventory_Revised_England"
AW_ORIGINAL_PREFIX = "Ancient_Woodland_England"
# Original woods more than this far from any revised wood are treated as
# outside the revised inventory's coverage (see DECISIONS.md).
AW_REVISED_COVERAGE_M = 3_000
EDM_PREFIX = "EDM_"

# The IRZ download has no per-category columns, only a 13-digit code inside
# the irzurl field. Digit positions map, in order, to the development
# categories below; see docs/irz_code_mapping.md for how this was verified
# and what each digit value means. 0 means the category is not triggered.
IRZ_CATEGORIES = {
    "irz_all_consultations": "All Consultations",
    "irz_infrastructure": "Infrastructure",
    "irz_wind_solar": "Wind & Solar Energy",
    "irz_minerals_oil_gas": "Minerals, Oil and Gas",
    "irz_rural_non_residential": "Rural Non Residential",
    "irz_residential": "Residential",
    "irz_rural_residential": "Rural Residential",
    "irz_air_pollution": "Air Pollution",
    "irz_combustion": "Combustion",
    "irz_waste": "Waste",
    "irz_composting": "Composting",
    "irz_discharges": "Discharges",
    "irz_water_supply": "Water Supply",
}
IRZ_CODE_LENGTH = len(IRZ_CATEGORIES)
IRZ_SUMMARY_COLUMNS = [
    "irz_all_consultations", "irz_infrastructure", "irz_discharges", "irz_water_supply",
    "irz_combustion", "irz_composting", "irz_air_pollution", "irz_rural_non_residential",
]

# EDM spreadsheet heading (whitespace collapsed) -> short column name.
# docs/data_dictionary.md lists the same lookup.
EDM_COLUMNS = {
    "Unique ID": "unique_id",
    "Water Company Name": "company",
    "Site Name (EA Consents Database)": "site_name",
    "Site Name (WaSC operational) [optional]": "site_name_operational",
    "EA Permit Reference (EA Consents Database)": "permit_ref",
    "WaSC Supplementary Permit Ref. [optional]": "permit_ref_supplementary",
    "Activity Reference on Permit": "activity_ref",
    "Storm Discharge Asset Type": "asset_type",
    "Outlet Discharge NGR (EA Consents Database)": "outlet_ngr",
    "WFD Waterbody ID (Cycle 3) (discharge outlet)": "wfd_waterbody_id",
    "WFD Waterbody Catchment Name (Cycle 3) (discharge outlet)": "wfd_catchment",
    "Receiving Water / Environment (common name) (EA Consents Database)": "receiving_water",
    "Shellfish Water(s) (only populate for storm overflow with a Shellfish Water EDM requirement)": "shellfish_water",
    "Bathing Water(s) (only populate for storm overflow with a Bathing Water EDM requirement)": "bathing_water",
    "Treatment Method on Permit (over & above Storm Tank settlement / screening)": "treatment_method",
    "Total Duration (hh:mm:ss) all spills prior to processing through 12-24h count method": "spill_duration_total",
    "Counted spills using 12-24h count method": "spill_count",
    "Long-term average spill count": "spill_count_lt_avg",
    "Data start - calendar year": "data_start_year",
    "EDM Operation - % of reporting period EDM operational": "edm_operational_pct",
    "EDM Operation - Reporting % - Primary Reason <90%": "edm_low_reason",
    "EDM Operation - Action taken / planned - Status & timeframe": "edm_low_action",
    "High Spill Frequency - Operational Review - Single year reason": "high_spill_reason_year",
    "High Spill Frequency - Operational Review - Long-term average reason": "high_spill_reason_lt_avg",
    "Investigation activity for the reporting period": "investigation",
    "Improvement activity for the reporting period": "improvement",
}

# 100 km square letters of the OS National Grid
NGR_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # no I


def find_raw(prefix: str) -> Path:
    matches = sorted(RAW_DIR.glob(f"{prefix}*"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one file starting {prefix!r} in {RAW_DIR}, found {matches}")
    return matches[0]


def load_aoi() -> gpd.GeoDataFrame:
    return gpd.read_file(f"zip://{AOI_ZIP}").to_crs(BNG)


def read_in_aoi(path: Path, aoi: gpd.GeoDataFrame, **kwargs) -> gpd.GeoDataFrame:
    """Read only features whose bounding box overlaps the AOI.

    The bbox filter has to be in the layer's own CRS, so the AOI (densified so
    its edges reproject faithfully) is reprojected to that CRS first.
    """
    layer_crs = pyogrio.read_info(path, **kwargs)["crs"]
    bbox = tuple(aoi.segmentize(1_000).to_crs(layer_crs).total_bounds)
    return gpd.read_file(path, bbox=bbox, **kwargs)


def to_bng_clipped(gdf: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.to_crs(BNG)
    # "structure" repair keeps polygons as polygons; the default can turn
    # self-intersecting rings into stray lines.
    geoms = make_valid(force_2d(gdf.geometry.values), method="structure", keep_collapsed=False)
    gdf = gdf.set_geometry(geoms)
    clipped = gpd.clip(gdf, aoi, keep_geom_type=True)
    return clipped[~clipped.geometry.is_empty].reset_index(drop=True)


def save(gdf: gpd.GeoDataFrame, name: str) -> Path:
    out = CLEAN_DIR / f"{name}.gpkg"
    out.unlink(missing_ok=True)
    gdf.to_file(out, layer=name, driver="GPKG")
    return out


def add_irz_code_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()
    gdf["irzcode"] = gdf["irzurl"].str.extract(r"irzcode=(\d+)", expand=False)
    bad = gdf["irzcode"].str.len() != IRZ_CODE_LENGTH
    if bad.any():
        raise ValueError(f"{bad.sum()} IRZ rows lack a {IRZ_CODE_LENGTH}-digit irzcode")
    for i, col in enumerate(IRZ_CATEGORIES):
        gdf[col] = gdf["irzcode"].str[i].astype(int)
    return gdf


def slim_priority_habitat(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Keep the columns we use and simplify shapes to cut the file size.

    The download has no confidence column, so none is kept. area_ha is
    recomputed because the source areaha describes the unclipped polygon.
    """
    gdf = gdf[["uid", "mainhabs", "addhabs", "geometry"]].copy()
    simplified = gdf.geometry.simplify(PRIORITY_HABITAT_SIMPLIFY_M, preserve_topology=True)
    gdf = gdf.set_geometry(make_valid(simplified.values, method="structure", keep_collapsed=False))
    gdf = gdf[~gdf.geometry.is_empty]
    gdf["area_ha"] = gdf.geometry.area / 10_000
    return gdf.reset_index(drop=True)


def prepare_simple(aoi: gpd.GeoDataFrame) -> None:
    for prefix, name in SIMPLE_LAYERS.items():
        gdf = to_bng_clipped(read_in_aoi(find_raw(prefix), aoi), aoi)
        if name == "irz":
            gdf = add_irz_code_columns(gdf)
        elif name == "priority_habitat":
            gdf = slim_priority_habitat(gdf)
        save(gdf, name)


def prepare_ancient_woodland(aoi: gpd.GeoDataFrame) -> None:
    """Revised inventory within its coverage, the original inventory outside it.

    The revised inventory only covers part of the AOI (not the north-east
    corner or part of the western edge) and has no coverage boundary, so
    coverage is taken as land within AW_REVISED_COVERAGE_M of a revised wood.
    Inside it, only revised polygons are used, so original woods the revision
    dropped stay dropped.
    """
    revised = to_bng_clipped(read_in_aoi(find_raw(AW_REVISED_PREFIX), aoi), aoi)
    original = to_bng_clipped(read_in_aoi(find_raw(AW_ORIGINAL_PREFIX), aoi), aoi)

    revised.columns = [c.lower() if c != "geometry" else c for c in revised.columns]
    original.columns = [c.lower() if c != "geometry" else c for c in original.columns]
    original = original.rename(columns={"themname": "themename", "themid": "themeid"})

    coverage = revised.buffer(AW_REVISED_COVERAGE_M).union_all()
    original = original[~original.geometry.centroid.within(coverage)].copy()

    revised["source"] = "revised"
    original["source"] = "original"
    combined = pd.concat([revised, original], ignore_index=True)
    combined = gpd.GeoDataFrame(combined, geometry="geometry", crs=BNG)
    combined["area_ha"] = combined.geometry.area / 10_000
    save(combined, "ancient_woodland")


def ngr_to_easting_northing(ngr: str) -> tuple[float, float]:
    """Convert an OS grid reference such as 'TL2709096270' to metres."""
    ngr = re.sub(r"\s+", "", str(ngr)).upper()
    match = re.fullmatch(r"([A-HJ-Z]{2})(\d*)", ngr)
    if not match or len(match.group(2)) % 2:
        raise ValueError(f"Not a grid reference: {ngr!r}")
    letters, digits = match.groups()

    l1, l2 = (NGR_LETTERS.index(c) for c in letters)
    e100 = ((l1 - 2) % 5) * 5 + l2 % 5
    n100 = 19 - (l1 // 5) * 5 - l2 // 5

    half = len(digits) // 2
    scale = 10 ** (5 - half)
    e = int(digits[:half] or 0) * scale
    n = int(digits[half:] or 0) * scale
    # Grid references give the south-west corner of a square; use its centre.
    centre = scale / 2 if half < 5 else 0
    return e100 * 100_000 + e + centre, n100 * 100_000 + n + centre


def prepare_storm_overflows(aoi: gpd.GeoDataFrame) -> None:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(find_raw(EDM_PREFIX)) as zf:
        zf.extractall(tmp)
        spreadsheets = [
            p for p in Path(tmp).rglob("*.xlsx")
            if "all water and sewerage companies" in p.name.lower()
        ]
        if len(spreadsheets) != 1:
            raise FileNotFoundError(f"Expected one all-companies EDM spreadsheet, found {spreadsheets}")

        # Header row is preceded by title and note rows; find it by its first cell.
        raw = pd.read_excel(spreadsheets[0], sheet_name="All WaSC", header=None)
        header_row = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "Unique ID"][0]
        df = pd.read_excel(spreadsheets[0], sheet_name="All WaSC", header=header_row)

    df = df.dropna(axis="columns", how="all")
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    unknown = set(df.columns) - set(EDM_COLUMNS)
    if unknown:
        raise KeyError(f"EDM columns with no short name in EDM_COLUMNS: {sorted(unknown)}")
    df = df.rename(columns=EDM_COLUMNS)
    df = df[df["company"].str.strip() == "Anglian Water"].copy()

    ngr_col = "outlet_ngr"
    has_ngr = df[ngr_col].notna() & (df[ngr_col].astype(str).str.strip() != "")
    if (~has_ngr).any():
        print(f"storm_overflows: dropping {(~has_ngr).sum()} rows with no grid reference")
    df = df[has_ngr]

    coords = df[ngr_col].map(ngr_to_easting_northing)
    df["easting"] = [c[0] for c in coords]
    df["northing"] = [c[1] for c in coords]

    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["easting"], df["northing"]), crs=BNG
    )
    save(to_bng_clipped(gdf, aoi), "storm_overflows")


OUTPUTS = [
    "irz", "sssi", "sssi_units", "sac", "spa", "ramsar",
    "priority_habitat", "ancient_woodland", "storm_overflows",
]


def print_summary() -> None:
    for name in OUTPUTS:
        gdf = gpd.read_file(CLEAN_DIR / f"{name}.gpkg")
        print(f"\n== {name}.gpkg: {len(gdf)} rows")
        print("columns:", ", ".join(gdf.columns))

    irz = gpd.read_file(CLEAN_DIR / "irz.gpkg")
    print("\n== irz.gpkg categories (digit value; 0 = not triggered)")
    for col in IRZ_SUMMARY_COLUMNS:
        counts = irz[col].value_counts()
        top = ", ".join(f"{v}: {n}" for v, n in counts.head(10).items())
        print(f"{IRZ_CATEGORIES[col]} ({col}): {irz[col].nunique()} unique | {top}")


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    aoi = load_aoi()
    prepare_simple(aoi)
    prepare_ancient_woodland(aoi)
    prepare_storm_overflows(aoi)
    print_summary()


if __name__ == "__main__":
    main()
