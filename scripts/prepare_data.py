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
AW_REVISED_PREFIX = "Ancient_Woodland_Inventory_Revised_England"
AW_ORIGINAL_PREFIX = "Ancient_Woodland_England"
EDM_PREFIX = "EDM_"

# The IRZ download has no per-category columns, only a 13-digit code inside
# the irzurl field. Each digit is kept as its own column (irz_d01..irz_d13).
IRZ_CODE_LENGTH = 13

# Digit position -> development category, INFERRED from the order of the 13
# categories in Appendix 3 of data/reference/SSSI IRZ User Guidance v5.4.
# The guidance does not document the code itself, so treat as unconfirmed.
IRZ_CATEGORIES_INFERRED = {
    1: "All Consultations",
    2: "Infrastructure",
    3: "Wind & Solar Energy",
    4: "Minerals, Oil and Gas",
    5: "Rural Non Residential",
    6: "Residential",
    7: "Rural Residential",
    8: "Air Pollution",
    9: "Combustion",
    10: "Waste",
    11: "Composting",
    12: "Discharges",
    13: "Water Supply",
}
IRZ_SUMMARY_CATEGORIES = [
    "All Consultations", "Infrastructure", "Discharges", "Water Supply",
    "Combustion", "Composting", "Air Pollution", "Rural Non Residential",
]

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
    for i in range(IRZ_CODE_LENGTH):
        gdf[f"irz_d{i + 1:02d}"] = gdf["irzcode"].str[i]
    return gdf


def prepare_simple(aoi: gpd.GeoDataFrame) -> None:
    for prefix, name in SIMPLE_LAYERS.items():
        gdf = to_bng_clipped(read_in_aoi(find_raw(prefix), aoi), aoi)
        if name == "irz":
            gdf = add_irz_code_columns(gdf)
        save(gdf, name)


def prepare_ancient_woodland(aoi: gpd.GeoDataFrame) -> None:
    """Revised polygons where they exist, the original inventory elsewhere."""
    revised = to_bng_clipped(read_in_aoi(find_raw(AW_REVISED_PREFIX), aoi), aoi)
    original = to_bng_clipped(read_in_aoi(find_raw(AW_ORIGINAL_PREFIX), aoi), aoi)

    revised.columns = [c.lower() if c != "geometry" else c for c in revised.columns]
    original.columns = [c.lower() if c != "geometry" else c for c in original.columns]
    original = original.rename(columns={"themname": "themename", "themid": "themeid"})

    # Remove the revised footprint from the original polygons (overlay uses a
    # spatial index, so only overlapping pairs are compared), then drop the
    # thin slivers left where the two inventories trace the same edge.
    original = gpd.overlay(original, revised[["geometry"]], how="difference", keep_geom_type=True)
    original = original.explode(index_parts=False)
    original = original[original.geometry.area >= 100].dissolve(by="globalid", as_index=False)

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
    df = df[df["Water Company Name"].str.strip() == "Anglian Water"].copy()

    ngr_col = "Outlet Discharge NGR (EA Consents Database)"
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
    print("\n== irz.gpkg categories (digit-to-category mapping inferred, not confirmed)")
    position = {name: pos for pos, name in IRZ_CATEGORIES_INFERRED.items()}
    for category in IRZ_SUMMARY_CATEGORIES:
        col = f"irz_d{position[category]:02d}"
        counts = irz[col].value_counts()
        top = ", ".join(f"{v}: {n}" for v, n in counts.head(10).items())
        print(f"{category} ({col}): {irz[col].nunique()} unique | {top}")


def main() -> None:
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    aoi = load_aoi()
    prepare_simple(aoi)
    prepare_ancient_woodland(aoi)
    prepare_storm_overflows(aoi)
    print_summary()


if __name__ == "__main__":
    main()
