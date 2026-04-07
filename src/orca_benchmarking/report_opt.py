# ============================================================
# ORCA optimisation benchmarking report
#
# This script:
#   * Parses ORCA optimisation output files
#   * Collects SLURM accounting data via sacct
#   * Produces a single interactive HTML dashboard containing:
#       - CPU efficiency
#       - Memory usage (GB)
#       - Time-based scaling plots
#       - Ideal-time reference lines
#       - Speedup plots with ideal reference
#   * Optionally writes all processed data to CSV (--csv)
#
# The figure:
#   * Is physically square and browser-responsive
#   * Shows two rows at a time
#   * Uses explicit axis limits (all plots start at 0)
#
# NOTE:
#   This file intentionally does NOT include
#     if __name__ == "__main__":
#   by user request.
# ============================================================

import os
import re
import subprocess
import sys
import json
import statistics
import argparse
import csv

from tqdm import tqdm
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

# Name of the benchmarking directory (must exist)
BENCH_DIR_NAME = "orca_benchmarking"

# SLURM output naming pattern:
#   slurm-<jobid>_<cores>.out
SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")


# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    """
    Parse an ORCA output file and extract:
      * Mean DIIS iteration time
      * Mean SOSCF iteration time
      * Mean geometry-iteration time

    Returns:
        (diis_mean, soscf_mean, geom_mean)
        Each value may be None if the data are absent.
    """

    diis, soscf, geom = [], [], []
    in_diis = False
    in_soscf = False

    # Regex matching iteration timing table rows
    iter_re = re.compile(r"^\s*\d+.*\s+([0-9]+(?:\.[0-9]+)?)\s*$")

    # Regex matching geometry iteration timing lines
    geom_re = re.compile(
        r"Time for complete geometry iter\s*:\s*([0-9]+(?:\.[0-9]+)?)"
    )

    with open(path) as f:
        for line in f:

            # Blank line terminates DIIS/SOSCF tables
            if not line.strip():
                in_diis = False
                in_soscf = False
                continue

            # Detect DIIS table
            if "D-I-I-S" in line:
                in_diis, in_soscf = True, False
                continue

            # Detect SOSCF table
            if "S-O-S-C-F" in line:
                in_diis, in_soscf = False, True
                continue

            # Skip separator lines
            if line.startswith("---"):
                continue

            # Geometry iteration timing
            g = geom_re.search(line)
            if g:
                geom.append(float(g.group(1)))
                continue

            # DIIS / SOSCF iteration timing
            m = iter_re.match(line)
            if m:
                val = float(m.group(1))
                if in_diis:
                    diis.append(val)
                elif in_soscf:
                    soscf.append(val)

    # Helper: return mean or None
    mean = lambda v: statistics.mean(v) if v else None

    return mean(diis), mean(soscf), mean(geom)


# ------------------------------------------------------------
# SLURM sacct helpers
# ------------------------------------------------------------
def run_sacct(jobid, taskid):
    """
    Run `sacct --json` for a given job/task id pair
    and return the parsed JSON output.
    """
    result = subprocess.run(
        ["sacct", "--json", "-j", f"{jobid}_{taskid}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def parse_sacct_data(data):
    """
    Extract from sacct JSON:
      * elapsed time (s)
      * total CPU time (s)
      * maximum RSS (GB)
    """

    job = data["jobs"][0]
    elapsed = float(job["time"]["elapsed"])

    cpu_msec = 0
    max_mem_b = 0

    # Loop over job steps and requested TRES
    for step in job["steps"]:
        for t in step["tres"]["requested"]["total"]:
            if t["type"] == "cpu":
                cpu_msec += t["count"]
            elif t["type"] == "mem":
                max_mem_b = max(max_mem_b, t["count"])

    # Convert memory to GB
    max_mem_gb = max_mem_b / 1024.0 / 1024.0 / 1024.0

    return elapsed, cpu_msec / 1000.0, max_mem_gb


# ------------------------------------------------------------
# Main driver
# ------------------------------------------------------------
def main():
    """
    Main entry point:
      * Collect benchmark data
      * Compute ideal time & speedup references
      * Build combined Plotly figure
      * Write HTML + optional CSV
    """

    # ---------------- CLI arguments ----------------
    parser = argparse.ArgumentParser(
        description="ORCA optimisation benchmarking report"
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write CSV output alongside HTML report",
    )
    args = parser.parse_args()

    print("🔍 ORCA optimisation benchmarking report")

    # ---------------- Locate benchmark directory ----------------
    bench_dir = os.path.join(os.getcwd(), BENCH_DIR_NAME)
    if not os.path.isdir(bench_dir):
        sys.exit("❌ Run from the directory ABOVE orca_benchmarking/")

    print("📊 Collecting benchmark results...")

    results = []

    # ---------------- Process all SLURM outputs ----------------
    for fname in tqdm(sorted(os.listdir(bench_dir)),
                      desc="Processing SLURM outputs"):

        # Match only slurm-<jobid>_<cores>.out
        m = SLURM_FILE_REGEX.match(fname)
        if not m:
            continue

        jobid, cores = m.group(1), int(m.group(2))

        # ORCA output path
        orca_out = os.path.join(bench_dir, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            continue

        # Parse ORCA timings
        diis, soscf, geom = parse_orca_output(orca_out)

        # Parse SLURM accounting
        elapsed, cpu, rss_gb = parse_sacct_data(run_sacct(jobid, cores))

        # CPU efficiency (%)
        cpu_eff = (cpu / (elapsed * cores)) * 100.0

        results.append(dict(
            cores=cores,
            cpu_eff=cpu_eff,
            rss_gb=rss_gb,
            diis_time=diis,
            soscf_time=soscf,
            geom_time=geom,
        ))

    # Sort results by core count
    results.sort(key=lambda r: r["cores"])
    cores = [r["cores"] for r in results]
    max_cores = max(cores)

    # --------------------------------------------------------
    # Ideal time reference curves: t(1) / cores
    # --------------------------------------------------------
    r1 = next((r for r in results if r["cores"] == 1), None)

    ideal_time = {"diis": None, "soscf": None, "geom": None}
    if r1:
        if r1["diis_time"] is not None:
            ideal_time["diis"] = [r1["diis_time"] / c for c in cores]
        if r1["soscf_time"] is not None:
            ideal_time["soscf"] = [r1["soscf_time"] / c for c in cores]
        if r1["geom_time"] is not None:
            ideal_time["geom"] = [r1["geom_time"] / c for c in cores]

    # --------------------------------------------------------
    # Speedup computation: t(1) / t(N)
    # --------------------------------------------------------
    for key in ("diis", "soscf", "geom"):
        tkey = f"{key}_time"
        skey = f"{key}_speedup"
        if r1 and r1[tkey] is not None:
            for r in results:
                r[skey] = r1[tkey] / r[tkey] if r[tkey] else None
        else:
            for r in results:
                r[skey] = None

    print("📈 Building combined figure...")

    # --------------------------------------------------------
    # Create combined 3x3 subplot layout
    # --------------------------------------------------------
    fig = make_subplots(
        rows=3,
        cols=3,
        specs=[
            [{"type": "xy"}, {"type": "xy"}, None],
            [{"type": "xy"}, {"type": "xy"}, {"type": "xy"}],
            [{"type": "xy"}, {"type": "xy"}, {"type": "xy"}],
        ],
        subplot_titles=[
            "CPU efficiency", "Memory usage",
            "DIIS time", "SOSCF time", "Geometry time",
            "DIIS speedup", "SOSCF speedup", "Geometry speedup",
        ],
        vertical_spacing=0.06,
        horizontal_spacing=0.06,
    )

    # --------------------------------------------------------
    # Row 1: CPU efficiency & memory
    # --------------------------------------------------------
    fig.add_trace(
        go.Scatter(x=cores, y=[r["cpu_eff"] for r in results],
                   mode="lines+markers", name="CPU efficiency (%)"),
        1, 1
    )

    fig.add_trace(
        go.Scatter(x=cores, y=[r["rss_gb"] for r in results],
                   mode="lines+markers", name="Max RSS (GB)"),
        1, 2
    )

    # --------------------------------------------------------
    # Row 2: Time plots + ideal reference
    # --------------------------------------------------------
    for col, key in enumerate(("diis", "soscf", "geom"), start=1):
        fig.add_trace(
            go.Scatter(x=cores,
                       y=[r[f"{key}_time"] for r in results],
                       mode="lines+markers",
                       name=f"{key.upper()} time"),
            2, col
        )

        if ideal_time[key] is not None:
            fig.add_trace(
                go.Scatter(x=cores,
                           y=ideal_time[key],
                           mode="lines",
                           line=dict(dash="dash", color="gray"),
                           name=f"{key.upper()} ideal time (t₁ / cores)"),
                2, col
            )

    # --------------------------------------------------------
    # Row 3: Speedup plots + ideal reference
    # --------------------------------------------------------
    for col, key in enumerate(("diis", "soscf", "geom"), start=1):
        fig.add_trace(
            go.Scatter(x=cores, y=cores,
                       mode="lines",
                       line=dict(dash="dash", color="gray"),
                       name="Ideal speedup (y = x)"),
            3, col
        )

        fig.add_trace(
            go.Scatter(x=cores,
                       y=[r[f"{key}_speedup"] for r in results],
                       mode="lines+markers",
                       name=f"{key.upper()} speedup"),
            3, col
        )

    # --------------------------------------------------------
    # Explicit axis limits & labels (all plots start at 0)
    # --------------------------------------------------------
    fig.update_xaxes(
        title_text="Number of cores",
        range=[0, max_cores],
        showline=True,
        ticks="outside",
        showticklabels=True,
    )

    fig.update_yaxes(
        range=[0, None],
        showline=True,
        ticks="outside",
        showticklabels=True,
    )

    fig.update_yaxes(range=[0, 100],
                     title_text="CPU efficiency (%)",
                     row=1, col=1)

    fig.update_yaxes(title_text="Max RSS (GB)", row=1, col=2)
    fig.update_yaxes(title_text="Time (s)", row=2, col=1)
    fig.update_yaxes(title_text="Time (s)", row=2, col=2)
    fig.update_yaxes(title_text="Time (s)", row=2, col=3)
    fig.update_yaxes(title_text="Speedup", row=3, col=1)
    fig.update_yaxes(title_text="Speedup", row=3, col=2)
    fig.update_yaxes(title_text="Speedup", row=3, col=3)

    fig.update_layout(
        template="none",
        hovermode="x unified",
        showlegend=False,
    )

    # --------------------------------------------------------
    # Write responsive square HTML output
    # --------------------------------------------------------
    print("🖥️ Writing combined HTML output...")

    post_script = """
    function resizeSquare() {
        const s = Math.min(window.innerWidth / 3,
                           window.innerHeight / 2);
        Plotly.relayout('{plot_id}',
                        {width: s * 3, height: s * 2});
    }
    window.addEventListener('resize', resizeSquare);
    resizeSquare();
    """

    html = pio.to_html(
        fig,
        include_plotlyjs="cdn",
        full_html=True,
        config={"responsive": True},
        post_script=post_script,
    )

    with open("orca_benchmark_results_opt.html", "w") as f:
        f.write(html)

    # --------------------------------------------------------
    # Optional CSV output
    # --------------------------------------------------------
    if args.csv:
        print("📄 Writing CSV output...")
        with open("orca_benchmark_results_opt.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)
        print("✅ CSV written to orca_benchmark_results_opt.csv")

    print("✅ Plot written to orca_benchmark_results_opt.html")
