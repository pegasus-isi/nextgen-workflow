import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from pyngiab import PyNGIAB

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import spotpy
import xarray as xr
from dataretrieval import nwis
from spotpy.parameter import Uniform


sys.path.append("/ngen/pyngiab")

def update_model_params_from_csv(model_module, csv_to_json_map, best_row, model_label):
    """
    Update only parameters that:
      - already exist in realization.json model_params, and
      - have a matching column in the calibration CSV.
    """
    current_params = model_module["params"].get("model_params", {})
    if not current_params:
        print(f"{model_label}: no model_params in realization — nothing to update.")
        return 0

    print(f"\n{model_label} — parameters found in realization.json:")
    print(" ", list(current_params.keys()))

    updated_count = 0
    skipped = []

    for json_name in current_params.keys():
        csv_column = csv_to_json_map.get(json_name, json_name)

        if csv_column not in best_row.index:
            skipped.append(f"{json_name} (no CSV column '{csv_column}')")
            continue

        old_value = current_params[json_name]
        new_value = float(best_row[csv_column])
        current_params[json_name] = new_value
        updated_count += 1
        print(f"  {json_name}: {old_value} → {new_value}")

    model_module["params"]["model_params"] = current_params

    if skipped:
        print(f"{model_label} — skipped:")
        for line in skipped:
            print(" ", line)

    print(f"{model_label}: updated {updated_count} parameter(s).")
    return updated_count


def get_troute_output_name(path):
    """
    Reads the realization.json file and returns the expected t-route output filename.
    """
    with open(path, "r") as file:
        realization = json.load(file)
    start_date = datetime.strptime(realization["time"]["start_time"], "%Y-%m-%d %H:%M:%S")
    return f"troute_output_{start_date.strftime('%Y%m%d%H%M')}.nc"

def update_parameters(file_path, param_updates, model_type_name):
    with open(file_path, "r") as f:
        realization = json.load(f)
    models = realization["global"]["formulations"][0]["params"]["modules"]
    for model in models:
        if model["params"]["model_type_name"] == model_type_name:
            model["params"]["model_params"] = param_updates
            break
    with open(file_path, "w") as f:
        json.dump(realization, f, indent=4)


def update_snow_emis(value):
    """
    Update selected NOAH LSM parameters in the MPTABLE.TBL file.

    Parameters:
        directory_path (str): Path to the 'noah_om/parameters' directory.
        param_updates (dict): Keys are parameter names (e.g., 'MFSNO'), values are strings to insert.
    """
    file_path = Path("data/gage-10109001/config/MPTABLE.TBL")
    if not file_path.exists():
        raise FileNotFoundError(f"MPTABLE.TBL not found at {file_path}")

    with open(file_path, "r") as file:
        lines = file.readlines()
        # print(f"Updating parameters in {file_path}...")

        for i, line in enumerate(lines):
            if line.strip().startswith("SNOW_EMIS"):
                lines[i] = f"  SNOW_EMIS     = {value}\n"

    with open(file_path, "w") as file:
        file.writelines(lines)


# === Utility Function to Retrieve and Preprocess USGS Streamflow ===
def process_usgs_streamflow(site, start, end, output_path=None):
    start = pd.to_datetime(start) - pd.Timedelta(days=1)
    end = pd.to_datetime(end) + pd.Timedelta(days=1)
    adjusted_start = start.strftime("%Y-%m-%d")
    adjusted_end = end.strftime("%Y-%m-%d")

    dfo_usgs = nwis.get_record(sites=site, service="iv", start=adjusted_start, end=adjusted_end)
    dfo_usgs.index = pd.to_datetime(dfo_usgs.index)
    dfo_usgs["Time"] = dfo_usgs.index.floor("h")
    dfo_usgs["00060"] = pd.to_numeric(dfo_usgs["00060"], errors="coerce")
    dfo_usgs_hr = dfo_usgs.groupby("Time")["00060"].mean().reset_index()
    dfo_usgs_hr["values"] = dfo_usgs_hr["00060"] / 35.3147
    dfo_usgs_hr = dfo_usgs_hr[["Time", "values"]]
    if output_path:
        dfo_usgs_hr.to_pickle(output_path)
    return dfo_usgs_hr


# === Wrapper to Set Up NextGen Model Execution ===
class NextGenSetup:
    def __init__(
        self,
        gage_id,
        start_date,
        end_date,
        training_start_date,
        observed_flow_path,
        troute_output_path,
        data_dir,
    ):
        self.gage_id = gage_id
        self.training_start_date = pd.to_datetime(training_start_date)
        self.end_date = pd.to_datetime(end_date)
        self.observed = pd.read_pickle(observed_flow_path)
        self.observed["Time"] = pd.to_datetime(self.observed["Time"]).dt.tz_localize(None)
        self.observed = self.observed[
            (self.observed["Time"] >= self.training_start_date)
            & (self.observed["Time"] <= self.end_date)
        ]
        self.observed = self.observed.set_index("Time")
        self.troute_output_path = troute_output_path
        self.realization_path = Path(data_dir) / "config" / "realization.json"

    def write_config(self, params):
        param_map = {
            "b": params[0],
            "satpsi": params[1],
            "satdk": params[2],
            "maxsmc": params[3],
            "refkdt": params[4],
            "expon": params[5],
            "slope": params[6],
            "max_gw_storage": params[7],
            "Kn": params[8],
            "Klf": params[9],
            "Cgw": params[10],
        }

        update_parameters(self.realization_path, param_map, "CFE")

        # Create updated NOAH parameters dictionary
        noah_param_updates = {
            "MFSNO": params[11],  # Pass float directly
            "MP": params[12],
            "RSURF_EXP": params[13],
            # "SNOW_EMIS": params[11],
            "CWP": params[14],
            "VCMX25": params[15],
            "RSURF_SNOW": params[16],
            "SCAMAX": params[17],
        }

        update_parameters(self.realization_path, noah_param_updates, "NoahOWP")
        # update_snow_emis(params[11])

    def run_model(self, data_dir):

        troute_output_folder = Path(data_dir) / "outputs" / "troute"
        print(troute_output_folder)
        for file in troute_output_folder.glob("*.nc"):
            file.unlink()
            # print("T-route has been removed from previous run")
        # try:
        model = PyNGIAB(data_dir, serial_execution_mode=False)
        model.run()
        
    def evaluate(self, feature_id):
        ds = xr.open_dataset(self.troute_output_path)
        simulated = ds["flow"].sel(feature_id=feature_id).values
        actual_start = min(self.training_start_date, self.observed.index[0])
        simulated = simulated[ds["time"] >= actual_start]
        simulated = simulated[: len(self.observed) - 1]
        return simulated


# === SPOTPY Setup Class for Calibration with TensorBoard ===
class SpotpySetup:
    # CFE model parameters
    soil_params_b = Uniform(2.0, 15.0, optguess=4.05)
    satpsi = Uniform(0.03, 0.955, optguess=0.355)
    satdk = Uniform(0.0000001, 0.000726, optguess=0.00000338)  # hit min
    maxsmc = Uniform(0.16, 0.59, optguess=0.439)  # hit max set to 0.8
    refkdt = Uniform(0.1, 4.0, optguess=1.0)  ######new
    expon = Uniform(1.0, 8.0, optguess=3.0)
    slope = Uniform(0.0, 1.0, optguess=0.1)
    max_gw_storage = Uniform(0.01, 0.25, optguess=0.05)  ######### new
    K_nash_subsurface = Uniform(0.0, 1.0, optguess=0.03)
    K_lf = Uniform(0.0, 1.0, optguess=0.01)
    Cgw = Uniform(0.0000018, 0.0018, optguess=0.000018)

    # # Additional NOAH OWP Modular parameters
    MFSNO = Uniform(0.5, 4.0, optguess=2.0)  # multiplier on snowfall melt factor
    MP = Uniform(3.6, 12.6, optguess=9.0)  # hit max
    RSURF_EXP = Uniform(1.0, 6.0, optguess=5.0)  # hit max
    # SNOW_EMIS = Uniform(0.90, 1.0)  # snow emissivity
    CWP = Uniform(0.09, 0.36, optguess=0.18)
    VCMX25 = Uniform(24.0, 112.0, optguess=52.2)
    RSURF_SNOW = Uniform(0.136, 100.0, optguess=50.0)  # hit min
    SCAMAX = Uniform(0.7, 1.0, optguess=0.9)

    def __init__(
        self,
        model_setup,
        data_dir,
        feature_id,
        invert_objective,
        objective_function,
        objective_function_name=None,
    ):
        self.obj_func = objective_function
        self.objective_function_name = objective_function_name
        self.invert_objective = invert_objective
        self.model = model_setup
        self.data_dir = data_dir
        self.feature_id = feature_id
        self.run_id = 0
        self.best_objective = float("inf") if not invert_objective else float("-inf")

        # Get parameter names for logging
        self.param_names = [
            "soil_params_b",
            "satpsi",
            "satdk",
            "maxsmc",
            "refkdt",
            "expon",
            "slope",
            "max_gw_storage",
            "K_nash_subsurface",
            "K_lf",
            "Cgw",
            "MFSNO",
            "MP",
            "RSURF_EXP",
            # "SNOW_EMIS",
            "CWP",
            "VCMX25",
            "RSURF_SNOW",
            "SCAMAX",
        ]

        self.data_dir = Path(data_dir).resolve()
        self.calibration_dir = (self.data_dir / "calibration").resolve()
        self.iterations_dir = (self.calibration_dir / "iterations").resolve()
        self.iterations_dir.mkdir(parents=True, exist_ok=True)
        (self.calibration_dir / "plots").mkdir(parents=True, exist_ok=True)
        self.iterations_csv_path = self.iterations_dir / "calibration_iterations.csv"
        self.iteration_records = []
        self._iterations_csv_announced = False

    def simulation(self, vector):
        self.current_params = [float(x) for x in vector]
        self.model.write_config(self.current_params)
        self.model.run_model(self.data_dir)
        return self.model.evaluate(self.feature_id)

    def evaluation(self):
        return self.model.observed.values.squeeze()[1:]

    def objectivefunction(self, simulation, evaluation):
        if len(simulation) != len(evaluation):
            raise ValueError("simulation and observation are not equal length")

        objective_metric = self.obj_func(evaluation, simulation)
        if self.objective_function_name == "KGE":
            if self.invert_objective:
                objective_metric = 1 - objective_metric
            else:
                objective_metric = objective_metric - 1
        elif self.objective_function_name == "RMSE":
            if self.invert_objective:
                objective_metric = -objective_metric

        
        # Calculate additional metrics for TensorBoard
        rmse = spotpy.objectivefunctions.rmse(evaluation, simulation)
        kge = spotpy.objectivefunctions.kge(evaluation, simulation)
        mae = np.mean(np.abs(evaluation - simulation))
        nse = 1 - (
            np.sum((evaluation - simulation) ** 2) / np.sum((evaluation - np.mean(evaluation)) ** 2)
        )
        correlation = np.corrcoef(evaluation, simulation)[0, 1]

        record = {
            "iteration": self.run_id,
            "objective_function": self.objective_function_name,
            "objective_value": objective_metric,
            "RMSE": rmse,
            "KGE": kge,
            "MAE": mae,
            "NSE": nse,
            "correlation": correlation,
        }
        for i, param_name in enumerate(self.param_names):
            if i < len(self.current_params):
                record[param_name] = self.current_params[i]
        self.iteration_records.append(record)
        self._save_iterations_csv(iteration=self.run_id)

        for i, param_name in enumerate(self.param_names):
            if i < len(self.current_params):
                start_date = self.model.training_start_date
                if self.run_id % 1 == 0:
                    dates = pd.date_range(start=start_date, periods=len(evaluation), freq="H")

                    fig, ax = plt.subplots(figsize=(12, 6))
                    ax.plot(dates, evaluation, label="Observed", color="black", linewidth=1.5)
                    ax.plot(dates, simulation, label="Simulated", linestyle="--", alpha=0.8)
                    ax.legend()
                    ax.set_title(f"Iteration {self.run_id} - KGE {kge:.4f}")
                    ax.set_xlabel("Date")
                    ax.set_ylabel("Streamflow [m3/sec]")
                    ax.grid(True, alpha=0.3)

                    import matplotlib.dates as mdates

                    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
                    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
                    fig.autofmt_xdate()

                    plot_file = self.calibration_dir / "plots" / f"{self.run_id}.png"
                    plot_file.parent.mkdir(parents=True, exist_ok=True)
                    fig.savefig(plot_file)
                    plt.close(fig)

        self.run_id += 1
        return objective_metric

    def _save_iterations_csv(self, iteration=None):
        """
        Rewrite calibration_iterations.csv with every iteration completed so far.
        Called automatically after each iteration (0, 1, 2, ...).
        """
        if not self.iteration_records:
            return None

        try:
            df = pd.DataFrame(self.iteration_records)
            obj = pd.to_numeric(df["objective_value"], errors="coerce")
            best_pos = int(obj.argmax()) if obj.notna().any() else 0

            df["is_best"] = False
            df.iloc[best_pos, df.columns.get_loc("is_best")] = True

            metric_cols = ["RMSE", "KGE", "MAE", "NSE", "correlation"]
            leading = ["iteration", "objective_function", "objective_value", "is_best"]
            col_order = leading + [c for c in metric_cols if c in df.columns] + self.param_names
            df = df[[c for c in col_order if c in df.columns]]

            self.iterations_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.iterations_csv_path.with_suffix(".tmp.csv")
            df.to_csv(tmp_path, index=False)
            tmp_path.replace(self.iterations_csv_path)

            if not self._iterations_csv_announced:
                print(f"Iterations CSV (updated after each iteration): {self.iterations_csv_path}")
                self._iterations_csv_announced = True

            if iteration is not None:
                print(
                    f"  CSV updated: iteration {iteration} saved "
                    f"({len(df)} row(s) total, is_best on iteration {int(df.iloc[best_pos]['iteration'])})"
                )

            return best_pos
        except Exception as exc:
            print(f"ERROR writing iterations CSV to {self.iterations_csv_path}: {exc}")
            raise


def export_calibration_iterations(optimizer, verbose=True):
    """
    Write all calibration iterations to CSV under calibration/iterations/.
    The best iteration is marked in the is_best column (True).
    Called after each iteration so the CSV exists from iteration 0 onward.
    """
    if not optimizer.iteration_records:
        return None, None

    best_pos = optimizer._save_iterations_csv()
    df = pd.DataFrame(optimizer.iteration_records)

    if verbose:
        print(f"Calibration iterations saved to: {optimizer.iterations_csv_path}")
        print(f"Best iteration so far: {best_pos} (is_best=True in CSV)")

    return df, best_pos


def _best_params_from_records(optimizer, best_pos):
    """Return best parameter vector in the order expected by NextGenSetup.write_config."""
    best_row = optimizer.iteration_records[best_pos]
    return [best_row[name] for name in optimizer.param_names]


def plot_results(results, observation_data, output_dir):
    plot_parametertrace(results, output_dir)
    plot_parameterInteraction(results, output_dir)
    plot_bestmodelrun(results, observation_data, output_dir)
    plot_parameter_correlation(results, output_dir)
    create_interactive_plots(results, observation_data, output_dir)


# === Function to Run SPOTPY Calibration with TensorBoard ===
def run_spotpy(
    gage_id,
    start_date,
    end_date,
    training_start_date,
    observed_flow_path,
    troute_output_path,
    data_dir,
    feature_id,
    algorithm,
    objective_function,
    repetitions=25,
    dds_trials=5,
    # tensorboard_logdir=None,
):
    # Model setup
    model_setup = NextGenSetup(
        gage_id,
        start_date,
        end_date,
        training_start_date,
        observed_flow_path,
        troute_output_path,
        data_dir,
    )

    if objective_function == "KGE":
        best_is_higher = True
        obj_func = spotpy.objectivefunctions.kge
    elif objective_function == "RMSE":
        best_is_higher = False
        obj_func = spotpy.objectivefunctions.rmse

    if algorithm == "DDS":
        algorithm_maximizes = True
    elif algorithm == "SCE":
        algorithm_maximizes = False

    invert_objective = best_is_higher != algorithm_maximizes

    # Log hyperparameters
    hparams = {
        "algorithm": algorithm,
        "objective_function": objective_function,
        "repetitions": repetitions,
        "gage_id": gage_id,
        "start_date": str(start_date),
        "end_date": str(end_date),
    }
    if algorithm == "DDS":
        hparams["dds_trials"] = dds_trials

    # writer.add_hparams(hparams, {"dummy": 0})  # TensorBoard requires at least one metric

    optimizer = SpotpySetup(
        model_setup, data_dir, feature_id, invert_objective, obj_func, objective_function
    )
    optimizer.iteration_records = []
    if optimizer.iterations_csv_path.exists():
        optimizer.iterations_csv_path.unlink()

    # RAM database: no spotpy_results_*.csv in calibration/ (we write calibration_iterations.csv)
    db_name = "ngen_cal"
    spotpy_csv = optimizer.calibration_dir / f"spotpy_results_{algorithm}_{objective_function}.csv"
    if spotpy_csv.exists():
        spotpy_csv.unlink()

    if algorithm == "SCE":
        sampler = spotpy.algorithms.sceua(optimizer, dbname=db_name, dbformat="ram")
        # sampler.sample(repetitions, ngs=20)

    elif algorithm == "DDS":
        sampler = spotpy.algorithms.dds(optimizer, dbname=db_name, dbformat="ram")
        sampler.sample(repetitions, trials=int(dds_trials))

    _, best_pos = export_calibration_iterations(optimizer, verbose=True)
    if best_pos is None:
        raise RuntimeError("No calibration iterations were recorded.")
    best_params = _best_params_from_records(optimizer, best_pos)

    return best_params
