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
import plotly.io as pio

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

    with open(path) as f:
        for line in f:
            if line.strip() == "":
                in_diis = in_soscf = False
                continue
            if "D-I-I-S" in line:
                in_diis, in_soscf = True, False
                continue
            if "S-O-S-C-F" in line:
                in_diis, in_soscf = False, True
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

    def mean(values):
        return statistics.mean(values) if values else None

    return mean(diis_times), mean(soscf_times), mean(geom_iter_times)

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

    return float(elapsed), cpu_msec / 1000.0, max_mem_b / 1024 / 1024

# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ORCA optimisation benchmarking report"
    )
    parser.add_argument("--csv", action="store_true", help="Write CSV output")
    args = parser.parse_args()

    print("🔍 ORCA optimisation benchmarking report")
    print("📁 Locating benchmark directory...")

    bench_dir = os.path.join(os.getcwd(), BENCH_DIR_NAME)
    if not os.path.isdir(bench_dir):
        sys.exit("❌ Run from the directory ABOVE orca_benchmarking/")

    print("✅ Benchmark directory found")
    print("\n📊 Collecting benchmark results...")

    results = []

    for fname in tqdm(sorted(os.listdir(bench_dir)),
                      desc="Processing SLURM outputs", unit="job"):
        m = SLURM_FILE_REGEX.match(fname)
        if not m:
            continue

        jobid, cores = m.group(1), int(m.group(2))
        orca_out = os.path.join(bench_dir, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            continue

        diis, soscf, geom = parse_orca_output(orca_out)
        elapsed, cpu, rss = parse_sacct_data(run_sacct(jobid, cores))

        results.append({
            "cores": cores,
            "diis": diis,
            "soscf": soscf,
            "geom": geom,
        })

    results.sort(key=lambda r: r["cores"])
    cores = [r["cores"] for r in results]

    print("📈 Generating time plot...")

    fig_time = make_subplots(
        rows=2, cols=2, shared_xaxes=True, vertical_spacing=0.06,
        subplot_titles=[
            "Mean DIIS iteration time",
            "Mean SOSCF iteration time",
            "Mean geometry iteration time",
            "",
        ],
    )

    metrics = [("diis", 1, 1), ("soscf", 1, 2), ("geom", 2, 1)]

    for key, r, c in metrics:
        fig_time.add_trace(
            go.Scatter(
                x=cores,
                y=[r[key] for r in results],
                mode="lines+markers",
                name=f"{key.upper()} time",
            ),
            row=r, col=c,
        )

    fig_time.update_layout(
        template="none",
        hovermode="x unified",
        margin=dict(l=80, r=40, t=90, b=80),
    )

    print("🖥️ Writing time plot HTML...")
    html_time = pio.to_html(fig_time, full_html=True, include_plotlyjs="cdn")
    with open("orca_benchmark_results_opt.html", "w") as f:
        f.write(html_time)

    print("📈 Computing speedup curves...")

    r1 = next((r for r in results if r["cores"] == 1), None)
    if r1 is None:
        print("⚠️  No CPU=1 data found — speedup plot skipped")
        return

    fig_speedup = make_subplots(
        rows=2, cols=2, shared_xaxes=True, vertical_spacing=0.06,
        subplot_titles=[
            "DIIS speedup",
            "SOSCF speedup",
            "Geometry speedup",
            "",
        ],
    )

    for key, r, c in metrics:
        if r1[key] is None:
            print(f"⚠️  No {key.upper()} data at CPU=1 — skipping")
            continue

        speedup = [r1[key] / r[key] if r[key] else None for r in results]

        fig_speedup.add_trace(
            go.Scatter(
                x=cores,
                y=speedup,
                mode="lines+markers",
                name=f"{key.upper()} speedup",
            ),
            row=r, col=c,
        )

    fig_speedup.update_layout(
        template="none",
        hovermode="x unified",
        margin=dict(l=80, r=40, t=90, b=80),
    )

    print("🖥️ Writing speedup plot HTML...")
    html_speedup = pio.to_html(fig_speedup, full_html=True, include_plotlyjs="cdn")
    with open("orca_benchmark_speedup_opt.html", "w") as f:
        f.write(html_speedup)

    print("✅ Plot written to orca_benchmark_results_opt.html")
    print("✅ Plot written to orca_benchmark_speedup_opt.html")

    if args.csv:
        print("📄 Writing CSV output...")
        with open("orca_benchmark_results_opt.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)
        print("✅ CSV written to orca_benchmark_results_opt.csv")