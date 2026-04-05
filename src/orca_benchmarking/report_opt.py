import os
import re
import subprocess
import csv
import sys
import json
import statistics
import argparse
from tqdm import tqdm

import pandas as pd
import altair as alt

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

    iter_line_re = re.compile(r"^\s*\d+.\*\s+([0-9]+(?:\.[0-9]+)?)\s*$")
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
    print(f"📁 Benchmark directory: {bench_dir}")

    if not os.path.isdir(bench_dir):
        sys.exit("❌ Run from the directory ABOVE orca_benchmarking/")

    results = []

    print("\n📊 Collecting benchmark results…")
    for fname in tqdm(
        sorted(os.listdir(bench_dir)),
        desc="Processing benchmarks",
        unit="job",
    ):
        m = SLURM_FILE_REGEX.match(fname)
        if not m:
            continue

        jobid, cores = m.group(1), int(m.group(2))

        orca_out = os.path.join(
            bench_dir, f"{cores}cores", "orca.out"
        )
        if not os.path.isfile(orca_out):
            continue

        diis_m, _, soscf_m, _, geom_m, _ = parse_orca_output(orca_out)
        elapsed, cpu, rss = parse_sacct_data(run_sacct(jobid, cores))

        cpu_eff = (cpu / (elapsed * cores)) * 100.0

        results.append({
            "cores": cores,
            "elapsed_time_s": elapsed,
            "cpu_time_s": cpu,
            "cpu_eff_percent": cpu_eff,
            "max_rss_mb": rss,
            "diis_mean_s": diis_m,
            "soscf_mean_s": soscf_m,
            "geom_iter_mean_s": geom_m,
        })

    if not results:
        sys.exit("\n❌ No optimisation benchmark data found.\n")

    results.sort(key=lambda r: r["cores"])

    # --------------------------------------------------------
    # Altair figure (HTML only) — Altair v5 SAFE
    # --------------------------------------------------------
    print("\n📈 Generating Altair figure…")

    df = pd.DataFrame(results)

    base = alt.Chart(df).encode(
        x=alt.X(
            "cores:Q",
            title="Number of cores",
            axis=alt.Axis(tickMinStep=1),
        )
    )

    PLOT_SIZE = 300

    cpu_eff = base.mark_line(point=True).encode(
        y=alt.Y(
            "cpu_eff_percent:Q",
            title="CPU efficiency (%)",
            scale=alt.Scale(domain=[0, 100]),
        )
    ).properties(
        title="CPU efficiency",
        height=PLOT_SIZE,
        width="container",
    )

    diis = base.mark_line(
        point=True,
        invalid="filter",
    ).encode(
        y=alt.Y(
            "diis_mean_s:Q",
            title="Mean DIIS iteration time (s)",
        )
    ).properties(
        title="Mean DIIS iteration time",
        height=PLOT_SIZE,
        width="container",
    )

    soscf = base.mark_line(
        point=True,
        invalid="filter",
    ).encode(
        y=alt.Y(
            "soscf_mean_s:Q",
            title="Mean SOSCF iteration time (s)",
        )
    ).properties(
        title="Mean SOSCF iteration time",
        height=PLOT_SIZE,
        width="container",
    )

    geom = base.mark_line(
        point=True,
        invalid="filter",
    ).encode(
        y=alt.Y(
            "geom_iter_mean_s:Q",
            title="Mean geometry iteration time (s)",
        )
    ).properties(
        title="Mean geometry iteration time",
        height=PLOT_SIZE,
        width="container",
    )

    chart = (
        (cpu_eff | diis)
        & (soscf | geom)
    ).properties(
        title="ORCA optimisation benchmarking"
    )

    chart.save("orca_benchmark_results_opt.html")
    print("✅ Plot written to orca_benchmark_results_opt.html")

    # --------------------------------------------------------
    # Optional CSV
    # --------------------------------------------------------
    if args.csv:
        print("\n📄 Writing CSV output…")
        with open("orca_benchmark_results_opt.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=results[0].keys())
            w.writeheader()
            w.writerows(results)

        print("✅ CSV written to orca_benchmark_results_opt.csv")

    print("\n🎉 Done.")

# ------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------
def cli():
    main()
