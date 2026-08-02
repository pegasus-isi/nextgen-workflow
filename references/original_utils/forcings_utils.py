from __future__ import annotations

# --- Ensure a valid working directory BEFORE importing geopandas/rtree ---
import os
SAFE_CWD = "/home/jovyan"
try:
    _ = os.getcwd()
except FileNotFoundError:
    os.chdir(SAFE_CWD)

# =============================================================================
# Quiet mode (suppress noisy libs)
# =============================================================================
import logging, warnings, numpy as np
os.environ.setdefault("CPL_DEBUG", "OFF")
os.environ.setdefault("CPL_LOG", "/dev/null")
os.environ.setdefault("PROJ_LOG_LEVEL", "OFF")
os.environ.setdefault("PROJ_DEBUG", "0")
for _name in ("rasterio","fiona","fiona.ogrext","gdal","osgeo","pyproj"):
    logging.getLogger(_name).setLevel(logging.ERROR)
np.seterr(all="ignore")
warnings.filterwarnings("ignore")
try:
    from osgeo import gdal  # noqa
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    gdal.UseExceptions(False)
except Exception:
    pass

# =============================================================================
# Imports
# =============================================================================
from typing import Optional, Tuple, Dict, Iterable, List
import numpy as np
import pandas as pd
import xarray as xr
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import rioxarray as rxr  # noqa

# =============================================================================
# Shared constants & metadata
# =============================================================================
DEFAULT_LAYER_NAME = "divides"
DEFAULT_ID_FIELD   = "divide_id"
YEAR_DAYS          = 365.2425

VAR_METADATA: Dict[str, Dict[str, str]] = {
    "APCP_surface": {
        "label": "Total Precipitation (mm/hr)",
        "full_name": "Total Precipitation",
        "units": "mm/hr",
    },
    "DLWRF_surface": {
        "label": "Downward Longwave Radiation Flux at the Surface (W/m²)",
        "full_name": "Downward Longwave Radiation Flux at the Surface",
        "units": "W/m²",
    },
    "PRES_surface": {
        "label": "Air Pressure at the Surface (Pa)",
        "full_name": "Air Pressure at the Surface",
        "units": "Pa",
    },
    "SPFH_2maboveground": {
        "label": "Specific Humidity at 2 m AGL (kg/kg)",
        "full_name": "Specific Humidity at 2 meters Above Ground Level",
        "units": "kg/kg",
    },
    "precip_rate": {
        "label": "Precipitation Rate (mm/hr)",
        "full_name": "Precipitation Rate",
        "units": "mm/hr",  # (converted from mm/s where needed)
    },
    "DSWRF_surface": {
        "label": "Downward Shortwave Radiation Flux at the Surface (W/m²)",
        "full_name": "Downward Shortwave Radiation Flux at the Surface",
        "units": "W/m²",
    },
    "TMP_2maboveground": {
        "label": "Temperature at 2 m AGL (K)",
        "full_name": "Temperature at 2 meters Above Ground Level",
        "units": "K",
    },
    "UGRD_10maboveground": {
        "label": "Eastward (U) Wind at 10 m AGL (m/hr)",
        "full_name": "Eastward (U) Wind at 10 meters Above Ground Level",
        "units": "m/hr",  # (converted from m/s)
    },
    "VGRD_10maboveground": {
        "label": "Northward (V) Wind at 10 m AGL (m/hr)",
        "full_name": "Northward (V) Wind at 10 meters Above Ground Level",
        "units": "m/hr",  # (converted from m/s)
    },
}

def get_var_label(var_name: str) -> str:
    return VAR_METADATA.get(var_name, {}).get("label", "")

def get_var_full_name(var_name: str) -> str:
    return VAR_METADATA.get(var_name, {}).get("full_name", var_name)

def get_var_units(var_name: str) -> str:
    return VAR_METADATA.get(var_name, {}).get("units", "")

def list_supported_variables() -> List[str]:
    return list(VAR_METADATA.keys())

# =============================================================================
# AORC/NextGen time-series helpers (basin mean)
# =============================================================================
def load_data(forcing_path: str, geopkg_path: str,
              divides_layer: str = DEFAULT_LAYER_NAME) -> Tuple[xr.Dataset, gpd.GeoDataFrame]:
    ds = xr.open_dataset(forcing_path)  # CF decode when possible
    divides_gdf = gpd.read_file(geopkg_path, layer=divides_layer)
    return ds, divides_gdf

_CATCHMENT_ID_FIELDS = ["divide_id", "id", "ids", "feature_id", "featureid", "catchment_id"]
_TOTAL_AREA_FIELDS   = ["tot_drainage_areasqkm", "total_area_km2", "tot_area_sqkm"]
_AREA_FIELD_CANDIDATES = ["areasqkm", "area_km2", "areasq_km"]
_TIME_DIM_CANDIDATES = ["time", "Time", "t"]

def _pick_first_present(gdf: gpd.GeoDataFrame, candidates: Iterable[str]) -> Optional[str]:
    for c in candidates:
        if c in gdf.columns:
            return c
    return None

def _pick_first_present_da(ds: xr.Dataset, candidates: Iterable[str]) -> Optional[str]:
    for c in candidates:
        if c in ds:
            return c
    return None

def _get_catchment_id_da(ds: xr.Dataset) -> xr.DataArray:
    id_name = _pick_first_present_da(ds, ["ids", "feature_id", "catchment_id", "features"])
    if id_name is None:
        raise ValueError("Unable to find catchment ID array in dataset. "
                         "Tried ['ids','feature_id','catchment_id','features'].")
    return ds[id_name]

def get_catchment_area_info(
    ds: xr.Dataset,
    divides_gdf: gpd.GeoDataFrame
) -> Tuple[Dict[str, float], List[float], float, str]:
    id_da = _get_catchment_id_da(ds)
    catch_dim = id_da.dims[0]
    catchment_ids_ds = id_da.values

    gdf_id_col = _pick_first_present(divides_gdf, _CATCHMENT_ID_FIELDS)
    if gdf_id_col is None:
        raise ValueError(f"Could not find a catchment id column in divides_gdf. Tried: {_CATCHMENT_ID_FIELDS}")

    area_col = _pick_first_present(divides_gdf, _AREA_FIELD_CANDIDATES)
    if area_col is None:
        raise ValueError(f"Could not find an area column in divides_gdf. Tried: {_AREA_FIELD_CANDIDATES}")

    total_area_col = _pick_first_present(divides_gdf, _TOTAL_AREA_FIELDS)
    if total_area_col is None:
        warnings.warn("Total drainage area column not found; computing sum of areas instead.")
        tot_drain_area = float(divides_gdf[area_col].sum())
    else:
        tot_drain_area = float(divides_gdf[total_area_col].max())

    catchment_area_dict = divides_gdf.set_index(gdf_id_col)[area_col].astype(float).to_dict()
    catchment_areas = [float(catchment_area_dict.get(str(cid), 0.0)) for cid in catchment_ids_ds]

    return catchment_area_dict, catchment_areas, tot_drain_area, catch_dim

def _to_datetime_index(time_da: xr.DataArray) -> pd.Index:
    try:
        idx = time_da.to_index()
    except Exception:
        idx = None
    if isinstance(idx, pd.DatetimeIndex):
        return idx

    try:
        from xarray.coding.cftimeindex import CFTimeIndex
        if isinstance(idx, CFTimeIndex):
            try:
                return idx.to_datetimeindex()
            except Exception:
                return pd.Index(idx.to_pydatetime())
    except Exception:
        pass

    vals = time_da.values
    units = time_da.attrs.get("units")
    calendar = time_da.attrs.get("calendar", "standard")
    if units is not None:
        import cftime
        dt = cftime.num2date(vals, units=units, calendar=calendar)
        return pd.Index([pd.to_datetime(d).to_pydatetime() for d in dt])
    return pd.to_datetime(vals, unit="s", origin="unix")

def _time_index_from_2d_Time(ds: xr.Dataset, time_dim_name: str) -> Optional[pd.DatetimeIndex]:
    if "Time" not in ds or ds["Time"].ndim != 2:
        return None
    time_da = ds["Time"]
    dims = list(time_da.dims)
    if time_dim_name not in dims:
        return None
    other_dims = [d for d in dims if d != time_dim_name]
    if len(other_dims) != 1:
        return None
    catch_dim_guess = other_dims[0]
    seconds = time_da.isel({catch_dim_guess: 0}).values
    units = str(time_da.attrs.get("units", "s")).lower()
    if units in ("s", "sec", "seconds"):
        return pd.to_datetime(seconds, unit="s", origin="unix")
    return pd.to_datetime(seconds, unit="s", origin="unix")

def apply_transformation(var_values: np.ndarray, var_name: str) -> np.ndarray:
    if var_name == "precip_rate":
        return var_values * 3600.0  # mm/s -> mm/hr
    elif var_name in ("UGRD_10maboveground", "VGRD_10maboveground"):
        return var_values * 3600.0  # m/s -> m/hr
    else:
        return var_values

def validate_variables(ds: xr.Dataset, required_vars: Iterable[str]) -> List[str]:
    return [v for v in required_vars if v not in ds.variables]

def process_time_series(
    forcing_path: str,
    geopkg_path: str,
    var_name: str
) -> pd.Series:
    divides_layer = DEFAULT_LAYER_NAME
    ds, divides_gdf = load_data(forcing_path, geopkg_path, divides_layer=divides_layer)

    if var_name not in ds.variables:
        available = ", ".join(list(ds.variables))
        raise KeyError(f"Variable '{var_name}' not found in dataset. Available: {available}")

    _, catchment_areas, tot_drain_area, catch_dim = get_catchment_area_info(ds, divides_gdf)

    var_da = ds[var_name]
    time_dim_name = None
    for cand in _TIME_DIM_CANDIDATES:
        if cand in var_da.dims:
            time_dim_name = cand
            break
    if time_dim_name is None:
        for d in var_da.dims:
            coord = ds.coords.get(d)
            if coord is not None and np.issubdtype(coord.dtype, np.datetime64):
                time_dim_name = d
                break
    if time_dim_name is None:
        raise ValueError(f"Could not identify a time dimension in variable '{var_name}'.")

    time_index = _time_index_from_2d_Time(ds, time_dim_name)
    if time_index is None:
        time_coord = var_da.coords.get(time_dim_name) or ds[time_dim_name]
        time_index = _to_datetime_index(time_coord)

    if catch_dim not in var_da.dims:
        for alt in ["ids", "feature_id", "catchment_id", "features"]:
            if alt in var_da.dims:
                catch_dim = alt
                break
        if catch_dim not in var_da.dims:
            other_dims = [d for d in var_da.dims if d != time_dim_name]
            if len(other_dims) == 1:
                catch_dim = other_dims[0]
            else:
                raise ValueError(
                    f"Could not identify catchment dimension for '{var_name}'. "
                    f"Found dims: {var_da.dims}, time dim: {time_dim_name}"
                )

    var_2d = var_da.transpose(catch_dim, time_dim_name).values
    var_2d = apply_transformation(var_2d, var_name)

    catchment_areas_arr = np.asarray(catchment_areas, dtype=float)
    if float(tot_drain_area) <= 0.0:
        raise ValueError("Total drainage area must be positive.")

    area_weighted = (var_2d * catchment_areas_arr[:, None]).sum(axis=0) / float(tot_drain_area)

    if len(area_weighted) != len(time_index):
        raise ValueError(
            f"Time length mismatch after alignment: values={len(area_weighted)} vs index={len(time_index)}. "
            f"Var dims={tuple(var_da.dims)}; time dim='{time_dim_name}'."
        )

    series = pd.Series(area_weighted, index=time_index).sort_index()
    return series

# =============================================================================
# Catchment-wise mean-annual stats (for datasets with 2D Time etc.)
# =============================================================================
def _normalize_units(units: Optional[str]) -> str:
    if not units:
        return ""
    return units.lower().replace("−", "-").strip()

def _detect_var_kind_and_rate_units(var_name: str, units_str: str) -> Tuple[str, Optional[str]]:
    name = (var_name or "").lower()
    u = _normalize_units(units_str)
    if ("mm h^-1" in u) or ("mm h-1" in u) or ("mm/hr" in u) or (name == "apcp_surface"):
        return "rate", "per_hour"
    if ("mm s^-1" in u) or ("mm s-1" in u) or ("mm/s" in u) or (name == "precip_rate"):
        return "rate", "per_second"
    if ("precip" in name or "apcp" in name or "rain" in name) and ("/h" in u or "h-1" in u):
        return "rate", "per_hour"
    if ("precip" in name or "rain" in name) and ("/s" in u or "s-1" in u):
        return "rate", "per_second"
    return "state", None

def _step_hours_from_seconds(time_seconds_da: xr.DataArray) -> xr.DataArray:
    dt_sec = time_seconds_da.diff("time")
    if dt_sec.sizes.get("time", 0) == 0:
        pad = xr.full_like(time_seconds_da.isel(time=0), np.nan)
        dt_sec_aligned = pad
    else:
        pad = dt_sec.isel(time=-1)
        dt_sec_aligned = xr.concat([dt_sec, pad], dim="time")
    return dt_sec_aligned.astype("float64") / 3600.0

def _step_seconds_from_seconds(time_seconds_da: xr.DataArray) -> xr.DataArray:
    dt_sec = time_seconds_da.diff("time")
    if dt_sec.sizes.get("time", 0) == 0:
        pad = xr.full_like(time_seconds_da.isel(time=0), np.nan)
        dt_sec_aligned = pad
    else:
        pad = dt_sec.isel(time=-1)
        dt_sec_aligned = xr.concat([dt_sec, pad], dim="time")
    return dt_sec_aligned.astype("float64")

def compute_mean_annual(
    ds: xr.Dataset,
    var_name: str,
    *,
    force_kind: Optional[str] = None,
    hours_per_year: float = 24.0 * YEAR_DAYS,
) -> Tuple[pd.DataFrame, Dict]:
    if "catchment-id" in ds.dims and "catchment" not in ds.dims:
        ds = ds.rename({"catchment-id": "catchment"})

    required = ["Time", "ids", var_name]
    for v in required:
        if v not in ds:
            raise ValueError(f"Variable '{v}' not found in dataset.")

    da = ds[var_name].transpose("catchment", "time")
    time_sec = ds["Time"].transpose("catchment", "time")

    dt_hours   = _step_hours_from_seconds(time_sec)
    dt_seconds = _step_seconds_from_seconds(time_sec)
    valid = np.isfinite(da) & np.isfinite(dt_hours) & np.isfinite(dt_seconds)

    covered_hours = (dt_hours.where(valid, 0.0)).sum("time")
    total_years = (covered_hours / hours_per_year).rename("total_years")

    units = _normalize_units(da.attrs.get("units"))
    if force_kind in {"rate", "state"}:
        kind = force_kind
        rate_base = None
        if kind == "rate":
            _, rate_base = _detect_var_kind_and_rate_units(var_name, units)
            if rate_base is None:
                rate_base = "per_hour"
    else:
        kind, rate_base = _detect_var_kind_and_rate_units(var_name, units)

    if kind == "rate":
        if rate_base == "per_second":
            step_amount = da.where(valid, 0.0) * dt_seconds.where(valid, 0.0)
        else:
            step_amount = da.where(valid, 0.0) * dt_hours.where(valid, 0.0)
        total_amount = step_amount.sum("time").rename("total_amount")
        mean_annual = xr.where(total_years > 0, total_amount / total_years, np.nan).rename("mean_annual")
    else:
        weighted_sum = da.where(valid, 0.0) * dt_hours.where(valid, 0.0)
        mean_annual = xr.where(covered_hours > 0, weighted_sum.sum("time") / covered_hours, np.nan).rename("mean_annual")
        total_amount = xr.full_like(mean_annual, np.nan).rename("total_amount")

    nc_ids = pd.Index(ds["ids"].astype(str).values, name="catchment_id")
    out_df = pd.DataFrame({
        "catchment_id": nc_ids.values,
        "mean_annual": mean_annual.values,
        "total_amount": total_amount.values,
        "total_years": total_years.values,
        "units": [units] * len(nc_ids),
        "var_name": [var_name] * len(nc_ids),
        "kind": [kind] * len(nc_ids),
    })
    meta = dict(units=units, kind=kind, hours_per_year=hours_per_year, rate_base=rate_base)
    return out_df, meta

# =============================================================================
# Vector outputs and plotting (catchment choropleth)
# =============================================================================
def join_to_gpkg(
    gpkg_path: str,
    stats_df: pd.DataFrame,
    layer_name: str = DEFAULT_LAYER_NAME,
    id_field: str = DEFAULT_ID_FIELD,
) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(gpkg_path, layer=layer_name)
    gdf[id_field] = gdf[id_field].astype(str)
    gdf_map = gdf.merge(stats_df, left_on=id_field, right_on="catchment_id", how="left")
    return gdf_map

def save_csv_and_gpkg(
    stats_df: pd.DataFrame,
    gpkg_path: str,
    var_name: str,
    *,
    layer_name: str = DEFAULT_LAYER_NAME,
    id_field: str = DEFAULT_ID_FIELD,
    csv_prefix: str = "MeanAnnual",
    gpkg_prefix: str = "catchments_with_MeanAnnual",
) -> Tuple[str, str]:
    csv_name = f"{csv_prefix}_{var_name}_by_catchment.csv"
    stats_df.to_csv(csv_name, index=False)
    gdf_map = join_to_gpkg(gpkg_path, stats_df, layer_name=layer_name, id_field=id_field)
    gpkg_name = f"{gpkg_prefix}_{var_name}.gpkg"
    gdf_map.to_file(gpkg_name, layer=layer_name, driver="GPKG")
    return csv_name, gpkg_name

def plot_mean_annual_map(
    gdf_map: gpd.GeoDataFrame,
    value_col: str = "mean_annual",
    *,
    title: str = "Mean Annual",
    units: str = "",
    cmap: str = "viridis_r",
    annotate: bool = True,
    figsize: Tuple[int, int] = (10, 8),
) -> None:
    fig, ax = plt.subplots(figsize=figsize)
    gdf_map.plot(
        column=value_col,
        ax=ax,
        legend=True,
        cmap=cmap,
        edgecolor="0.6",
        linewidth=0.6,
        missing_kwds={"color":"lightgray","edgecolor":"0.7","hatch":"///","label":"No data"},
    )
    units_txt = f" [{units}]" if units else ""
    ax.set_title(f"{title}{units_txt}", fontsize=14)
    ax.set_axis_off()

    if annotate and value_col in gdf_map:
        centroids = gdf_map.geometry.representative_point()
        for xy, val in zip(centroids, gdf_map[value_col]):
            if pd.notna(val):
                ax.text(xy.x, xy.y, f"{val:.0f}", fontsize=7, ha="center", va="center")

    try:
        cb = ax.get_figure().axes[-1]
        cb.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: f"{int(v):d}"))
    except Exception:
        pass

    plt.tight_layout()
    plt.show()

# =============================================================================
# Grid-based mean-annual raster over HydroFabric (single-figure helper)
# =============================================================================
def _decode_hours_since(ds: xr.Dataset) -> xr.Dataset:
    if "time" not in ds:
        raise ValueError("Dataset has no 'time' coordinate.")
    units = ds["time"].attrs.get("units", "")
    if isinstance(units, str) and units.startswith("hours since "):
        ref = pd.to_datetime(units.replace("hours since ", ""), utc=True)
        time = ref + pd.to_timedelta(ds["time"].values.astype(float), unit="h")
        ds = ds.assign_coords(time=("time", pd.DatetimeIndex(time).tz_localize(None)))
        return ds
    return xr.decode_cf(ds)

def _dt_seconds_from_timeindex(time_index: pd.DatetimeIndex) -> np.ndarray:
    dts = pd.Series(time_index).diff().dt.total_seconds().to_numpy()
    if dts.size == 0:
        return np.array([0.0])
    if np.isnan(dts[0]):
        dts[0] = np.nanmedian(dts[1:]) if dts.size > 1 else 0.0
    if np.isnan(dts).any():
        fill = np.nanmedian(dts)
        dts = np.where(np.isnan(dts), fill, dts)
    return dts

def _is_precip_mm_per_s(da: xr.DataArray) -> bool:
    units = (da.attrs.get("units") or "").lower().strip()
    name  = (da.name or "").lower()
    return (name == "precip_rate") or (units in ("mm/s", "mm s-1", "mm s^-1"))

def _mean_annual_over_period_grid(da: xr.DataArray) -> xr.DataArray:
    if not all(d in da.dims for d in ("time","y","x")):
        raise ValueError(f"Expected dims (time,y,x); found {da.dims}")
    da = da.transpose("time", "y", "x")

    t = pd.DatetimeIndex(da["time"].values)
    dt_sec = _dt_seconds_from_timeindex(t)
    dt = xr.DataArray(dt_sec, coords={"time": da["time"]}, dims=("time",))
    dt3 = dt.broadcast_like(da)

    if _is_precip_mm_per_s(da):
        rate_hr = da * 3600.0
        dt_hr   = dt3 / 3600.0
        amount_mm = (rate_hr * dt_hr).sum("time")
        years     = (dt.sum("time") / 3600.0) / (YEAR_DAYS * 24.0)
        out = xr.where(years > 0, amount_mm / years, np.nan)
        out = out.rename("mean_annual_mm_per_yr")
        out.attrs["units"] = "mm/yr"
        out.attrs["note"]  = "precip_rate mm/s → mm/hr; integrate; divide by fractional years"
        return out

    num = (da * dt3).sum("time")
    den = dt.sum("time").broadcast_like(num)
    out = xr.where(den > 0, num / den, np.nan).rename("time_weighted_mean")
    out.attrs["units"] = da.attrs.get("units", "")
    out.attrs["note"]  = "time-weighted mean over full period"
    return out


def make_mean_annual_map(
    nc_path: str,
    gpkg_path: str,
    var_name: str,
    *,
    annotate: bool = True,
    label_decimals: int = 0,
    cmap: str = "viridis_r",
    figsize: tuple[int,int] = (9, 7),
    save_outputs: bool = False,
    out_dir: str | None = None,
) -> tuple[plt.Figure, gpd.GeoDataFrame, xr.DataArray]:
    ds = xr.open_dataset(nc_path, decode_times=False)
    ds = _decode_hours_since(ds)

    if var_name not in ds:
        raise KeyError(f"Variable '{var_name}' not found. Available: {list(ds.data_vars)}")

    da = ds[var_name]
    if not all(d in da.dims for d in ("time","y","x")):
        if all(d in da.dims for d in ("time","lat","lon")):
            da = da.rename({"lat":"y","lon":"x"}).transpose("time","y","x")
        else:
            raise ValueError(f"Variable '{var_name}' must be on a (time,y,x) grid; found dims {da.dims}")

    m = _mean_annual_over_period_grid(da)
    m = m.rio.set_spatial_dims(x_dim="x", y_dim="y", inplace=False)

    proj4 = None
    for v in ds.data_vars:
        if "proj4" in ds[v].attrs:
            proj4 = ds[v].attrs["proj4"]; break

    crs_ok = False
    if proj4:
        try:
            import pyproj
            crs_obj = pyproj.CRS.from_proj4(proj4)
            m = m.rio.write_crs(crs_obj.to_wkt("WKT2_2019"), inplace=False)
            crs_ok = True
        except Exception:
            crs_ok = False

    gdf = gpd.read_file(gpkg_path, layer=DEFAULT_LAYER_NAME)
    if DEFAULT_ID_FIELD not in gdf.columns:
        gdf = gdf.rename(columns={gdf.columns[0]: DEFAULT_ID_FIELD})
    gdf[DEFAULT_ID_FIELD] = gdf[DEFAULT_ID_FIELD].astype(str)

    if not crs_ok:
        target_crs = "EPSG:4326"
        m = m.rio.reproject(target_crs)
        gdf = gdf.to_crs(target_crs)
    else:
        gdf = gdf.to_crs(m.rio.crs)

    vals = []
    for _, row in gdf.iterrows():
        try:
            clipped = m.rio.clip([row.geometry], drop=True)
            vals.append(float(clipped.mean().values))
        except Exception:
            vals.append(np.nan)
    gdf = gdf.copy()
    gdf["mean_value"] = vals

    # ---- Prevent auto-display inside Jupyter ----
    with plt.ioff():
        fig, ax = plt.subplots(figsize=figsize)

        im = m.plot(ax=ax, cmap=cmap, add_colorbar=False)
        cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
        units = m.attrs.get("units", "")
        if units:
            cbar.set_label(units, fontsize=11)

        gdf.boundary.plot(ax=ax, linewidth=1.2, edgecolor="black")

        if annotate:
            for _, r in gdf.iterrows():
                try:
                    x, y = r.geometry.representative_point().coords[0]
                    v = r["mean_value"]
                    if np.isfinite(v):
                        txt = f"{int(np.rint(v))}" if label_decimals <= 0 else f"{v:.{label_decimals}f}".rstrip("0").rstrip(".")
                        ax.text(x, y, txt, fontsize=8.5, ha="center", va="center", color="black")
                except Exception:
                    pass

        title = f"Mean Annual {get_var_full_name(var_name)}"
        if _is_precip_mm_per_s(da):
            title = "Mean Annual Precipitation (mm/yr)"
        ax.set_title(title, fontsize=13)

        ax.set_xlabel(""); ax.set_ylabel("")
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xticklabels([]); ax.set_yticklabels([])

        plt.tight_layout()

    # Optional exports (unchanged) ...
    if save_outputs:
        import pathlib
        out_root = pathlib.Path(out_dir or ".")
        out_root.mkdir(parents=True, exist_ok=True)
        (out_root / "mean_annual_map.png").write_bytes(fig_to_bytes(fig))
        try:
            m.rio.to_raster((out_root / "mean_annual_map.tif").as_posix())
        except Exception:
            pass
        try:
            gdf[[DEFAULT_ID_FIELD, "mean_value"]].to_csv((out_root / "divide_means.csv").as_posix(), index=False)
        except Exception:
            pass
        try:
            gdf.to_file((out_root / "divides_with_mean_annual.gpkg").as_posix(),
                        layer=DEFAULT_LAYER_NAME, driver="GPKG")
        except Exception:
            pass

    # Return without plt.show() — only one display when you evaluate `fig`
    return fig, gdf, m

# def make_mean_annual_map(
#     nc_path: str,
#     gpkg_path: str,
#     var_name: str,
#     *,
#     annotate: bool = True,
#     label_decimals: int = 0,
#     cmap: str = "viridis_r",
#     figsize: tuple[int,int] = (9, 7),
#     save_outputs: bool = False,
#     out_dir: str | None = None,
# ) -> tuple[plt.Figure, gpd.GeoDataFrame, xr.DataArray]:
#     ds = xr.open_dataset(nc_path, decode_times=False)
#     ds = _decode_hours_since(ds)

#     if var_name not in ds:
#         raise KeyError(f"Variable '{var_name}' not found. Available: {list(ds.data_vars)}")

#     da = ds[var_name]
#     if not all(d in da.dims for d in ("time","y","x")):
#         if all(d in da.dims for d in ("time","lat","lon")):
#             da = da.rename({"lat":"y","lon":"x"}).transpose("time","y","x")
#         else:
#             raise ValueError(f"Variable '{var_name}' must be on a (time,y,x) grid; found dims {da.dims}")

#     m = _mean_annual_over_period_grid(da)
#     m = m.rio.set_spatial_dims(x_dim="x", y_dim="y", inplace=False)

#     proj4 = None
#     for v in ds.data_vars:
#         if "proj4" in ds[v].attrs:
#             proj4 = ds[v].attrs["proj4"]; break

#     crs_ok = False
#     if proj4:
#         try:
#             import pyproj
#             crs_obj = pyproj.CRS.from_proj4(proj4)
#             m = m.rio.write_crs(crs_obj.to_wkt("WKT2_2019"), inplace=False)
#             crs_ok = True
#         except Exception:
#             crs_ok = False

#     gdf = gpd.read_file(gpkg_path, layer=DEFAULT_LAYER_NAME)
#     if DEFAULT_ID_FIELD not in gdf.columns:
#         gdf = gdf.rename(columns={gdf.columns[0]: DEFAULT_ID_FIELD})
#     gdf[DEFAULT_ID_FIELD] = gdf[DEFAULT_ID_FIELD].astype(str)

#     if not crs_ok:
#         target_crs = "EPSG:4326"
#         m = m.rio.reproject(target_crs)
#         gdf = gdf.to_crs(target_crs)
#     else:
#         gdf = gdf.to_crs(m.rio.crs)

#     vals = []
#     for _, row in gdf.iterrows():
#         try:
#             clipped = m.rio.clip([row.geometry], drop=True)
#             vals.append(float(clipped.mean().values))
#         except Exception:
#             vals.append(np.nan)
#     gdf = gdf.copy()
#     gdf["mean_value"] = vals

#     fig, ax = plt.subplots(figsize=figsize)
#     im = m.plot(ax=ax, cmap=cmap, add_colorbar=False)
#     cbar = plt.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
#     units = m.attrs.get("units", "")
#     if units:
#         cbar.set_label(units, fontsize=11)

#     gdf.boundary.plot(ax=ax, linewidth=1.2, edgecolor="black")

#     if annotate:
#         for _, r in gdf.iterrows():
#             try:
#                 x, y = r.geometry.representative_point().coords[0]
#                 v = r["mean_value"]
#                 if np.isfinite(v):
#                     txt = f"{int(np.rint(v))}" if label_decimals <= 0 else f"{v:.{label_decimals}f}".rstrip("0").rstrip(".")
#                     ax.text(x, y, txt, fontsize=8.5, ha="center", va="center", color="black")
#             except Exception:
#                 pass

#     title = f"Mean Annual {get_var_full_name(var_name)}"
#     if _is_precip_mm_per_s(da):
#         title = "Mean Annual Precipitation (mm/yr)"
#     ax.set_title(title, fontsize=13)

#     ax.set_xlabel(""); ax.set_ylabel("")
#     ax.set_xticks([]); ax.set_yticks([])
#     ax.set_xticklabels([]); ax.set_yticklabels([])

#     plt.tight_layout()

#     if save_outputs:
#         import pathlib
#         out_root = pathlib.Path(out_dir or ".")
#         out_root.mkdir(parents=True, exist_ok=True)
#         (out_root / "mean_annual_map.png").write_bytes(fig_to_bytes(fig))
#         try:
#             m.rio.to_raster((out_root / "mean_annual_map.tif").as_posix())
#         except Exception:
#             pass
#         try:
#             gdf[[DEFAULT_ID_FIELD, "mean_value"]].to_csv((out_root / "divide_means.csv").as_posix(), index=False)
#         except Exception:
#             pass
#         try:
#             gdf.to_file((out_root / "divides_with_mean_annual.gpkg").as_posix(),
#                         layer=DEFAULT_LAYER_NAME, driver="GPKG")
#         except Exception:
#             pass

#     # Return without plt.show(): evaluating `fig` in a notebook shows exactly ONE figure
#     return fig, gdf, m

def fig_to_bytes(fig: plt.Figure) -> bytes:
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight")
    return buf.getvalue()
