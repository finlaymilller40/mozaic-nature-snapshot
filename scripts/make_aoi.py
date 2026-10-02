"""Build the Cambridgeshire area of interest (AOI) as zipped shapefiles.

Writes to data/aoi/:
  cambs_aoi.zip        - rectangle in British National Grid (EPSG:27700)
  cambs_aoi_wgs84.zip  - the same area in WGS84 (EPSG:4326)
"""

import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

AOI_DIR = Path(__file__).resolve().parent.parent / "data" / "aoi"

# British National Grid extent in metres
MIN_E, MAX_E = 488_000, 585_000
MIN_N, MAX_N = 225_000, 325_000

SHAPEFILE_PARTS = (".shp", ".shx", ".dbf", ".prj")


def write_zipped_shapefile(gdf: gpd.GeoDataFrame, name: str) -> Path:
    shp_path = AOI_DIR / f"{name}.shp"
    gdf.to_file(shp_path, driver="ESRI Shapefile")

    zip_path = AOI_DIR / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for ext in SHAPEFILE_PARTS:
            part = shp_path.with_suffix(ext)
            zf.write(part, arcname=part.name)
    return zip_path


def main() -> None:
    AOI_DIR.mkdir(parents=True, exist_ok=True)

    aoi_bng = gpd.GeoDataFrame(
        {"name": ["cambs_aoi"]},
        geometry=[box(MIN_E, MIN_N, MAX_E, MAX_N)],
        crs="EPSG:27700",
    )
    write_zipped_shapefile(aoi_bng, "cambs_aoi")

    # Add vertices every 1 km before reprojecting so the edges follow the
    # true BNG rectangle rather than straight lines between four corners.
    aoi_wgs84 = aoi_bng.set_geometry(aoi_bng.segmentize(1_000)).to_crs("EPSG:4326")
    write_zipped_shapefile(aoi_wgs84, "cambs_aoi_wgs84")


if __name__ == "__main__":
    main()
