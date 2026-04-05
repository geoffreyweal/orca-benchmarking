import os
import re
import subprocess
import csv
import sys
import json
import statistics
import argparse

import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
BENCH_DIR_NAME = "orca_benchmarking"
SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")

# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    """
    Extract timing statistics from ORCA output:
      - DIIS iteration times (mean, std)
      - SOSCF iteration times (mean, std)
      - Geometry iteration times (mean, std)
    """

    diis_times = []
    soscf_times = []
    geom_iter_times = []

    in_diis = False
    in_soscf = False

    iter_line_re = re.compile(r"^\s*\d+.*\s+([0-9]+(?:\.[0-9]+)?)\s*$")
    geom_iter_re = re.compile(
        r"Time for complete geometry iter\s*:\s*([0-9]+(?:\.[0-9]+)?)"
    )

    with open(path, "r") as f:
        for line in f:

            # Blank line always terminates DIIS / SOSCF tables
            if line.strip() == "":
                in_diis = False
                in_soscf = False
                continue

            if "D-I-I-S" in line:
                in_diis = True
                in_soscf = False
                continue

            if "S-O-S-C-F" in line:
                in_diis = False
                in_soscf = True
                continue

            # Geometry iteration timing
            g = geom_iter_re.search(line)
            if g:
                geom_iter_times.append(float(g.group(1)))
                continue

            if line.strip().startswith("---"):
                continue

            m = iter_line_re.match(line)
            if not m:
                continue

            time_sec = float(m.group(1))
            if in_diis:
                diis_times.append(time_sec)
            elif in_soscf:
                soscf_times.append(time_sec)

    def mean_std(values):
        if len(values) == 0:
            return None, None
        if len(values) == 1:
            return values[0], 0.0
        return statistics.mean(values), statistics.stdev(values)

    return (
        *mean_std(diis_times),
        *mean_std(soscf_times),
        *mean_std(geom_iter_times),
    )

# ------------------------------------------------------------
# sacct helpers
# ------------------------------------------------------------
def run_sacct(jobid, taskid):
    cmd = ["sacct", "--json", "-j", f"{jobid}_{taskid}"]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)

# ------------------------------------------------------------
# REQUIRED sacct parsing method (UNCHANGED)
# ------------------------------------------------------------
def parse_sacct_data(data):
    def extract_tres(objs, name, default=0):
        for obj in objs:
            if obj.get("type") == name:
                return obj.get("count", default)
        return default

    jobs = data.get("jobs", [])
    if len(jobs) == 0:
        raise RuntimeError("sacct returned no job data")
    if len(jobs) > 1:
        raise RuntimeError("sacct returned multiple jobs unexpectedly")

    job = jobs[0]

    elapsed_sec = job["time"]["elapsed"]
    total_cpu_msec = 0
    max_mem_b = -1

    for step in job.get("steps", []):
        tres_used = step["tres"]["requested"]
        total_cpu_msec += extract_tres(tres_used["total"], "cpu", 0)

        mem_b = extract_tres(tres_used["total"], "mem", 0)
        if mem_b > max_mem_b:
            max_mem_b = mem_b

    return (
        float(elapsed_sec),
        total_cpu_msec / 1000.0,
        max_mem_b / 1024.0 / 1024.0,
    )

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate ORCA optimisation benchmarking report"
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write orca_benchmark_results_opt.csv",
    )
    args = parser.parse_args()

    cwd = os.getcwd()
    bench_dir = os.path.join(cwd, BENCH_DIR_NAME)

    if not os.path.isdir(bench_dir):
        sys.exit(
            f"\n❌ Error: '{BENCH_DIR_NAME}/' not found in:\n"
            f"    {cwd}\n\n"
            "Please run from the directory ABOVE 'orca_benchmarking/'.\n"
        )

    results = []

    for filename in sorted(os.listdir(bench_dir)):
        m = SLURM_FILE_REGEX.match(filename)
        if not m:
            continue

        jobid, cores = m.groups()
        cores = int(cores)

        orca_out = os.path.join(bench_dir, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            continue

        (
            diis_mean, diis_std,
            soscf_mean, soscf_std,
            geom_mean, geom_std,
        ) = parse_orca_output(orca_out)

        sacct_json = run_sacct(jobid, cores)
        elapsed_s, cpu_s, rss_mb = parse_sacct_data(sacct_json)

        cpu_eff = (cpu_s / (elapsed_s * cores)) * 100.0

        results.append({
            "cores": cores,
            "elapsed_time_s": elapsed_s,
            "cpu_time_s": cpu_s,
            "cpu_eff_percent": cpu_eff,
            "max_rss_mb": rss_mb,
            "diis_mean_s": diis_mean,
            "soscf_mean_s": soscf_mean,
            "geom_iter_mean_s": geom_mean,
        })

    if not results:
        sys.exit("\n❌ No optimisation benchmark data found.\n")

    results.sort(key=lambda r: r["cores"])

    # --------------------------------------------------------
    # CSV output (optional)
    # --------------------------------------------------------
    if args.csv:
        with open("orca_benchmark_results_opt.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print("✅ orca_benchmark_results_opt.csv written")

    # --------------------------------------------------------
    # Plotly figure
    # --------------------------------------------------------
    cores = [r["cores"] for r in results]

    fig = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes=True,
        subplot_titles=[
            "CPU efficiency (%)",
            "Mean DIIS iteration time (s)",
            "Mean SOSCF iteration time (s)",
            "Mean geometry iteration time (s)",
        ],
    )

    fig.add_trace(
        go.Scatter(
            x=cores,
            y=[r["cpu_eff_percent"] for r in results],
            mode="lines+markers",
            name="CPU efficiency",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=cores,
            y=[r["diis_mean_s"] for r in results],
            mode="lines+markers",
            name="DIIS mean",
        ),
        row=1,
        col=2,
    )

    fig.add_trace(
        go.Scatter(
            x=cores,
            y=[r["soscf_mean_s"] for r in results],
            mode="lines+markers",
            name="SOSCF mean",
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=cores,
            y=[r["geom_iter_mean_s"] for r in results],
            mode="lines+markers",
            name="Geometry mean",
        ),
        row=2,
        col=2,
    )

    fig.update_layout(
        title="ORCA optimisation benchmarking",
        hovermode="x unified",
    )

    fig.show()

# ------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------
def cli():
    main()