import os
import geopandas as gpd
import pandas as pd
import netCDF4
import numpy as np
import glob  # To find files matching a pattern
from dataretrieval import nwis

# Import the basin-mean time-series helper from your utilities
from forcings_utils import process_time_series


def generate_paths(hydrofabric_id):
    """
    Generate paths for preprocessing, subset, output, forcings, and NetCDF file for a given hydrofabric ID.
    """
    base_path = f"/home/jovyan/ngiab_preprocess_output/{hydrofabric_id}"
    
    # Construct file paths for the preprocessing, subset, and output files
    ngiab_prerocessing_path = base_path
    hf_subset_path = f"{base_path}/config/{hydrofabric_id}_subset.gpkg"
    ngiab_outputs_path = f"{base_path}/outputs/ngen"
    agg_csv_path = f"{base_path}/outputs/weighted_average_variables_{hydrofabric_id}.csv"

    # AORC/forcing NetCDF path (adjust if your filename differs)
    forcings_path = f"{base_path}/forcings/forcings.nc"
    
    # Use glob to get the path of any file that starts with 'troute_output' in the 'troute' directory
    troute_files = glob.glob(f"{base_path}/outputs/troute/troute_output*.nc")
    if troute_files:
        netcdf_file_path = troute_files[0]
    else:
        raise FileNotFoundError(
            f"No NetCDF file starting with 'troute_output' found in {base_path}/outputs/troute/"
        )
    
    # return forcings_path as well
    return (
        ngiab_prerocessing_path,
        hf_subset_path,
        ngiab_outputs_path,
        agg_csv_path,
        netcdf_file_path,
        forcings_path,
    )


def get_flow_data_from_netcdf(file_path, feature_id_input):
    """
    Extract 'flow' data from the NetCDF file (convert m³/s → m³/h).
    """
    with netCDF4.Dataset(file_path, 'r') as nc_file:
        feature_ids = nc_file.variables['feature_id'][:]
        flow = nc_file.variables['flow'][:]
        feature_index = np.where(feature_ids == feature_id_input)[0][0]
        flow_data = flow[feature_index, :]
        flow_data *= 3600  # m³/s → m³/h
        return flow_data


def _infer_dt_seconds_from_cat(cat_df, fallback=3600):
    """
    Infer dt (seconds) from the 'Time' column of a cat-XXXX.csv; fallback to 3600 if not possible.
    """
    try:
        t = pd.to_datetime(cat_df["Time"])
        if len(t) >= 2:
            dt = (t.iloc[1] - t.iloc[0]).total_seconds()
            if dt > 0:
                return int(dt)
    except Exception:
        pass
    return fallback


def ngen_output_analysis(hydrofabric_id, feature_id_input):
    """
    Analyze the output for the given hydrofabric ID and feature ID and write a CSV with weighted variables.
    Adds 'APCP_surface_m' from the forcings (mm → m) aligned on the 'Time' column.

    Noah-OWP variables are included with correct unit conversions:
      - QINSUR (m/s)        → m over dt
      - EVAPOTRANS (m/s)    → m over dt
      - QRAIN, QSNOW (mm/s) → m over dt
      - QSEVA (mm/s)        → m over dt
      - SNEQV, ACSNOM, CMC, SNLIQ, ECAN, ETRAN (mm) → m
      - SNOWH (m)           → m (no change)
      - FSNO (unitless fraction) → unitless (no _m suffix)
    """
    # Access paths using the generate_paths function
    (
        ngiab_prerocessing_path,
        hf_subset_path,
        ngiab_outputs_path,
        agg_csv_path,
        netcdf_file_path,
        forcings_path,
    ) = generate_paths(hydrofabric_id)

    # Step 1: Load the GeoPackage file containing the divides
    divides_gdf = gpd.read_file(hf_subset_path, layer="divides")

    # Step 2: Calculate total drainage area in square meters directly from GeoPackage file
    tot_drain_area = divides_gdf["tot_drainage_areasqkm"].max() * 1e6  # km² → m²

    # Step 3: Create a dictionary that maps divide_id to its corresponding area (km²)
    divide_areas = divides_gdf.set_index("divide_id")["areasqkm"].to_dict()

    # Step 4: Initialize an empty DataFrame to store aggregated data
    aggregated_data = pd.DataFrame()

    # Step 5: List of variables to aggregate from each cat file (existing + new)
    variables_to_aggregate = [
        # Existing
        "RAIN_RATE", "GIUH_RUNOFF", "INFILTRATION_EXCESS",
        "DIRECT_RUNOFF", "NASH_LATERAL_RUNOFF", "DEEP_GW_TO_CHANNEL_FLUX",
        "SOIL_TO_GW_FLUX", "Q_OUT", "POTENTIAL_ET", "ACTUAL_ET",
        "GW_STORAGE", "SOIL_STORAGE", "SOIL_STORAGE_CHANGE",
        "SURF_RUNOFF_SCHEME", "NWM_PONDED_DEPTH",
        # Noah-OWP
        "QINSUR",      # m/s  → m over dt
        "SNEQV",       # mm   → m
        "SNOWH",       # m    → m
        "QSNOW",       # mm/s → m over dt
        "ACSNOM",      # mm   → m
        "ECAN",        # mm   → m
        "ETRAN",       # mm   → m
        "QSEVA",       # mm/s → m over dt
        "EVAPOTRANS",  # m/s  → m over dt
        "QRAIN",       # mm/s → m over dt
        "CMC",         # mm   → m
        "SNLIQ",       # mm   → m
        "FSNO",        # unitless fraction (0–1)
    ]

    # --- Unit conversion helpers ---
    rate_mps_to_m      = lambda series, dt: series.astype(float) * float(dt)            # m/s → m over dt
    rate_mmps_to_m     = lambda series, dt: series.astype(float) * float(dt) / 1000.0   # mm/s → m over dt
    mm_to_m            = lambda series: series.astype(float) / 1000.0                   # mm → m
    identity           = lambda series: series.astype(float)                             # already meters
    unitless_identity  = lambda series: series.astype(float)                             # keep as fraction

    # Map per-variable conversion; entries not listed fall back to "as-is"
    converters = {
        # m/s → m over dt
        "QINSUR":      ("rate_mps_to_m", rate_mps_to_m),
        "EVAPOTRANS":  ("rate_mps_to_m", rate_mps_to_m),

        # mm/s → m over dt
        "QRAIN":       ("rate_mmps_to_m", rate_mmps_to_m),
        "QSNOW":       ("rate_mmps_to_m", rate_mmps_to_m),
        "QSEVA":       ("rate_mmps_to_m", rate_mmps_to_m),

        # mm → m
        "SNEQV":       ("mm_to_m", mm_to_m),
        "ACSNOM":      ("mm_to_m", mm_to_m),
        "CMC":         ("mm_to_m", mm_to_m),
        "SNLIQ":       ("mm_to_m", mm_to_m),
        "ECAN":        ("mm_to_m", mm_to_m),
        "ETRAN":       ("mm_to_m", mm_to_m),

        # already in meters
        "SNOWH":       ("identity", identity),

        # unitless fraction
        "FSNO":        ("unitless_identity", unitless_identity),
    }

    # Variables that should NOT be suffixed with "_m" (unitless)
    unitless_vars = {"FSNO"}

    def to_final_name(var_name: str) -> str:
        """Return final column name based on units (avoid _m for unitless)."""
        return var_name if var_name in unitless_vars else f"{var_name}_m"

    # Step 7: Process each CSV file in the ngen output folder
    for file_name in os.listdir(ngiab_outputs_path):
        if file_name.startswith("cat") and file_name.endswith(".csv"):
            divide_id = file_name.split(".")[0]

            if divide_id in divide_areas:
                area_km2 = divide_areas[divide_id]
                file_path = os.path.join(ngiab_outputs_path, file_name)
                cat_data = pd.read_csv(file_path)

                # infer dt (seconds) from the time column for correct rate integration
                dt_sec = _infer_dt_seconds_from_cat(cat_data, fallback=3600)

                # Retrieve the "Flow" data from the NetCDF file
                flow_data = get_flow_data_from_netcdf(netcdf_file_path, feature_id_input)

                # Align lengths
                if len(flow_data) != len(cat_data):
                    min_length = min(len(flow_data), len(cat_data))
                    cat_data = cat_data.iloc[:min_length]
                    flow_data = flow_data[:min_length]

                # Weighted (area) columns container
                weighted_columns = {}

                # Loop through each variable and compute area-weighted average (converted to meters per step where applicable)
                for var in variables_to_aggregate:
                    if var in cat_data.columns:
                        col = pd.to_numeric(cat_data[var], errors="coerce")

                        # Convert using converters where defined
                        if var in converters:
                            _, func = converters[var]
                            if func in (rate_mps_to_m, rate_mmps_to_m):
                                col_conv = func(col, dt_sec)
                            else:
                                col_conv = func(col)
                        else:
                            # keep as-is (assumed already meters-per-step like prior vars)
                            col_conv = col.astype(float)

                        # area-weighted mean depth/amount for this time step
                        weighted_value = (col_conv * area_km2) * 1_000_000 / tot_drain_area

                        # Store with final name (unit-aware)
                        weighted_columns[to_final_name(var)] = weighted_value

                # Add normalized streamflow (m/hr per unit area)
                weighted_columns["STRFLOW_m"] = flow_data / tot_drain_area

                if weighted_columns:
                    weighted_data = pd.DataFrame(weighted_columns)
                    weighted_data["Time Step"] = cat_data["Time Step"]
                    weighted_data["Time"] = cat_data["Time"]

                    # Place Time columns first
                    time_cols = ["Time Step", "Time"]
                    columns_order = time_cols + [c for c in weighted_columns.keys()]
                    weighted_data = weighted_data[columns_order]

                    if aggregated_data.empty:
                        aggregated_data = weighted_data.copy()
                    else:
                        # Sum/add (they’re already basin-weighted for each cat; if files are disjoint cats,
                        # this accumulates to basin mean due to area normalization per cat)
                        for c in [col for col in weighted_columns.keys() if col in weighted_data.columns]:
                            if c in aggregated_data.columns:
                                aggregated_data[c] += weighted_data[c]
                            else:
                                aggregated_data[c] = weighted_data[c]

                        # Ensure Time columns exist
                        for tc in time_cols:
                            if tc not in aggregated_data.columns and tc in weighted_data.columns:
                                aggregated_data[tc] = weighted_data[tc]

    # --------- Merge precipitation from forcings (APCP_surface, mm → m) ----------
    if not aggregated_data.empty:
        try:
            precip_series_mm = process_time_series(
                forcings_path,
                hf_subset_path,
                "APCP_surface",
            )
            precip_series_m = (precip_series_mm / 1000.0).rename("APCP_surface_m")

            aggregated_data["Time"] = pd.to_datetime(aggregated_data["Time"]).dt.floor("h")

            precip_df = precip_series_m.to_frame()
            precip_df.index.name = "Time"
            precip_df = precip_df.reset_index()
            precip_df["Time"] = pd.to_datetime(precip_df["Time"]).dt.floor("h")

            aggregated_data = aggregated_data.merge(precip_df, on="Time", how="left")

            # Reorder with Time columns first; keep everything else as-is
            tail_cols = ["APCP_surface_m"]  # only precipitation gets nudged to end group
            ordered_columns = (
                ["Time Step", "Time"]
                + [c for c in aggregated_data.columns if c not in (["Time Step", "Time"] + tail_cols)]
                + tail_cols
            )
            aggregated_data = aggregated_data[ordered_columns]

        except Exception as e:
            print(f"Warning: could not append precipitation column: {e}")
    # -------------------------------------------------------------------------------

    # Step 18: Save or display the final aggregated data
    if not aggregated_data.empty:
        ordered_columns = ["Time Step", "Time"] + [col for col in aggregated_data.columns if col not in ["Time Step", "Time"]]
        aggregated_data = aggregated_data[ordered_columns]
        aggregated_data.reset_index(drop=True, inplace=True)
        
        os.makedirs(os.path.dirname(agg_csv_path), exist_ok=True)
        aggregated_data.to_csv(agg_csv_path, index=False)
        print(f"CSV file created at: {agg_csv_path}")
    else:
        print("No data processed or no matching cat files found.")
    return aggregated_data


# Process Observed USGS Streamflow Data
def process_usgs_streamflow(site, start, end, hydrofabric_path):
    start = pd.to_datetime(start) - pd.Timedelta(days=1)
    end = pd.to_datetime(end) + pd.Timedelta(days=1)
    adjusted_start = start.strftime('%Y-%m-%d')
    adjusted_end = end.strftime('%Y-%m-%d')
    dfo_usgs = nwis.get_record(sites=site, service='iv', start=adjusted_start, end=adjusted_end)
    dfo_usgs.index = pd.to_datetime(dfo_usgs.index)
    dfo_usgs['Time'] = dfo_usgs.index.floor('h')
    dfo_usgs['00060'] = pd.to_numeric(dfo_usgs['00060'], errors='coerce')
    dfo_usgs_hr = dfo_usgs.groupby('Time')['00060'].mean().reset_index()
    dfo_usgs_hr['Streamflow (ft³/sec)'] = dfo_usgs_hr['00060'] / 35.3147
    dfo_usgs_hr['Streamflow (m³/hr)'] = dfo_usgs_hr['00060']*3600 / 35.3147

    divides_gdf = gpd.read_file(hydrofabric_path, layer="divides")
    tot_drain_area = divides_gdf["tot_drainage_areasqkm"].max() * 1e6  # km² → m²
    dfo_usgs_hr['Streamflow (m/hr)'] = dfo_usgs_hr['00060']*3600 / (35.3147*tot_drain_area)
    return dfo_usgs_hr
