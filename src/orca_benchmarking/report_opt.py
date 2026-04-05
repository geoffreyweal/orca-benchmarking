import os
import re
import subprocess
import csv
import sys
import json
import statistics
import argparse
from tqdm import tqdm
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

            g = geom_iter_re.search(line)
            if g:
                geom_iter_times.append(float(g.group(1)))
                continue

            if line.strip().startswith("---"):
                continue

            m = iter_line_re.match(line)
            if not m:
                continue

            t = float(m.group(1))
            if in_diis:
                diis_times.append(t)
            elif in_soscf:
                soscf_times.append(t)

    def mean_std(values):
        if not values:
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
    result = subprocess.run(
        ["sacct", "--json", "-j", f"{jobid}_{taskid}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)

def parse_sacct_data(data):
    def extract_tres(objs, name, default=0):
        for o in objs:
            if o.get("type") == name:
                return o.get("count", default)
        return default

    job = data["jobs"][0]
    elapsed = job["time"]["elapsed"]
    cpu_msec = 0
    max_mem_b = -1

    for step in job["steps"]:
        tres = step["tres"]["requested"]["total"]
        cpu_msec += extract_tres(tres, "cpu", 0)
        mem = extract_tres(tres, "mem", 0)
        max_mem_b = max(max_mem_b, mem)

    return (
        float(elapsed),
        cpu_msec / 1000.0,
        max_mem_b / 1024.0 / 1024.0,
    )

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ORCA optimisation benchmarking report"
    )
    parser.add_argument("--csv", action="store_true", help="Write CSV output")
    args = parser.parse_args()

    print("🔍 ORCA optimisation benchmark report")

    bench_dir = os.path.join(os.getcwd(), BENCH_DIR_NAME)
    if not os.path.isdir(bench_dir):
        sys.exit("❌ Run from the directory ABOVE orca_benchmarking/")

    results = []

    print("\n📊 Collecting benchmark results…")
    for fname in tqdm(sorted(os.listdir(bench_dir)), unit="job"):
        m = SLURM_FILE_REGEX.match(fname)
        if not m:
            continue

        jobid, cores = m.group(1), int(m.group(2))
        orca_out = os.path.join(bench_dir, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            continue

        diis_m, _, soscf_m, _, geom_m, _ = parse_orca_output(orca_out)
        elapsed, cpu, rss = parse_sacct_data(run_sacct(jobid, cores))

        cpu_eff = (cpu / (elapsed * cores)) * 100.0

        results.append({
            "cores": cores,
            "cpu_eff_percent": cpu_eff,
            "diis_mean_s": diis_m,
            "soscf_mean_s": soscf_m,
            "geom_iter_mean_s": geom_m,
        })

    results.sort(key=lambda r: r["cores"])
    cores = [r["cores"] for r in results]

    # --------------------------------------------------------
    # Plotly figure
    # --------------------------------------------------------
    fig = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes=True,
        subplot_titles=[
            "CPU efficiency",
            "Mean DIIS iteration time",
            "Mean SOSCF iteration time",
            "Mean geometry iteration time",
        ],
    )

    fig.add_trace(go.Scatter(
        x=cores, y=[r["cpu_eff_percent"] for r in results],
        mode="lines+markers",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=cores, y=[r["diis_mean_s"] for r in results],
        mode="lines+markers",
    ), row=1, col=2)

    fig.add_trace(go.Scatter(
        x=cores, y=[r["soscf_mean_s"] for r in results],
        mode="lines+markers",
    ), row=2, col=1)

    fig.add_trace(go.Scatter(
        x=cores, y=[r["geom_iter_mean_s"] for r in results],
        mode="lines+markers",
    ), row=2, col=2)

    fig.update_layout(
        hovermode="x",
        title="ORCA optimisation benchmarking",
    )

    # --------------------------------------------------------
    # FULL hover synchronization via injected JavaScript
    # --------------------------------------------------------
    html = fig.to_html(
        full_html=True,
        include_plotlyjs="cdn",
        div_id="benchmark-plot",
    )

    custom_js = """
<script>
document.addEventListener("DOMContentLoaded", function () {
  var plot = document.getElementById("benchmark-plot");

  plot.on("plotly_hover", function (eventData) {
    if (!eventData || !eventData.points || !eventData.points.length) return;

    var xVal = eventData.points[0].x;
    var hoverPoints = [];

    plot.data.forEach(function (trace, traceIndex) {
      if (!trace.x) return;
      var idx = trace.x.indexOf(xVal);
      if (idx !== -1) {
        hoverPoints.push({
          curveNumber: traceIndex,
          pointNumber: idx
        });
      }
    });

    Plotly.Fx.hover(plot, hoverPoints);
  });

  plot.on("plotly_unhover", function () {
    Plotly.Fx.unhover(plot);
  });
});
</script>
"""

    html = html.replace("</body>", custom_js + "\n</body>")

    with open("orca_benchmark_results_opt_full_sync.html", "w") as f:
        f.write(html)

    print("✅ Full-hover-synchronised HTML written:")
    print("   orca_benchmark_results_opt_full_sync.html")

# ------------------------------------------------------------
def cli():
    main()
