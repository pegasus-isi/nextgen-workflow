# 🌊 NextGen Modeling Workflow Resource

---

<div style="background:#13294b; border-left:6px solid #5cd6ff; padding:16px 20px; border-radius:10px; margin:20px 0;">
  <h2 style="margin:0; font-size:22px; font-weight:700; color:#eaf7ff;">
    🔍 Overview
  </h2>
  <p style="margin:10px 0 0; color:#eaf7ff; font-size:15px; line-height:1.6;">
    This HydroShare resource provides a complete, reproducible workflow for preparing, running, evaluating, calibrating, and analyzing
    research-scale simulations of the <strong>Next Generation Water Resources Modeling Framework (NextGen)</strong>.
    It is designed for execution on the <strong>CIROH 2i2c-JupyterHub</strong> cloud platform.
  </p>
</div>

## ✨ What This Resource Includes

- 📓 **Jupyter notebooks** — data preparation, model execution, output evaluation (TEEHR), calibration, and analysis
- 🛠️ **Python utility modules** — forcing visualization, HydroFabric exploration, calibration, output aggregation, and TEEHR evaluation

This resource enables hydrologic researchers and students to experiment with NextGen components, evaluate model skill against USGS and NWM datasets using TEEHR, visualize HydroFabric layers, and explore hydrologic behavior at subdomain scale.

---

## 📂 Contents

### 📓 1. Jupyter Notebooks

| | Notebook | Description |
|---|----------|-------------|
| 📋 | `NextGen_Data_Preparation.ipynb` | Subsets HydroFabric, prepares forcing data, creates model realizations and configurations, and organizes model input and output directories. |
| ▶️ | `NextGen_Run.ipynb` | Executes the NextGen model and generates cat-files (hydrologic variables), nexus output, and routing NetCDF results. |
| 📊 | `NextGen_Outputs_Analysis.ipynb` | Processes outputs, computes basin-mean variables, and generates CSV summaries. |
| 📈 | `NextGen_TEEHR_Evaluation.ipynb` | Evaluates simulated streamflow with TEEHR using USGS observations, NWM v3.0, and NextGen results; computes performance metrics and diagnostics. |
| ⚙️ | `NextGen_Calibration.ipynb` | Performs automated calibration with SPOTPY using USGS observations and dynamic parameter updates. |

### 🛠️ 2. Python Utility Modules

| | File | Purpose |
|---|------|---------|
| ⚙️ | `cal_utils.py` | Calibration utilities (parameter updates, t-route output extraction, SPOTPY setup). |
| 🌧️ | `forcings_utils.py` | Forcing data visualization, transformation, and time-series alignment. |
| 🗺️ | `hydrofabric_visualization_utils.py` | Interactive HydroFabric visualization using Folium and GeoPandas. |
| 📦 | `ngen_outputs_utils.py` | Aggregates Noah, CFE, and routing outputs with unit conversion and area weighting. |
| 🔗 | `ngiab_utils.py` | Utilities for TEEHR-based NextGen evaluation and the `NextGen_TEEHR_Evaluation.ipynb` workflow. |

---

## 🚀 Quick Start Guide

**Workflow at a glance:**

| Step | Icon | Action | Notebook |
|:----:|:----:|--------|----------|
| 1 | 🚀 | Launch environment | — (open on CIROH JupyterHub) |
| 2 | 📋 | Prepare inputs | `NextGen_Data_Preparation.ipynb` |
| 3 | ▶️ | Run model | `NextGen_Run.ipynb` |
| 4 | 📊 | Analyze outputs | `NextGen_Outputs_Analysis.ipynb` |
| 5 | 📈 | Evaluate with TEEHR | `NextGen_TEEHR_Evaluation.ipynb` |
| 6 | ⚙️ | Calibrate parameters | `NextGen_Calibration.ipynb` |

> 💡 **Tip:** Run the steps in order: **📋 Prepare → ▶️ Run → 📊 Analyze → 📈 Evaluate → ⚙️ Calibrate**

---

### 🚀 Step 1 — Launch the Environment

Open this resource in **CIROH 2i2c-JupyterHub** so NextGen, PyNGIAB, and TEEHR dependencies are available.

### 📋 Step 2 — Prepare Inputs

Use `NextGen_Data_Preparation.ipynb` to subset HydroFabric, prepare forcing data, and generate model configuration files.

### ▶️ Step 3 — Run NextGen

Run `NextGen_Run.ipynb` to produce hydrologic and routing outputs.

### 📊 Step 4 — Analyze Outputs

Use `NextGen_Outputs_Analysis.ipynb` for basin-mean calculations and CSV summaries.

### 📈 Step 5 — Evaluate Model Performance with TEEHR

Run `NextGen_TEEHR_Evaluation.ipynb` to:

- 🔗 Add USGS, NWM, and NGIAB time series
- 📐 Compute metrics (KGE, NSE, MAE, and others)
- 📉 Generate performance and diagnostic plots

### ⚙️ Step 6 — Perform Calibration

Use `NextGen_Calibration.ipynb` for SPOTPY calibration and to update model parameters in `realization.json`.

---

## 👥 Intended Users

This resource is intended for:

- 🌊 Hydrologic modelers
- 🔬 CIROH researchers
- 🎓 Students learning NextGen workflows
- 📉 Analysts evaluating hydrologic prediction skill
- ♻️ Anyone interested in reproducible modeling and model–data evaluation

---

## 📫 Contact

For questions or suggested improvements, contact:

- **Ayman Nassar** — ayman.nassar@usu.edu; aymnassar@gmail.com
- **David Tarboton** — david.tarboton@usu.edu
